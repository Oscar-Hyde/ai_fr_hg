# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Permission-aware document ingestion and background processing.

The requesting user is durable processing authority. Source access is checked
before enqueue and checked again by the worker under that same user, so retries
or scheduler execution can never acquire scheduler/Administrator privileges.
"""

from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import socket
import time
import zipfile
from contextlib import contextmanager
from io import BytesIO
from urllib.parse import unquote, urljoin, urlparse

import frappe
from frappe import _
from frappe.utils import cint, now_datetime

from ai_fr_hg.ai.logging import write_audit_log
from ai_fr_hg.ai.exceptions import (
	CorruptDocumentError,
	DocumentFetchError,
	DocumentProcessingError,
	DocumentResourceLimitError,
	DocumentSourcePermissionError,
	UnsupportedDocumentError,
)
from ai_fr_hg.ai.readers import get_reader, supported_extensions
from ai_fr_hg.ai.readers.base import MissingDependency
from ai_fr_hg.utils.network import enforce_local_only, get_allowed_hosts

DEFAULT_MAX_DOCUMENT_MB = 50
MAX_REDIRECTS = 5
MAX_ARCHIVE_MEMBERS = 10_000
ARCHIVE_EXTENSIONS = {"docx", "xlsx", "xlsm", "pptx"}
REDIRECT_STATUSES = {301, 302, 303, 307, 308}
ALLOWED_CONTENT_TYPES = {
	"application/json",
	"application/octet-stream",
	"application/pdf",
	"application/vnd.ms-excel.sheet.macroenabled.12",
	"application/vnd.openxmlformats-officedocument.presentationml.presentation",
	"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
	"application/vnd.openxmlformats-officedocument.wordprocessingml.document",
	"application/xml",
	"image/bmp",
	"image/gif",
	"image/jpeg",
	"image/png",
	"image/tiff",
	"image/webp",
	"message/rfc822",
}
MIME_EXTENSIONS = {
	"application/json": ".json",
	"application/pdf": ".pdf",
	"application/vnd.ms-excel.sheet.macroenabled.12": ".xlsm",
	"application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
	"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
	"application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
	"application/xml": ".xml",
	"image/bmp": ".bmp",
	"image/gif": ".gif",
	"image/jpeg": ".jpg",
	"image/png": ".png",
	"image/tiff": ".tiff",
	"image/webp": ".webp",
	"message/rfc822": ".eml",
	"text/csv": ".csv",
	"text/html": ".html",
	"text/markdown": ".md",
	"text/plain": ".txt",
	"text/tab-separated-values": ".tsv",
}


def _max_document_bytes() -> int:
	configured = cint(frappe.db.get_single_value("AI Platform Settings", "max_document_size_mb"))
	return max(1, configured or DEFAULT_MAX_DOCUMENT_MB) * 1024 * 1024


def _max_retries() -> int:
	configured = frappe.db.get_single_value("AI Platform Settings", "max_retries")
	return 2 if configured in (None, "") else max(0, cint(configured))


def _is_manager(user: str) -> bool:
	if user == "Administrator":
		return True
	return bool(set(frappe.get_roles(user)).intersection({"System Manager", "AI Manager"}))


def _url_ingestion_allowed(user: str) -> bool:
	return _is_manager(user) or bool(
		frappe.db.get_single_value("AI Platform Settings", "allow_user_url_ingestion")
	)


def _assert_valid_authority(user: str | None) -> str:
	user = user or ""
	if not user or user == "Guest" or not frappe.db.exists("User", user):
		raise DocumentSourcePermissionError(_("A valid authenticated processing user is required."))
	if user != "Administrator" and not frappe.db.get_value("User", user, "enabled"):
		raise DocumentSourcePermissionError(_("Processing user {0} is disabled.").format(user))
	return user


@contextmanager
def _as_user(user: str):
	"""Temporarily restore the durable request authority in this worker."""
	previous = frappe.session.user
	if previous != user:
		frappe.set_user(user)
	try:
		yield
	finally:
		if frappe.session.user != previous:
			frappe.set_user(previous)


def _file_doc(file_url: str, file_record: str | None = None, document_name: str | None = None):
	"""Resolve one stable File identity; ambiguous legacy URLs fail closed."""
	name = file_record
	if not name:
		# Legacy rows have only a URL. An exact attachment to the requesting AI
		# Document is stable enough to backfill; otherwise URL/content identity
		# cannot distinguish duplicate File rows and must never select an
		# arbitrary oldest record.
		attached = (
			frappe.get_all(
				"File",
				filters={
					"file_url": file_url,
					"is_folder": 0,
					"attached_to_doctype": "AI Document",
					"attached_to_name": document_name,
				},
				pluck="name",
				order_by="creation asc, name asc",
				limit_page_length=2,
			)
			if document_name
			else []
		)
		if len(attached) > 1:
			raise DocumentFetchError(
				_("More than one File is attached as the source of AI Document {0}.").format(document_name)
			)
		if attached:
			name = attached[0]
		else:
			matches = frappe.get_all(
				"File",
				filters={"file_url": file_url, "is_folder": 0},
				pluck="name",
				order_by="creation asc, name asc",
				limit_page_length=2,
			)
			if len(matches) > 1:
				raise DocumentFetchError(
					_("More than one File record uses {0}; provide the exact File identity.").format(file_url)
				)
			name = matches[0] if matches else None
	if not name:
		raise DocumentFetchError(_("File record not found for {0}.").format(file_url))
	file_doc = frappe.get_doc("File", name)
	if file_doc.file_url != file_url:
		raise DocumentFetchError(_("File record {0} does not match {1}.").format(name, file_url))
	return file_doc


def validate_source_access(document, user: str | None = None) -> None:
	"""Check source authority without reading or fetching source content."""
	user = _assert_valid_authority(user or frappe.session.user)
	source_type = document.source_type

	if source_type == "Text":
		return
	if source_type == "File":
		if not document.source_file:
			raise DocumentFetchError(_("No source file is attached."))
		file_doc = _file_doc(
			document.source_file, document.get("source_file_record"), document.name
		)
		if not frappe.has_permission("File", "read", doc=file_doc, user=user):
			raise DocumentSourcePermissionError(
				_("User {0} cannot read source File {1}.").format(user, file_doc.name)
			)
		return
	if source_type == "DocType Record":
		if not document.source_doctype or not document.source_name:
			raise DocumentFetchError(_("Source DocType and record name are required."))
		if not frappe.db.exists(document.source_doctype, document.source_name):
			raise DocumentFetchError(
				_("Source record {0} {1} does not exist.").format(
					document.source_doctype, document.source_name
				)
			)
		source_doc = frappe.get_doc(document.source_doctype, document.source_name)
		if not frappe.has_permission(
			document.source_doctype,
			"read",
			doc=source_doc,
			user=user,
		):
			raise DocumentSourcePermissionError(
				_("User {0} cannot read source record {1} {2}.").format(
					user, document.source_doctype, document.source_name
				)
			)
		return
	if source_type == "URL":
		if not _url_ingestion_allowed(user):
			raise DocumentSourcePermissionError(
				_("User {0} is not allowed to ingest URL sources.").format(user)
			)
		_validate_fetch_url(document.source_url, user=user)
		return

	raise UnsupportedDocumentError(_("Source type {0} is not supported.").format(source_type))


def enqueue_processing(
	document_name: str,
	requested_by: str | None = None,
	*,
	reset_retries: bool = False,
) -> dict:
	"""Authorize and enqueue one canonical processing job after commit."""
	requested_by = _assert_valid_authority(requested_by or frappe.session.user)
	job_id = f"ai-document::{document_name}"
	lock_key = f"{frappe.local.site}:ai_fr_hg:document-enqueue:{document_name}"

	with frappe.cache.lock(lock_key, timeout=15, blocking_timeout=10):
		with _as_user(requested_by):
			document = frappe.get_doc("AI Document", document_name)
			if not frappe.has_permission("AI Document", "write", doc=document, user=requested_by):
				raise DocumentSourcePermissionError(
					_("User {0} cannot process AI Document {1}.").format(requested_by, document_name)
				)
			validate_source_access(document, requested_by)

			if document.status in {"Extracting", "Chunking", "Embedding"}:
				return {"document": document_name, "status": document.status, "job_id": document.processing_job_id}
			if document.status == "Queued" and document.processing_job_id:
				try:
					from frappe.utils.background_jobs import is_job_enqueued

					job_is_active = is_job_enqueued(document.processing_job_id)
				except Exception:
					# Redis uncertainty must not create a duplicate worker. The scheduled
					# reconciler will retry this check when the queue is available again.
					frappe.log_error(
						title=_("Could not inspect document job {0}").format(document.processing_job_id),
						message=frappe.get_traceback(),
					)
					job_is_active = True
				if job_is_active:
					return {"document": document_name, "status": "Queued", "job_id": document.processing_job_id}
			if document.status in {"Indexed", "Archived"}:
				frappe.throw(
					_("Document {0} must be explicitly reprocessed before it can be queued again.").format(
						document_name
					),
					frappe.ValidationError,
				)

			values = {
				"status": "Queued",
				"processing_requested_by": requested_by,
				"processing_requested_on": now_datetime(),
				"processing_job_id": job_id,
				"error_type": None,
				"error_message": None,
			}
			if reset_retries:
				values["retry_count"] = 0
			frappe.db.set_value("AI Document", document_name, values, update_modified=False)

			queue = frappe.db.get_single_value("AI Platform Settings", "processing_queue") or "long"
			try:
				frappe.enqueue(
					"ai_fr_hg.ai.ingestion.process_document",
					queue=queue,
					timeout=3600,
					job_id=job_id,
					deduplicate=True,
					document_name=document_name,
					requested_by=requested_by,
					enqueue_after_commit=True,
				)
			except Exception as exc:
				frappe.db.set_value(
					"AI Document",
					document_name,
					{"status": "Failed", "error_type": "EnqueueError", "error_message": str(exc)[:2000]},
					update_modified=False,
				)
				raise

			from ai_fr_hg.ai.logging import write_audit_log

			write_audit_log(
				action="Document Processing Queued",
				category="Execution",
				message=_("Document {0} was queued for canonical processing.").format(document_name),
				details={"authority": requested_by, "job_id": job_id, "queue": queue},
				reference_doctype="AI Document",
				reference_name=document_name,
				raise_on_error=True,
			)

	return {"document": document_name, "status": "Queued", "job_id": job_id}


# Backward-compatible public name retained for callers in older integrations.
def enqueue_document_processing(document_name: str) -> dict:
	return enqueue_processing(document_name)


def process_document(
	document_name: str,
	index: bool | None = None,
	requested_by: str | None = None,
) -> dict:
	"""Extract and optionally index a document under persisted authority.

	Normal worker calls require a Queued record and always build the canonical
	chunk index; ``auto_embed_on_ingest`` controls only whether those chunks are
	embedded. ``index=False`` is a compatibility mode for authorized callers
	that need extracted text immediately. It never consumes or replaces an
	already queued indexing job.
	"""
	started = time.monotonic()
	document = frappe.get_doc("AI Document", document_name)
	persisted_authority = document.processing_requested_by
	interactive_extraction = index is False
	authority = (
		(requested_by or frappe.session.user)
		if interactive_extraction
		else (persisted_authority or requested_by or document.owner)
	)

	try:
		authority = _assert_valid_authority(authority)
		if (
			not interactive_extraction
			and requested_by
			and persisted_authority
			and requested_by != persisted_authority
		):
			raise DocumentSourcePermissionError(_("The processing authority does not match the queued request."))

		lock_key = f"{frappe.local.site}:ai_fr_hg:document-process:{document_name}"
		processing_lock = frappe.cache.lock(
			lock_key,
			timeout=3600,
			blocking_timeout=10 if interactive_extraction else 120,
		)
		if not processing_lock.acquire(blocking=True):
			return {
				"document": document_name,
				"status": frappe.db.get_value("AI Document", document_name, "status") or "Unknown",
				"skipped": True,
				"locked": True,
			}
		try:
			with _as_user(authority):
				document.reload()
				starting_status = document.status

				if interactive_extraction:
					if starting_status == "Indexed" and (document.content or "").strip():
						return {
							"document": document_name,
							"status": "Indexed",
							"characters": cint(document.character_count),
							"skipped": True,
						}
					if starting_status not in {"Draft", "Failed", "Queued"}:
						return {"document": document_name, "status": starting_status, "skipped": True}
				elif starting_status != "Queued":
					return {"document": document_name, "status": starting_status, "skipped": True}

				if not frappe.has_permission("AI Document", "write", doc=document, user=authority):
					raise DocumentSourcePermissionError(
						_("User {0} no longer has permission to process {1}.").format(authority, document_name)
					)
				validate_source_access(document, authority)
				document.db_set("status", "Extracting", update_modified=False)
				result, reader, content, filename, mime_type = _extract_source(document, authority)

				document.db_set(
					{
						"content": result.text,
						"reader_used": reader.label,
						"mime_type": mime_type or mimetypes.guess_type(filename)[0],
						"file_size": len(content),
						"checksum": hashlib.sha256(content).hexdigest(),
						"page_count": result.page_count,
						"word_count": result.word_count,
						"character_count": result.character_count,
						"metadata": json.dumps(result.metadata, default=str) if result.metadata else None,
						"error_type": None,
						"error_message": None,
					},
					update_modified=False,
				)

				if interactive_extraction:
					# Preserve Queued so the already submitted worker can build the index.
					# Draft/Failed callers asked only for extraction and remain unindexed.
					restored_status = "Queued" if starting_status == "Queued" else "Draft"
					duration = int((time.monotonic() - started) * 1000)
					document.db_set(
						{"status": restored_status, "processing_duration_ms": duration},
						update_modified=False,
					)
					write_audit_log(
						action="Document Text Extracted",
						reference_doctype="AI Document",
						reference_name=document_name,
						details={
							"authority": authority,
							"duration_ms": duration,
							"reader": reader.label,
							"queued_index_preserved": starting_status == "Queued",
						},
						raise_on_error=True,
					)
					return {
						"document": document_name,
						"status": restored_status,
						"characters": result.character_count,
						"warnings": result.warnings,
					}

				from ai_fr_hg.ai.knowledge import index_document

				embed = (
					bool(frappe.db.get_single_value("AI Platform Settings", "auto_embed_on_ingest"))
					if index is None
					else bool(index)
				)
				index_result = index_document(document_name, embed=embed)
				if frappe.db.get_value("AI Document", document_name, "status") != "Indexed":
					raise CorruptDocumentError(_("Document indexing did not complete successfully."))

				duration = int((time.monotonic() - started) * 1000)
				document.db_set(
					{
						"processing_duration_ms": duration,
						"error_type": None,
						"error_message": None,
					},
					update_modified=False,
				)
				chunks = cint(index_result.get("chunks"))
				write_audit_log(
					action="Document Indexed",
					reference_doctype="AI Document",
					reference_name=document_name,
					details={
						"authority": authority,
						"chunks": chunks,
						"embedded": cint(index_result.get("embedded")),
						"embedding_requested": embed,
						"duration_ms": duration,
						"reader": reader.label,
						"checksum": hashlib.sha256(content).hexdigest(),
					},
					raise_on_error=True,
				)
				frappe.publish_realtime(
					"ai_document_processed",
					{"document": document_name, "status": "Indexed"},
					user=authority,
				)
				return {
					"document": document_name,
					"status": "Indexed",
					"chunks": chunks,
					"characters": result.character_count,
					"warnings": result.warnings,
				}
		finally:
			try:
				processing_lock.release()
			except Exception:
				frappe.log_error(title="AI document processing lock release failed", message=frappe.get_traceback())
	except Exception as exc:
		error_type = exc.__class__.__name__
		duration = int((time.monotonic() - started) * 1000)
		current_retries = cint(frappe.db.get_value("AI Document", document_name, "retry_count"))
		frappe.db.set_value(
			"AI Document",
			document_name,
			{
				"status": "Failed",
				"error_type": error_type,
				"error_message": str(exc)[:2000],
				"processing_duration_ms": duration,
				"retry_count": current_retries + 1,
			},
			update_modified=False,
		)
		frappe.log_error(
			title=_("AI document ingestion failed: {0}").format(document_name),
			message=frappe.get_traceback(),
		)
		write_audit_log(
			action="Document Processing Failed",
			severity="Critical",
			reference_doctype="AI Document",
			reference_name=document_name,
			details={"authority": authority, "error_type": error_type, "error": str(exc)[:1000]},
			raise_on_error=True,
		)
		return {"document": document_name, "status": "Failed", "error_type": error_type, "error": str(exc)}


def _extract_source(document, authority: str):
	"""Run the one reader-registry extraction path and validate its output."""
	content, filename, mime_type = get_source_content(document, authority)
	_validate_size(content)
	_validate_archive(content, filename)

	reader = get_reader(filename)
	if not reader:
		extension = _extension(filename) or _("unknown")
		raise UnsupportedDocumentError(
			_("No document reader is registered for .{0}. Supported extensions: {1}").format(
				extension, ", ".join(supported_extensions())
			)
		)

	try:
		result = reader.read(content, filename)
	except MissingDependency as exc:
		raise UnsupportedDocumentError(str(exc)) from exc
	except DocumentProcessingError:
		raise
	except Exception as exc:
		raise CorruptDocumentError(
			_("The {0} reader could not parse {1}: {2}").format(reader.label, filename, str(exc))
		) from exc

	if not result.text or not result.text.strip():
		raise CorruptDocumentError(
			_("The {0} reader found no readable text in {1}.").format(reader.label, filename)
		)
	if len(result.text) > _max_document_bytes() * 10:
		raise DocumentResourceLimitError(_("Extracted text exceeds the processing character limit."))
	return result, reader, content, filename, mime_type

def get_source_content(document, user: str | None = None) -> tuple[bytes, str, str | None]:
	"""Load source bytes only after explicit source permission validation."""
	user = _assert_valid_authority(user or frappe.session.user)
	validate_source_access(document, user)

	if document.source_type == "File":
		file_doc = _file_doc(
			document.source_file, document.get("source_file_record"), document.name
		)
		content = file_doc.get_content()
		if isinstance(content, str):
			content = content.encode("utf-8")
		return (
			content,
			file_doc.file_name or os.path.basename(document.source_file),
			file_doc.get("content_type") or file_doc.get("file_type"),
		)

	if document.source_type == "Text":
		return (document.content or "").encode("utf-8"), f"{document.name}.txt", "text/plain"

	if document.source_type == "URL":
		return fetch_url_content(document.source_url, user=user)

	if document.source_type == "DocType Record":
		return get_doctype_content(document.source_doctype, document.source_name, user)

	raise UnsupportedDocumentError(_("Source type {0} is not supported.").format(document.source_type))


def get_doctype_content(doctype: str, name: str, user: str | None = None) -> tuple[bytes, str, str]:
	"""Serialize only fields readable by the requesting user."""
	user = _assert_valid_authority(user or frappe.session.user)
	doc = frappe.get_doc(doctype, name)
	if not frappe.has_permission(doctype, "read", doc=doc, user=user):
		raise DocumentSourcePermissionError(
			_("User {0} cannot read source record {1} {2}.").format(user, doctype, name)
		)

	from frappe.model import get_permitted_fields

	permitted = set(get_permitted_fields(doctype, user=user, permission_type="read"))
	readable_types = {
		"Check",
		"Code",
		"Currency",
		"Data",
		"Date",
		"Datetime",
		"Duration",
		"Dynamic Link",
		"Float",
		"Int",
		"Link",
		"Long Text",
		"Percent",
		"Read Only",
		"Select",
		"Small Text",
		"Text",
		"Time",
	}
	lines = [f"{doctype}: {name}"]
	for field in frappe.get_meta(doctype).fields:
		if field.fieldname not in permitted or field.fieldtype not in readable_types:
			continue
		value = doc.get(field.fieldname)
		if value not in (None, ""):
			lines.append(f"{field.label or field.fieldname}: {value}")
	return "\n".join(lines).encode("utf-8"), f"{doctype}-{name}.txt", "text/plain"


def _validate_fetch_url(url: str | None, user: str | None = None) -> None:
	if not url:
		raise DocumentFetchError(_("A source URL is required."))
	try:
		parsed = urlparse(url)
	except ValueError as exc:
		raise DocumentFetchError(_("The source URL is invalid.")) from exc
	if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
		raise DocumentFetchError(_("Only absolute HTTP and HTTPS source URLs are supported."))
	if parsed.username or parsed.password:
		raise DocumentFetchError(_("Source URLs must not contain embedded credentials."))
	user = _assert_valid_authority(user or frappe.session.user)
	if not _is_manager(user) and parsed.hostname.lower() not in get_allowed_hosts():
		raise DocumentFetchError(
			_("Non-manager URL ingestion is limited to hosts in Additional Allowed Hosts.")
		)
	try:
		socket.getaddrinfo(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80))
	except (OSError, UnicodeError, ValueError) as exc:
		raise DocumentFetchError(_("Could not resolve source host {0}.").format(parsed.hostname)) from exc
	try:
		enforce_local_only(url, _("Document source URL"))
	except Exception as exc:
		raise DocumentFetchError(str(exc)) from exc


def _content_type(response) -> str:
	return (response.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()


def _validate_response_headers(response, max_bytes: int) -> str:
	content_type = _content_type(response)
	if content_type and not (content_type.startswith("text/") or content_type in ALLOWED_CONTENT_TYPES):
		raise UnsupportedDocumentError(
			_("URL returned unsupported Content-Type {0}.").format(content_type)
		)
	content_length = response.headers.get("Content-Length")
	if content_length:
		try:
			if int(content_length) > max_bytes:
				raise DocumentResourceLimitError(
					_("URL response is larger than the configured {0} byte limit.").format(max_bytes)
				)
		except ValueError as exc:
			raise DocumentFetchError(_("URL returned an invalid Content-Length header.")) from exc
	return content_type


def _url_filename(url: str, content_type: str) -> str:
	filename = os.path.basename(unquote(urlparse(url).path)).strip() or "download"
	filename = filename.replace("\x00", "")
	if "." not in filename and content_type in MIME_EXTENSIONS:
		filename += MIME_EXTENSIONS[content_type]
	return filename


def fetch_url_content(
	url: str,
	user: str | None = None,
) -> tuple[bytes, str, str | None]:
	"""Fetch a URL with manual redirect validation and bounded streaming."""
	import requests

	user = _assert_valid_authority(user or frappe.session.user)
	max_bytes = _max_document_bytes()
	timeout = min(120, max(5, cint(frappe.db.get_single_value("AI Platform Settings", "request_timeout")) or 30))
	current_url = url
	session = requests.Session()
	session.trust_env = False

	try:
		for redirect_count in range(MAX_REDIRECTS + 1):
			_validate_fetch_url(current_url, user=user)
			try:
				response = session.get(
					current_url,
					headers={
						"Accept": "text/*, application/json, application/pdf, application/xml, image/*, application/octet-stream",
						"User-Agent": "AI-FR-HG-Document-Ingestion/1.0",
					},
					timeout=(min(10, timeout), timeout),
					allow_redirects=False,
					stream=True,
				)
			except requests.RequestException as exc:
				raise DocumentFetchError(_("Could not fetch {0}: {1}").format(current_url, str(exc))) from exc

			try:
				if response.status_code in REDIRECT_STATUSES:
					location = response.headers.get("Location")
					if not location:
						raise DocumentFetchError(_("URL redirect did not include a Location header."))
					if redirect_count >= MAX_REDIRECTS:
						raise DocumentFetchError(
							_("URL exceeded the maximum of {0} redirects.").format(MAX_REDIRECTS)
						)
					current_url = urljoin(current_url, location)
					continue

				try:
					response.raise_for_status()
				except requests.HTTPError as exc:
					raise DocumentFetchError(
						_("URL returned HTTP {0}.").format(response.status_code)
					) from exc

				content_type = _validate_response_headers(response, max_bytes)
				buffer = bytearray()
				try:
					for chunk in response.iter_content(chunk_size=64 * 1024):
						if not chunk:
							continue
						buffer.extend(chunk)
						if len(buffer) > max_bytes:
							raise DocumentResourceLimitError(
								_("URL response exceeded the configured {0} byte limit.").format(max_bytes)
							)
				except requests.RequestException as exc:
					raise DocumentFetchError(_("URL response could not be read: {0}").format(str(exc))) from exc
				if not buffer:
					raise CorruptDocumentError(_("URL returned an empty document."))
				return bytes(buffer), _url_filename(current_url, content_type), content_type or None
			finally:
				response.close()
	finally:
		session.close()

	raise DocumentFetchError(_("URL fetch ended without a document."))


def _extension(filename: str) -> str:
	return filename.rsplit(".", 1)[-1].lower() if "." in filename else ""


def _validate_size(content: bytes) -> None:
	max_bytes = _max_document_bytes()
	if len(content) > max_bytes:
		raise DocumentResourceLimitError(
			_("Document is {0} bytes; the configured limit is {1} bytes.").format(len(content), max_bytes)
		)
	if not content:
		raise CorruptDocumentError(_("Document source is empty."))


def _validate_archive(content: bytes, filename: str) -> None:
	"""Reject corrupt/encrypted Office containers and bounded zip bombs."""
	if _extension(filename) not in ARCHIVE_EXTENSIONS:
		return
	try:
		with zipfile.ZipFile(BytesIO(content)) as archive:
			members = archive.infolist()
			if len(members) > MAX_ARCHIVE_MEMBERS:
				raise DocumentResourceLimitError(
					_("Office document contains too many archive members ({0}).").format(len(members))
				)
			uncompressed = sum(member.file_size for member in members)
			if uncompressed > _max_document_bytes() * 10:
				raise DocumentResourceLimitError(
					_("Office document expands beyond the configured processing limit.")
				)
			if any(member.flag_bits & 0x1 for member in members):
				raise CorruptDocumentError(_("Encrypted Office documents are not supported."))
	except (zipfile.BadZipFile, zipfile.LargeZipFile) as exc:
		raise CorruptDocumentError(_("The Office document container is corrupt.")) from exc


def _reconcile_file_privacy_from_url(file_doc) -> None:
	"""Keep File privacy metadata consistent with Frappe's canonical URL path.

	Frappe stores public and private files in different directories and derives
	the on-disk path from ``is_private``.  A legacy direct update can leave the
	flag out of sync with an otherwise valid native upload URL; reconcile only
	the two canonical local URL forms before Frappe reads the file.  Remote and
	unknown URLs are intentionally left to Frappe's own validation.
	"""
	file_url = file_doc.file_url or ""
	if file_url.startswith("/private/files/"):
		expected_private = 1
	elif file_url.startswith("/files/"):
		expected_private = 0
	else:
		return

	if cint(file_doc.is_private) != expected_private:
		frappe.db.set_value("File", file_doc.name, "is_private", expected_private, update_modified=False)
		file_doc.is_private = expected_private
		frappe.clear_document_cache("File", file_doc.name)


def get_file_content(
	file_url: str,
	user: str | None = None,
	file_record: str | None = None,
) -> tuple[bytes, str]:
	"""Read an exact Frappe File identity only when the user can read it."""
	user = _assert_valid_authority(user or frappe.session.user)
	file_doc = _file_doc(file_url, file_record)
	_reconcile_file_privacy_from_url(file_doc)
	if not frappe.has_permission("File", "read", doc=file_doc, user=user):
		raise DocumentSourcePermissionError(
			_("User {0} cannot read source File {1}.").format(user, file_doc.name)
		)
	content = file_doc.get_content()
	if isinstance(content, str):
		content = content.encode("utf-8")
	_validate_size(content)
	return content, file_doc.file_name or os.path.basename(file_url)


def process_document_now(
	document_name: str,
	requested_by: str | None = None,
	*,
	embed: bool | None = None,
) -> dict:
	"""Synchronously run the canonical processor without submitting an RQ job."""
	authority = _assert_valid_authority(requested_by or frappe.session.user)
	with _as_user(authority):
		document = frappe.get_doc("AI Document", document_name)
		if not frappe.has_permission("AI Document", "write", doc=document, user=authority):
			raise DocumentSourcePermissionError(
				_("User {0} cannot process AI Document {1}.").format(authority, document_name)
			)
		validate_source_access(document, authority)
		if document.status == "Indexed":
			if embed is True:
				from ai_fr_hg.ai.knowledge import index_document

				result = index_document(document_name, embed=True)
				return {"document": document_name, "status": "Indexed", **result}
			return {"document": document_name, "status": "Indexed", "skipped": True}
		if document.status in {"Extracting", "Chunking", "Embedding"}:
			return {"document": document_name, "status": document.status, "skipped": True}
		if document.status == "Archived":
			frappe.throw(_("Archived documents cannot be processed."), frappe.ValidationError)
		if (
			document.status == "Queued"
			and document.processing_requested_by
			and document.processing_requested_by != authority
		):
			raise DocumentSourcePermissionError(
				_("Document {0} is queued under a different processing authority.").format(document_name)
			)

		frappe.db.set_value(
			"AI Document",
			document_name,
			{
				"status": "Queued",
				"processing_requested_by": authority,
				"processing_requested_on": now_datetime(),
				"processing_job_id": f"inline::{document_name}",
				"error_type": None,
				"error_message": None,
			},
			update_modified=False,
		)
	return process_document(document_name, index=embed, requested_by=authority)


def ingest_file(
	file_url: str,
	knowledge_base: str,
	title: str | None = None,
	extraction_schema: str | None = None,
	enqueue_job: bool = True,
	folder: str | None = None,
	file_record: str | None = None,
) -> str:
	"""Create an authorized AI Document from a Frappe File.

	Folder provenance is preserved via the canonical folder service (§7).
	If ``folder`` is not supplied, the File's current folder is used.
	"""
	from ai_fr_hg.ai.governance import check_capability, check_document_quota

	authority = _assert_valid_authority(frappe.session.user)
	check_capability("document_upload")
	check_document_quota()

	# A URL is content identity, not stable File identity. The central resolver
	# rejects ambiguous legacy URL-only requests instead of selecting another
	# document's established File row.
	file_doc = _file_doc(file_url, file_record)
	resolved_file_record = file_doc.name
	_, filename = get_file_content(file_url, authority, resolved_file_record)

	from ai_fr_hg.ai.folders import (
		_assert_folder_exists,
		_normalize_folder_path,
		assign_file_to_folder,
		get_default_folder,
	)
	if folder:
		resolved_folder = _assert_folder_exists(_normalize_folder_path(folder))
	elif file_doc.folder and frappe.db.exists("File", file_doc.folder):
		resolved_folder = file_doc.folder
	else:
		resolved_folder = get_default_folder(user=authority)

	# The physical File and AI Document parent change atomically in the caller's
	# transaction. Fail rather than creating a document with stale provenance.
	if file_doc.folder != resolved_folder:
		assign_file_to_folder(file_doc.name, resolved_folder, user=authority)

	document = frappe.new_doc("AI Document")
	document.update(
		{
			"title": title or filename,
			"knowledge_base": knowledge_base,
			"source_type": "File",
			"source_file": file_url,
			"source_file_record": resolved_file_record,
			"extraction_schema": extraction_schema,
			"status": "Draft",
			"folder": resolved_folder,
			"source_folder": resolved_folder,
		}
	)
	document.insert()
	if enqueue_job:
		enqueue_processing(document.name, requested_by=authority)
	else:
		process_document_now(document.name, requested_by=authority)
	return document.name


def ingest_text(
	text: str,
	knowledge_base: str,
	title: str,
	enqueue_job: bool = True,
) -> str:
	"""Create an authorized AI Document from bounded inline text."""
	from ai_fr_hg.ai.governance import check_capability, check_document_quota

	authority = _assert_valid_authority(frappe.session.user)
	check_capability("document_upload")
	check_document_quota()
	_validate_size((text or "").encode("utf-8"))

	document = frappe.new_doc("AI Document")
	document.update(
		{
			"title": title,
			"knowledge_base": knowledge_base,
			"source_type": "Text",
			"content": text,
			"status": "Draft",
		}
	)
	document.insert()
	if enqueue_job:
		enqueue_processing(document.name, requested_by=authority)
	else:
		process_document_now(document.name, requested_by=authority)
	return document.name


DEFAULT_WAIT_SECONDS = 8.0
POLL_INTERVAL = 0.4
MAX_INLINE_CONTEXT_CHARS = 8000


def prepare_documents_for_turn(document_names: list[str]) -> tuple[list[str], str]:
	"""Make uploaded documents usable this turn without a long poll.

	Indexed records stay in the retrieval scope. Documents that already have
	extracted text — or that can be extracted inline — become extra prompt
	context so the model can answer immediately instead of waiting for
	embeddings. A short wait is used only when nothing readable is available.
	"""
	names = list(dict.fromkeys(name for name in (document_names or []) if name))
	if not names:
		return [], ""

	indexed: list[str] = []
	excerpts: list[str] = []
	pending: list[str] = []

	for name in names:
		doc = frappe.get_doc("AI Document", name)
		if not frappe.has_permission("AI Document", "read", doc=doc, user=frappe.session.user):
			raise DocumentSourcePermissionError(
				_("User {0} cannot read AI Document {1}.").format(frappe.session.user, name)
			)
		status = doc.status
		content = (doc.content or "").strip()
		title = doc.title or name
		if status == "Indexed":
			indexed.append(name)
			continue
		if not content and status in {"Draft", "Failed", "Queued"}:
			try:
				process_document(name, index=False)
				content = (frappe.db.get_value("AI Document", name, "content") or "").strip()
				title = frappe.db.get_value("AI Document", name, "title") or title
			except Exception:
				frappe.log_error(
					title=_("Inline extraction for chat failed: {0}").format(name),
					message=frappe.get_traceback(),
				)
		if content:
			excerpts.append(f"[{title}]\n{content[:MAX_INLINE_CONTEXT_CHARS]}")
		else:
			pending.append(name)

	if pending:
		wait_for_indexed(pending, timeout=DEFAULT_WAIT_SECONDS)
		for name in pending:
			status = frappe.db.get_value("AI Document", name, "status") or "Unknown"
			if status == "Indexed":
				indexed.append(name)
				continue
			content = (frappe.db.get_value("AI Document", name, "content") or "").strip()
			title = frappe.db.get_value("AI Document", name, "title") or name
			if content:
				excerpts.append(f"[{title}]\n{content[:MAX_INLINE_CONTEXT_CHARS]}")

	return indexed, "\n\n".join(excerpts)


def wait_for_indexed(document_names: list[str], timeout: float | None = None) -> dict[str, str]:
	"""Wait for authorized documents to reach Indexed or Failed, within a deadline."""
	from ai_fr_hg.ai.deadline import DEFAULT_RESERVE_SECONDS, remaining_seconds

	names = list(dict.fromkeys(name for name in (document_names or []) if name))
	if not names:
		return {}
	for name in names:
		doc = frappe.get_doc("AI Document", name)
		if not frappe.has_permission("AI Document", "read", doc=doc, user=frappe.session.user):
			raise DocumentSourcePermissionError(
				_("User {0} cannot read AI Document {1}.").format(frappe.session.user, name)
			)

	if timeout is None:
		remaining = remaining_seconds()
		timeout = (
			DEFAULT_WAIT_SECONDS
			if remaining is None
			else max(remaining - DEFAULT_RESERVE_SECONDS, 0.5)
		)
	hard_deadline = time.monotonic() + max(float(timeout), 0.0)
	statuses: dict[str, str] = {}
	while True:
		statuses = {
			name: frappe.db.get_value("AI Document", name, "status") or "Unknown"
			for name in names
		}
		if all(status in {"Indexed", "Failed"} for status in statuses.values()):
			return statuses
		if time.monotonic() >= hard_deadline:
			return statuses
		time.sleep(POLL_INTERVAL)


def process_pending_documents() -> None:
	"""Reconcile stale queue records and bounded retries as original users."""
	max_retries = _max_retries()
	fields = ["name", "owner", "processing_requested_by"]
	failed = frappe.get_all(
		"AI Document",
		filters=[
			["status", "=", "Failed"],
			["retry_count", "<=", max_retries],
			["modified", "<", frappe.utils.add_to_date(now_datetime(), minutes=-5)],
		],
		fields=fields,
		limit_page_length=20,
	)
	remaining = max(20 - len(failed), 0)
	stale_queued = (
		frappe.get_all(
			"AI Document",
			filters=[
				["status", "=", "Queued"],
				["modified", "<", frappe.utils.add_to_date(now_datetime(), hours=-2)],
			],
			fields=fields,
			limit_page_length=remaining,
		)
		if remaining
		else []
	)

	for row in [*failed, *stale_queued]:
		authority = row.processing_requested_by or row.owner
		try:
			enqueue_processing(row.name, requested_by=authority)
		except Exception:
			frappe.log_error(
				title=_("Could not reconcile AI document {0}").format(row.name),
				message=frappe.get_traceback(),
			)
