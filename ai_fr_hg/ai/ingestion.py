# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Unified document ingestion pipeline.

Every source - an uploaded file, pasted text, a URL or an existing Frappe
record - converges on the same path: read -> extract text -> chunk -> embed ->
index. That single pipeline is what makes the platform's document handling
predictable and auditable.
"""

import hashlib
import time

import frappe
from frappe import _
from frappe.utils import cint, get_files_path, now_datetime

from ai_fr_hg.ai.exceptions import DocumentProcessingError
from ai_fr_hg.ai.readers import get_reader, supported_extensions
from ai_fr_hg.ai.readers.base import MissingDependency


def get_file_content(file_url: str) -> tuple[bytes, str]:
	"""Load the bytes and filename of a Frappe `File` by its URL."""
	if not file_url:
		frappe.throw(_("No file specified."))

	name = frappe.db.get_value("File", {"file_url": file_url}, "name")
	if not name:
		frappe.throw(_("File {0} was not found.").format(file_url))

	file_doc = frappe.get_doc("File", name)
	return file_doc.get_content(encodings=[]), file_doc.file_name


def process_document(document: str, index: bool | None = None) -> dict:
	"""Extract text from a document's source and optionally index it."""
	from ai_fr_hg.ai.knowledge import index_document

	doc = frappe.get_doc("AI Document", document)
	settings = frappe.get_cached_doc("AI Platform Settings")
	started = time.monotonic()

	doc.db_set("status", "Extracting", update_modified=False)
	doc.db_set("error_message", None, update_modified=False)

	try:
		result = extract_source_text(doc, settings)
	except MissingDependency as exc:
		_fail(doc, str(exc))
		return {"document": document, "status": "Failed", "error": str(exc)}
	except Exception as exc:
		_fail(doc, str(exc))
		frappe.log_error(title=f"AI ingestion failed: {document}", message=frappe.get_traceback())
		return {"document": document, "status": "Failed", "error": str(exc)}

	text = result.text or ""
	if not text.strip():
		message = "; ".join(result.warnings) or "No text could be extracted from this source."
		_fail(doc, message)
		return {"document": document, "status": "Failed", "error": message}

	doc.db_set(
		{
			"content": text,
			"character_count": len(text),
			"word_count": len(text.split()),
			"page_count": cint(result.page_count),
			"checksum": hashlib.sha256(text.encode("utf-8")).hexdigest()[:32],
			"metadata": frappe.as_json(result.metadata),
			"reader_used": result.metadata.get("reader"),
			"processing_duration_ms": int((time.monotonic() - started) * 1000),
		},
		update_modified=False,
	)

	should_index = settings.auto_embed_on_ingest if index is None else index
	if should_index:
		try:
			index_document(document)
		except Exception as exc:
			_fail(doc, f"Text extracted, but indexing failed: {exc}")
			frappe.log_error(title=f"AI indexing failed: {document}", message=frappe.get_traceback())
			return {"document": document, "status": "Failed", "error": str(exc)}
		status = "Indexed"
	elif index is False:
		# An interactive caller extracted the text inline to answer a question
		# and deliberately skipped embedding. The document is readable but not
		# yet searchable, so hand the indexing to a worker rather than
		# reporting a completeness the record does not have.
		enqueue_processing(document)
		status = "Queued"
	else:
		# Embedding is disabled platform-wide; extraction is all there is to do.
		doc.db_set("status", "Indexed", update_modified=False)
		status = "Indexed"

	frappe.publish_realtime(
		"ai_document_processed",
		{"document": document, "status": status},
		user=doc.owner,
	)

	return {
		"document": document,
		"status": status,
		"characters": len(text),
		"warnings": result.warnings,
	}


def extract_source_text(doc, settings):
	"""Dispatch to the right extraction strategy for the document's source."""
	from ai_fr_hg.ai.readers.base import ReadResult

	source_type = doc.source_type or "File"

	if source_type == "Text":
		if not doc.content:
			raise DocumentProcessingError(_("This document has no text content."))
		return ReadResult(
			text=doc.content, page_count=1, metadata={"format": "text", "reader": "Inline Text"}
		)

	if source_type == "File":
		return _read_file(doc, settings)

	if source_type == "URL":
		return _read_url(doc, settings)

	if source_type == "DocType Record":
		return _read_record(doc)

	raise DocumentProcessingError(_("Unsupported source type {0}.").format(source_type))


def _read_file(doc, settings):
	if not doc.source_file:
		raise DocumentProcessingError(_("No source file is attached."))

	content, filename = get_file_content(doc.source_file)

	max_bytes = cint(settings.max_document_size_mb) * 1024 * 1024
	if max_bytes and len(content) > max_bytes:
		raise DocumentProcessingError(
			_("File is {0} MB, which exceeds the {1} MB limit.").format(
				round(len(content) / 1024 / 1024, 1), settings.max_document_size_mb
			)
		)

	reader = get_reader(filename)
	if not reader:
		raise DocumentProcessingError(
			_("No reader is registered for this file type. Supported: {0}").format(
				", ".join(supported_extensions())
			)
		)

	result = reader.read(content, filename)
	result.metadata.setdefault("reader", reader.label)
	result.metadata.setdefault("filename", filename)

	doc.db_set("file_size", len(content), update_modified=False)
	if not doc.document_type:
		doc.db_set("document_type", reader.label, update_modified=False)
	if mime := _guess_mime(filename):
		doc.db_set("mime_type", mime, update_modified=False)

	return result


def _read_url(doc, settings):
	"""Fetch a URL, honouring strict local-only mode."""
	import requests

	from ai_fr_hg.utils.network import enforce_local_only

	url = doc.source_url
	enforce_local_only(url, _("Document source URL"))

	response = requests.get(url, timeout=cint(settings.request_timeout) or 60)
	response.raise_for_status()

	filename = url.rsplit("/", 1)[-1] or "index.html"
	if "." not in filename:
		filename += ".html"

	reader = get_reader(filename) or get_reader("page.html")
	result = reader.read(response.content, filename)
	result.metadata.setdefault("reader", reader.label)
	result.metadata["source_url"] = url
	return result


def _read_record(doc):
	"""Render an existing Frappe document into indexable text."""
	from ai_fr_hg.ai.readers.base import ReadResult

	if not doc.source_doctype or not doc.source_name:
		raise DocumentProcessingError(_("Source DocType and name are required."))

	frappe.has_permission(doc.source_doctype, "read", doc=doc.source_name, throw=True)
	source = frappe.get_doc(doc.source_doctype, doc.source_name)
	meta = frappe.get_meta(doc.source_doctype)

	lines = [f"# {doc.source_doctype}: {doc.source_name}"]
	for field in meta.fields:
		if field.fieldtype in ("Section Break", "Column Break", "Tab Break", "Button", "HTML"):
			continue
		value = source.get(field.fieldname)
		if value in (None, "", []):
			continue
		if field.fieldtype in ("Table", "Table MultiSelect"):
			lines.append(f"\n## {field.label or field.fieldname}")
			for row in value:
				cells = [
					f"{df.label or df.fieldname}: {row.get(df.fieldname)}"
					for df in frappe.get_meta(field.options).fields
					if row.get(df.fieldname) not in (None, "", [])
					and df.fieldtype not in ("Section Break", "Column Break")
				]
				if cells:
					lines.append(" | ".join(cells))
		else:
			lines.append(f"{field.label or field.fieldname}: {value}")

	return ReadResult(
		text="\n".join(lines),
		page_count=1,
		metadata={
			"format": "doctype",
			"reader": "DocType Record",
			"doctype": doc.source_doctype,
			"name": doc.source_name,
		},
	)


def _guess_mime(filename: str) -> str | None:
	import mimetypes

	return mimetypes.guess_type(filename)[0]


def _fail(doc, message: str) -> None:
	doc.db_set(
		{"status": "Failed", "error_message": (message or "")[:1000]},
		update_modified=False,
	)


def ingest_file(
	file_url: str,
	knowledge_base: str,
	title: str | None = None,
	extraction_schema: str | None = None,
	enqueue_job: bool = True,
) -> str:
	"""Create an `AI Document` from an uploaded file and start processing."""
	from ai_fr_hg.ai.governance import check_capability, check_document_quota

	check_capability("document_upload")
	check_document_quota()

	_, filename = get_file_content(file_url)

	doc = frappe.new_doc("AI Document")
	doc.update(
		{
			"title": title or filename,
			"knowledge_base": knowledge_base,
			"source_type": "File",
			"source_file": file_url,
			"extraction_schema": extraction_schema,
			"status": "Queued",
		}
	)
	doc.insert()

	if enqueue_job:
		enqueue_processing(doc.name)
	else:
		process_document(doc.name)
	return doc.name


def ingest_text(
	text: str,
	knowledge_base: str,
	title: str,
	enqueue_job: bool = True,
) -> str:
	"""Create an `AI Document` from raw text and start processing."""
	from ai_fr_hg.ai.governance import check_capability, check_document_quota

	check_capability("document_upload")
	check_document_quota()

	doc = frappe.new_doc("AI Document")
	doc.update(
		{
			"title": title,
			"knowledge_base": knowledge_base,
			"source_type": "Text",
			"content": text,
			"status": "Queued",
		}
	)
	doc.insert()

	if enqueue_job:
		enqueue_processing(doc.name)
	else:
		process_document(doc.name)
	return doc.name


def enqueue_processing(document: str) -> None:
	"""Queue document processing on a background worker."""
	queue = frappe.db.get_single_value("AI Platform Settings", "processing_queue") or "long"

	frappe.enqueue(
		"ai_fr_hg.ai.ingestion.process_document",
		queue=queue,
		timeout=3600,
		job_id=f"ai_process_{document}",
		deduplicate=True,
		document=document,
		enqueue_after_commit=True,
	)
	frappe.db.set_value("AI Document", document, "status", "Queued", update_modified=False)
