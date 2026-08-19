# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Canonical AI Document tree service.

The tree is a mixed projection of two existing authoritative models:

* native ``File`` records with ``is_folder = 1`` provide the recursive parent
  hierarchy through ``File.folder``;
* ``AI Document.folder`` places stable document identities at a location.

This is intentionally not a parallel browser, folder table, or hand-maintained
Nested Set.  Frappe's native ``is_tree`` / NestedSet mechanism assumes a single
homogeneous DocType per tree.  This view mixes ``File`` folders with
``AI Document`` nodes, so NestedSet cannot own the projection.  File remains
the NestedSet authority for physical folders; this service is the mixed-type
facade.  Frappe's native Tree View owns rendering and lazy expansion
(``ai_knowledge/doctype/ai_document/ai_document_tree.js``) while this module
owns permission-filtered reads and transactional mutations.  AI Document
processing, chunks, retrieval, and deletion hooks remain unchanged.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import frappe
from frappe import _
from frappe.utils import cint, get_datetime, now_datetime

from ai_fr_hg.ai.exceptions import (
	CircularFolderError,
	DocumentFetchError,
	FolderAlreadyExistsError,
	FolderNotEmptyError,
	FolderPermissionError,
	InvalidFolderNameError,
)
from ai_fr_hg.ai.folders import (
	_HOME,
	_assert_folder_exists,
	_check_permission,
	_check_write_access,
	_clean_name,
	_get_folder_doc,
	_normalize_folder_path,
)
from ai_fr_hg.ai.logging import write_audit_log
from ai_fr_hg.ai.organization import organization_name_key
from ai_fr_hg.utils.file_hooks import suppress_ai_document_ingestion

ROOT_LABEL = "AI Documents"
DEFAULT_PAGE_LENGTH = 100
MAX_PAGE_LENGTH = 250
BACKGROUND_THRESHOLD = 100
_SEARCH_DOCUMENT_SCAN_LIMIT = 1000
_SEARCH_DOCUMENT_SCAN_PAGE_LENGTH = 250
_PAGE_PREFIX = "__ai_document_page__:"
_MAX_ORGANIZATION_NAME_LENGTH = 140
_COPY_SUFFIX = re.compile(r"^(?P<stem>.*?)(?: \(Copy(?: (?P<number>\d+))?\))?(?P<ext>\.[^.]+)?$")


# ---------------------------------------------------------------------------
# Pure identity and collision helpers
# ---------------------------------------------------------------------------


def split_node_value(value: str) -> tuple[str, str]:
	"""Return ``(node_type, canonical_name)`` for a mixed tree node."""
	value = str(value or "")
	if value == ROOT_LABEL:
		return "root", _HOME
	if value.startswith("document::"):
		return "document", value.removeprefix("document::")
	if value.startswith(_PAGE_PREFIX):
		return "page", value.removeprefix(_PAGE_PREFIX)
	return "folder", _normalize_folder_path(value)


def document_node_value(name: str) -> str:
	return f"document::{name}"


def copy_name_candidates(name: str) -> Iterator[str]:
	"""Yield deterministic, extension-aware names for explicit copy collisions."""
	name = str(name or "Document").strip() or "Document"
	match = _COPY_SUFFIX.match(name)
	stem = (match.group("stem") if match else name) or "Document"
	ext = (match.group("ext") if match else "") or ""
	number = 1
	while True:
		suffix = " (Copy)" if number == 1 else f" (Copy {number})"
		available = _MAX_ORGANIZATION_NAME_LENGTH - len(suffix) - len(ext)
		if available < 1:
			frappe.throw(_("The file extension leaves no room for a copy name."), frappe.ValidationError)
		yield f"{stem[:available]}{suffix}{ext}"
		number += 1


def _encode_page(parent: str, kind: str, position: int | str) -> str:
	payload = json.dumps(
		{"parent": parent, "kind": kind, "position": position},
		separators=(",", ":"),
	).encode()
	return _PAGE_PREFIX + base64.urlsafe_b64encode(payload).decode().rstrip("=")


def _decode_page(payload: str) -> tuple[str, str, int | str]:
	try:
		padding = "=" * (-len(payload) % 4)
		data = json.loads(base64.urlsafe_b64decode(payload + padding))
		kind = str(data["kind"])
		position = data["position"]
		if kind in {"folder", "document"}:
			if isinstance(position, bool):
				raise ValueError
			position = int(position)
			if position < 0 or position > 10_000_000:
				raise ValueError
		elif kind == "document_after":
			if not isinstance(position, str) or len(position) > 255:
				raise ValueError
		else:
			raise ValueError
		return str(data["parent"]), kind, position
	except (binascii.Error, KeyError, TypeError, ValueError, json.JSONDecodeError):
		frappe.throw(_("Invalid tree continuation token."), frappe.ValidationError)


# ---------------------------------------------------------------------------
# Transaction, locking, permission, and audit helpers
# ---------------------------------------------------------------------------


@contextmanager
def _atomic(operation: str):
	"""Rollback every database mutation and audit row when an operation fails."""
	savepoint = f"ai_document_tree_{frappe.scrub(operation)}_{frappe.generate_hash(length=8)}"
	frappe.db.savepoint(savepoint)
	try:
		yield
	except Exception:
		frappe.db.rollback(save_point=savepoint)
		raise
	else:
		release = getattr(frappe.db, "release_savepoint", None)
		if release:
			release(savepoint)


def _audit(action: str, message: str, *, details: dict | None = None, reference: str | None = None) -> None:
	write_audit_log(
		action=action,
		category="Data",
		message=message,
		details=details,
		reference_doctype=(
			"File"
			if reference and (reference == _HOME or reference.startswith(f"{_HOME}/"))
			else "AI Document"
		),
		reference_name=reference,
		raise_on_error=True,
	)


def _lock(doctype: str, name: str, expected_modified: str | None = None) -> Any:
	if doctype not in {"AI Document", "File"}:
		raise ValueError("Unsupported lock DocType")
	# SQL values remain parameterized; only this two-value allowlisted identifier is interpolated.
	rows = frappe.db.sql(  # nosemgrep
		f"select modified from `tab{doctype}` where name=%s for update",
		(name,),
		as_dict=True,
	)
	if not rows:
		frappe.throw(_("The selected item no longer exists."), frappe.DoesNotExistError)
	actual = rows[0].modified
	if expected_modified and get_datetime(expected_modified) != get_datetime(actual):
		frappe.throw(
			_("This item changed after the tree was loaded. Refresh and try again."),
			frappe.TimestampMismatchError,
		)
	return actual


def _lock_names(doctype: str, names: list[str]) -> None:
	"""Lock a stable snapshot in deterministic batches for recursive mutation."""
	if doctype not in {"AI Document", "File"}:
		raise ValueError("Unsupported lock DocType")
	for batch in _chunks(sorted({name for name in names if name})):
		placeholders = ", ".join(["%s"] * len(batch))
		# The table is allowlisted above and placeholder count derives only from this bounded batch.
		rows = frappe.db.sql(  # nosemgrep
			f"select name from `tab{doctype}` where name in ({placeholders}) order by name for update",
			tuple(batch),
		)
		if len(rows) != len(batch):
			frappe.throw(
				_("The selected subtree changed. Refresh and try again."), frappe.TimestampMismatchError
			)


def _lock_subtree(folders: list[str], documents: list, files: list | None = None) -> None:
	# Parent membership rows are always locked before their children. Canonical
	# direct writers follow the same order, preventing folder/File inversions.
	_lock_names("File", folders)
	_lock_names("File", [row.name for row in (files or [])])
	_lock_names("AI Document", [row.name for row in documents])


def _document(name: str, permission: str = "read"):
	doc = frappe.get_doc("AI Document", name)
	doc.check_permission(permission)
	return doc


def _folder(name: str, permission: str = "read"):
	name = _assert_folder_exists(name)
	doc = _get_folder_doc(name)
	_check_permission("File", permission, doc=doc, user=frappe.session.user)
	return doc


def _can_folder(name: str, permission: str) -> bool:
	try:
		doc = _get_folder_doc(name)
		return bool(frappe.has_permission("File", permission, doc=doc, user=frappe.session.user))
	except Exception:
		return False


def _chunks(values: list[Any], size: int = 400) -> Iterator[list[Any]]:
	for index in range(0, len(values), size):
		yield values[index : index + size]


def _rows_by_name(doctype: str, names: list[str], fields: list[str]) -> list:
	"""Fetch named rows in bounded queries without weakening permissions."""
	if doctype not in {"AI Document", "File"}:
		raise ValueError("Unsupported row DocType")
	result = []
	for batch in _chunks(sorted({name for name in names if name})):
		result.extend(
			frappe.get_all(
				doctype,
				filters={"name": ["in", batch]},
				fields=fields,
				limit_page_length=0,
			)
		)
	return result


def _progress(current: int, total: int, title: str) -> None:
	publisher = getattr(frappe, "publish_progress", None)
	if publisher and total:
		publisher(min(100, int(current * 100 / total)), title=title)


def _subtree_state(folders: list[str], documents: list, files: list | None = None) -> str:
	"""Fingerprint the complete queued source snapshot without serializing content."""
	file_names = sorted({*folders, *[row.name for row in (files or [])]})
	document_names = sorted({row.name for row in documents})
	file_fields = ["name", "folder", "file_name", "file_url", "modified", "is_folder"]
	document_fields = [
		"name",
		"folder",
		"organization_name_key",
		"organization_revision",
		"source_file",
		"source_file_record",
		"modified",
	]
	file_rows = _rows_by_name("File", file_names, file_fields)
	document_rows = _rows_by_name("AI Document", document_names, document_fields)

	def state_value(row, field: str) -> str:
		value = row.get(field)
		return "" if value is None else str(value)

	payload = {
		# Requested identities are part of the digest so deletion cannot look the
		# same as an originally smaller subtree.
		"file_names": file_names,
		"document_names": document_names,
		"files": sorted(tuple(state_value(row, field) for field in file_fields) for row in file_rows),
		"documents": sorted(
			tuple(state_value(row, field) for field in document_fields) for row in document_rows
		),
	}
	return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _check_subtree_state(
	expected: str | None, folders: list[str], documents: list, files: list | None = None
) -> None:
	if expected and expected != _subtree_state(folders, documents, files):
		frappe.throw(
			_("The selected subtree changed after this operation was queued. Refresh and try again."),
			frappe.TimestampMismatchError,
		)


def _folder_paths(source: str) -> list[str]:
	source = _assert_folder_exists(source)
	prefix = f"{source}/"
	rows = frappe.get_all(
		"File",
		filters={"name": ["like", f"{prefix}%"], "is_folder": 1},
		pluck="name",
		order_by="name asc",
		limit_page_length=0,
	)
	# Folder names may legitimately contain SQL wildcard characters. The query
	# remains an indexed prefix candidate lookup; this exact check prevents `%`
	# or `_` in a canonical path from admitting unrelated sibling branches.
	return [source, *[name for name in rows if name.startswith(prefix)]]


def _documents_in_folders(folders: list[str], fields: list[str] | None = None) -> list:
	result = []
	for batch in _chunks(folders):
		result.extend(
			frappe.get_all(
				"AI Document",
				filters={"folder": ["in", batch]},
				fields=fields or ["name", "folder", "organization_name"],
				limit_page_length=0,
			)
		)
	return result


def _preflight_subtree(source: str, permission: str) -> tuple[list[str], list]:
	folders = _folder_paths(source)
	folder_rows = []
	for batch in _chunks(folders):
		folder_rows.extend(
			frappe.get_all(
				"File",
				filters={"name": ["in", batch], "is_folder": 1},
				fields=[
					"name",
					"file_name",
					"folder",
					"owner",
					"is_private",
					"attached_to_doctype",
					"attached_to_name",
				],
				limit_page_length=0,
			)
		)
	if len(folder_rows) != len(folders) or any(
		not frappe.has_permission(
			"File", permission, doc=_permission_row("File", row), user=frappe.session.user
		)
		for row in folder_rows
	):
		# Do not identify an inaccessible descendant.
		frappe.throw(_("You do not have permission for the complete folder subtree."), FolderPermissionError)
	documents = _documents_in_folders(
		folders,
		fields=[
			"name",
			"folder",
			"organization_name",
			"knowledge_base",
			"owner",
			"source_type",
			"source_file",
			"source_file_record",
		],
	)
	if any(
		not frappe.has_permission(
			"AI Document",
			permission,
			doc=_permission_row("AI Document", row),
			user=frappe.session.user,
		)
		for row in documents
	):
		frappe.throw(_("You do not have permission for the complete folder subtree."), FolderPermissionError)
	return folders, documents


_FILE_PERMISSION_FIELDS = [
	"name",
	"file_name",
	"file_url",
	"folder",
	"owner",
	"is_private",
	"attached_to_doctype",
	"attached_to_name",
]


def _paged_file_rows(filters: dict[str, Any]) -> Iterator:
	"""Stream deterministic File matches with a hard per-query memory bound."""
	offset = 0
	while True:
		rows = frappe.get_all(
			"File",
			filters=filters,
			fields=[*_FILE_PERMISSION_FIELDS, "creation"],
			order_by="creation asc, name asc",
			limit_start=offset,
			limit_page_length=400,
		)
		if not rows:
			return
		yield from rows
		offset += len(rows)


def _source_files_for_documents(documents: list) -> list:
	"""Resolve one authoritative physical File per File-backed document.

	Stable identities are resolved by primary key. Legacy URL-only rows may use
	a unique File attached to the exact AI Document, or a globally unique URL
	match. Ambiguous content identities fail closed. Both exact and fallback
	lookups stream fixed-size pages, so pathological duplicate URLs cannot
	produce an unbounded query result or an N+1 query per document.
	"""
	file_documents = [row for row in documents if row.source_type == "File"]
	stable_names = [row.source_file_record for row in file_documents if row.source_file_record]
	stable_by_name = {row.name: row for row in _rows_by_name("File", stable_names, _FILE_PERMISSION_FIELDS)}

	legacy_documents = [row for row in file_documents if not row.source_file_record]
	legacy_by_document = {}
	ambiguous_documents = set()
	legacy_fallbacks = {}
	ambiguous_urls = set()
	for document_batch in _chunks(legacy_documents):
		documents_by_name = {row.name: row for row in document_batch if row.source_file}
		urls = sorted({row.source_file for row in documents_by_name.values()})
		if not urls:
			continue

		# Fetch exact attachments separately so duplicate URLs cannot displace a
		# File explicitly attached to the source AI Document.
		for file_row in _paged_file_rows(
			{
				"file_url": ["in", urls],
				"is_folder": 0,
				"attached_to_doctype": "AI Document",
				"attached_to_name": ["in", list(documents_by_name)],
			}
		):
			document = documents_by_name.get(file_row.attached_to_name)
			if document and document.source_file == file_row.file_url:
				if document.name in legacy_by_document:
					ambiguous_documents.add(document.name)
				else:
					legacy_by_document[document.name] = file_row

		fallback_urls = sorted(
			{
				document.source_file
				for document in documents_by_name.values()
				if document.name not in legacy_by_document
			}
		)
		for url_batch in _chunks(fallback_urls):
			for file_row in _paged_file_rows({"file_url": ["in", url_batch], "is_folder": 0}):
				if file_row.file_url in legacy_fallbacks:
					ambiguous_urls.add(file_row.file_url)
				else:
					legacy_fallbacks[file_row.file_url] = file_row

	resolved = {}
	for document in file_documents:
		file_row = None
		if document.source_file_record:
			file_row = stable_by_name.get(document.source_file_record)
			if not file_row:
				raise DocumentFetchError(
					_("File record {0} was not found.").format(document.source_file_record)
				)
			if file_row.file_url != document.source_file:
				raise DocumentFetchError(
					_("File record {0} does not match {1}.").format(
						document.source_file_record, document.source_file
					)
				)
		else:
			if document.name in ambiguous_documents:
				raise DocumentFetchError(
					_("More than one File is attached as the source of AI Document {0}.").format(
						document.name
					)
				)
			file_row = legacy_by_document.get(document.name)
			if not file_row:
				if document.source_file in ambiguous_urls:
					raise DocumentFetchError(
						_(
							"More than one File record uses {0}; backfill the exact File identity first."
						).format(document.source_file)
					)
				file_row = legacy_fallbacks.get(document.source_file)
		if not file_row:
			raise DocumentFetchError(_("File record not found for {0}.").format(document.source_file))
		resolved[file_row.name] = file_row
	return list(resolved.values())


def _preflight_document_copies(documents: list) -> list:
	if any(
		not frappe.has_permission(
			"AI Document",
			"create",
			doc=_permission_row("AI Document", row),
			user=frappe.session.user,
		)
		for row in documents
	):
		frappe.throw(_("You do not have permission to create every document copy."), frappe.PermissionError)

	source_files = _source_files_for_documents(documents)
	for file_row in source_files:
		_check_permission("File", "read", doc=_permission_row("File", file_row), user=frappe.session.user)
	return source_files


def _document_collision(folder: str, organization_name: str, exclude: str | None = None) -> str | None:
	filters: dict[str, Any] = {
		"folder": folder,
		"organization_name_key": organization_name_key(organization_name),
	}
	if exclude:
		filters["name"] = ["!=", exclude]
	return frappe.db.get_value("AI Document", filters, "name")


def resolve_document_name(
	folder: str,
	requested: str,
	*,
	copy_on_collision: bool,
	exclude: str | None = None,
) -> str:
	"""Resolve a validated, location-local name through the core service."""
	folder = _assert_folder_exists(folder)
	requested = _clean_name(requested)
	if not _document_collision(folder, requested, exclude=exclude):
		return requested
	if not copy_on_collision:
		frappe.throw(
			_("A document named {0} already exists in {1}.").format(
				frappe.bold(requested), frappe.bold(folder)
			),
			FolderAlreadyExistsError,
		)
	for candidate in copy_name_candidates(requested):
		if not _document_collision(folder, candidate, exclude=exclude):
			return candidate
	raise AssertionError("unreachable")


def _available_file_name(folder: str, requested: str) -> str:
	from ai_fr_hg.ai.folders import _assert_unique_in_parent

	try:
		_assert_unique_in_parent(requested, folder, is_folder=False)
		return requested
	except FolderAlreadyExistsError:
		for candidate in copy_name_candidates(requested):
			try:
				_assert_unique_in_parent(candidate, folder, is_folder=False)
				return candidate
			except FolderAlreadyExistsError:
				continue
	raise AssertionError("unreachable")


def _available_folder_name(folder: str, requested: str, *, copy_on_collision: bool) -> str:
	from ai_fr_hg.ai.folders import _assert_unique_in_parent

	requested = _clean_name(requested)
	try:
		_assert_unique_in_parent(requested, folder, is_folder=True)
		return requested
	except FolderAlreadyExistsError:
		if not copy_on_collision:
			raise
	for candidate in copy_name_candidates(requested):
		try:
			_assert_unique_in_parent(candidate, folder, is_folder=True)
			return candidate
		except FolderAlreadyExistsError:
			continue
	raise AssertionError("unreachable")


# ---------------------------------------------------------------------------
# Permission-filtered lazy node retrieval
# ---------------------------------------------------------------------------


def _permission_row(doctype: str, row):
	"""Use already-fetched fields for permission hooks instead of an N+1 load."""
	row.doctype = doctype
	return row


def _permission_allowed(doctype: str, permission: str, *, doc=None) -> bool:
	"""Read a permission result for UI hints; mutation services always re-enforce it."""
	# This is intentionally a boolean capability query, not an ignored enforcement call.
	return bool(frappe.has_permission(doctype, permission, doc=doc))  # nosemgrep


def _folder_node(row) -> dict:
	permission_doc = _permission_row("File", row)
	can_write = _permission_allowed("File", "write", doc=permission_doc)
	can_create_folder = _permission_allowed("File", "create") and can_write
	can_create_document = _permission_allowed("AI Document", "create") and can_write
	return {
		"value": row.name,
		"title": row.file_name or row.name.rsplit("/", 1)[-1],
		"expandable": True,
		"node_type": "folder",
		"folder": row.folder or _HOME,
		"modified": str(row.modified),
		"can_read": True,
		"can_write": can_write,
		"can_delete": row.name != _HOME and _permission_allowed("File", "delete", doc=permission_doc),
		"can_copy": _permission_allowed("File", "create"),
		"can_create_folder": can_create_folder,
		"can_create_document": can_create_document,
		"can_create_child": can_create_folder or can_create_document,
	}


def _document_node(row, readable_folders: set[str]) -> dict | None:
	if (row.folder or _HOME) not in readable_folders:
		return None
	permission_doc = _permission_row("AI Document", row)
	return {
		"value": document_node_value(row.name),
		"title": row.organization_name or row.title or row.name,
		"expandable": False,
		"node_type": "document",
		"document": row.name,
		"folder": row.folder or _HOME,
		"status": row.status,
		"knowledge_base": row.knowledge_base,
		"modified": str(row.modified),
		"can_read": True,
		"can_write": _permission_allowed("AI Document", "write", doc=permission_doc),
		"can_copy": _permission_allowed("AI Document", "create", doc=permission_doc),
		"can_delete": _permission_allowed("AI Document", "delete", doc=permission_doc),
	}


def _tree_rows(
	parent: str,
	*,
	knowledge_base: str | None,
	search: str | None,
	cursor_kind: str,
	position: int | str,
	page_length: int,
) -> tuple[list[dict], tuple[str, int | str] | None]:
	"""Merge folder and document pages without ever loading the full prefix."""
	search = (search or "").strip()
	folder_filters: dict[str, Any] = {"is_folder": 1, "name": ["!=", parent]}
	document_filters: dict[str, Any] = {}
	folder_or_filters = None
	document_or_filters = None
	if search:
		folder_or_filters = [["file_name", "like", f"%{search}%"], ["name", "like", f"%{search}%"]]
		document_or_filters = [
			["organization_name", "like", f"%{search}%"],
			["title", "like", f"%{search}%"],
		]
	else:
		folder_filters["folder"] = parent
		document_filters["folder"] = parent
	if knowledge_base:
		document_filters["knowledge_base"] = knowledge_base

	def fetch_folders(start: int, length: int) -> list:
		return frappe.get_list(
			"File",
			filters=folder_filters,
			or_filters=folder_or_filters,
			fields=[
				"name",
				"file_name",
				"folder",
				"modified",
				"owner",
				"is_private",
				"attached_to_doctype",
				"attached_to_name",
			],
			order_by="file_name asc, name asc",
			limit_start=start,
			limit_page_length=length,
		)

	document_fields = [
		"name",
		"title",
		"organization_name",
		"folder",
		"status",
		"knowledge_base",
		"modified",
		"owner",
	]

	def fetch_documents(start: int, length: int) -> tuple[list[dict], int, bool]:
		"""Fetch a normal child page whose parent was already authorized."""
		rows = frappe.get_list(
			"AI Document",
			filters=document_filters,
			fields=document_fields,
			order_by="organization_name asc, name asc",
			limit_start=start,
			limit_page_length=length + 1,
		)
		consumed = min(length, len(rows))
		nodes = [node for row in rows[:length] if (node := _document_node(row, {parent}))]
		return nodes, consumed, len(rows) > length

	def fetch_search_documents(after: str, length: int) -> tuple[list[dict], str | None]:
		"""Return visible global-search rows with a non-leaking keyset cursor.

		AI Document and File permissions can differ. Candidate documents are
		therefore intersected with permission-aware parent-folder pages. The scan
		is intentionally capped: if no visible look-ahead row is found within the
		bound, search truncates fail-closed rather than exposing hidden candidates
		through an offset, sparse continuation page, or unbounded retry loop.
		"""
		visible: list[tuple[dict, str]] = []
		scan_after = after
		scanned = 0
		target = length + 1
		while len(visible) < target and scanned < _SEARCH_DOCUMENT_SCAN_LIMIT:
			query_length = min(
				_SEARCH_DOCUMENT_SCAN_PAGE_LENGTH,
				_SEARCH_DOCUMENT_SCAN_LIMIT - scanned,
			)
			filters = dict(document_filters)
			if scan_after:
				filters["name"] = [">", scan_after]
			rows = frappe.get_list(
				"AI Document",
				filters=filters,
				or_filters=document_or_filters,
				fields=document_fields,
				order_by="name asc",
				limit_start=0,
				limit_page_length=query_length,
			)
			if not rows:
				break
			scanned += len(rows)
			scan_after = rows[-1].name
			requested_folders = list({row.folder or _HOME for row in rows})
			readable_folders = set(
				frappe.get_list(
					"File",
					filters={"name": ["in", requested_folders], "is_folder": 1},
					pluck="name",
					limit_page_length=0,
				)
			)
			for row in rows:
				node = _document_node(row, readable_folders)
				if node:
					visible.append((node, row.name))
					if len(visible) >= target:
						break
			if len(rows) < query_length:
				break

		nodes = [node for node, _name in visible[:length]]
		if len(visible) > length:
			# An empty anchor is valid when folders consumed the whole page: it
			# means the next request starts at the beginning of document matches.
			return nodes, visible[length - 1][1] if length else ""
		# Reaching the scan cap without visible look-ahead deliberately omits a
		# continuation. A cursor based only on hidden rows would be a side channel.
		return nodes, None

	nodes: list[dict] = []
	if cursor_kind == "folder":
		if not isinstance(position, int):
			frappe.throw(_("Invalid tree continuation token."), frappe.ValidationError)
		folder_rows = fetch_folders(position, page_length + 1)
		for row in folder_rows[:page_length]:
			node = _folder_node(row)
			if search:
				node["expandable"] = False
			nodes.append(node)
		if len(folder_rows) > page_length:
			return nodes, ("folder", position + page_length)
		remaining = page_length - len(nodes)
		if search:
			document_nodes, next_after = fetch_search_documents("", remaining)
			nodes.extend(document_nodes)
			return nodes, (("document_after", next_after) if next_after is not None else None)
		document_nodes, consumed, has_more = fetch_documents(0, remaining)
		nodes.extend(document_nodes)
		if has_more:
			return nodes, ("document", consumed)
		return nodes, None

	if cursor_kind == "document_after":
		if not search or not isinstance(position, str):
			frappe.throw(_("Invalid tree continuation token."), frappe.ValidationError)
		document_nodes, next_after = fetch_search_documents(position, page_length)
		nodes.extend(document_nodes)
		return nodes, (("document_after", next_after) if next_after is not None else None)

	if search or not isinstance(position, int):
		frappe.throw(_("Invalid tree continuation token."), frappe.ValidationError)
	document_nodes, consumed, has_more = fetch_documents(position, page_length)
	nodes.extend(document_nodes)
	return nodes, (("document", position + consumed) if has_more else None)


def get_children(
	doctype: str | None = None,
	parent: str | None = None,
	is_root: bool | str = False,
	knowledge_base: str | None = None,
	search: str | None = None,
	limit: int = DEFAULT_PAGE_LENGTH,
) -> list[dict]:
	"""Return one permission-filtered page of native Tree View node payloads."""
	if doctype and doctype != "AI Document":
		frappe.throw(_("Invalid tree DocType."), frappe.ValidationError)
	if not frappe.has_permission("AI Document", "read"):
		frappe.throw(_("Not permitted."), frappe.PermissionError)

	page_length = min(MAX_PAGE_LENGTH, max(10, cint(limit) or DEFAULT_PAGE_LENGTH))
	if parent is None:
		root = _folder(_HOME, "read")
		can_write_root = _can_folder(_HOME, "write")
		can_create_folder = _permission_allowed("File", "create") and can_write_root
		can_create_document = _permission_allowed("AI Document", "create") and can_write_root
		return [
			{
				"value": ROOT_LABEL,
				"title": _(ROOT_LABEL),
				"expandable": True,
				"node_type": "root",
				"folder": _HOME,
				"modified": str(root.modified),
				"can_read": True,
				"can_write": can_write_root,
				"can_create_folder": can_create_folder,
				"can_create_document": can_create_document,
				"can_create_child": can_create_folder or can_create_document,
				"can_delete": False,
			}
		]
	parent = parent or ROOT_LABEL
	node_type, value = split_node_value(parent)
	if node_type == "document":
		_document(value, "read")
		return []
	if node_type == "page":
		parent, cursor_kind, position = _decode_page(value)
	else:
		cursor_kind = "folder"
		position = 0
		parent = _HOME if node_type == "root" or bool(cint(is_root)) else value

	_folder(parent, "read")
	# AI Document row permissions are KB-scoped.  Keep Frappe's per-document
	# authorization authoritative while deduplicating its grant queries to one
	# result per KB/access mode for this bounded page only.
	from ai_fr_hg.utils.permissions import scoped_knowledge_base_permission_cache

	with scoped_knowledge_base_permission_cache():
		nodes, next_cursor = _tree_rows(
			parent,
			knowledge_base=knowledge_base,
			search=search,
			cursor_kind=cursor_kind,
			position=position,
			page_length=page_length,
		)
	if next_cursor:
		next_kind, next_offset = next_cursor
		nodes.append(
			{
				"value": _encode_page(parent, next_kind, next_offset),
				"title": _("Load more…"),
				"expandable": True,
				"node_type": "page",
				"folder": parent,
				"can_read": True,
				"can_write": False,
				"can_delete": False,
			}
		)
	return nodes


# ---------------------------------------------------------------------------
# Individual mutations
# ---------------------------------------------------------------------------


def create_folder(
	folder_name: str,
	parent: str | None = None,
	*,
	expected_parent_modified: str | None = None,
) -> dict:
	from ai_fr_hg.ai.folders import create_folder as create_native_folder

	parent = _normalize_folder_path(parent or _HOME)
	with _atomic("create_folder"):
		_lock("File", parent, expected_parent_modified)
		_folder(parent, "write")
		result = create_native_folder(folder_name, parent_folder=parent, user=frappe.session.user)
		_audit(
			"AI Document Tree Folder Created",
			_("Folder {0} was created in {1}.").format(result["name"], parent),
			details={"parent": parent, "folder": result["name"]},
			reference=result["name"],
		)
		return {**result, "node": result["name"]}


def rename_document(document: str, new_name: str, *, expected_modified: str | None = None) -> dict:
	new_name = _clean_name(new_name)
	snapshot = _document(document, "write")
	snapshot_folder = snapshot.folder or _HOME
	snapshot_source_type = snapshot.source_type
	snapshot_source_record = snapshot.get("source_file_record")
	snapshot_source_url = snapshot.source_file
	source_file_name = None
	source_file_folder = None
	if snapshot_source_type == "File":
		from ai_fr_hg.ai.ingestion import _file_doc

		source_file = _file_doc(snapshot_source_url, snapshot_source_record, snapshot.name)
		source_file_name = source_file.name
		source_file_folder = source_file.folder or _HOME
	with _atomic("rename_document"):
		# Canonical lifecycle order is parent folders, physical Files, then the
		# AI Document identity. ``doc.save()`` revalidates the source File, so it
		# must never begin while already holding the document row ahead of it.
		_lock_names("File", [snapshot_folder, source_file_folder])
		_lock_names("File", [source_file_name])
		_lock("AI Document", document, expected_modified)
		doc = _document(document, "write")
		if (
			(doc.folder or _HOME) != snapshot_folder
			or doc.source_type != snapshot_source_type
			or doc.get("source_file_record") != snapshot_source_record
			or doc.source_file != snapshot_source_url
		):
			frappe.throw(
				_("The document source or location changed. Refresh and try again."),
				frappe.TimestampMismatchError,
			)
		_folder(snapshot_folder, "write")
		if _document_collision(doc.folder or _HOME, new_name, exclude=doc.name):
			frappe.throw(
				_("A document named {0} already exists in this folder.").format(frappe.bold(new_name)),
				FolderAlreadyExistsError,
			)
		old_name = doc.organization_name or doc.title
		doc.title = new_name
		doc.organization_name = new_name
		doc.organization_revision = cint(doc.organization_revision) + 1
		doc.save()
		_audit(
			"AI Document Tree Document Renamed",
			_("Document {0} was renamed from {1} to {2}.").format(doc.name, old_name, new_name),
			details={"old": old_name, "new": new_name, "folder": doc.folder},
			reference=doc.name,
		)
		return {
			"node": document_node_value(doc.name),
			"name": doc.name,
			"title": new_name,
			"modified": str(doc.modified),
		}


def rename_folder(folder: str, new_name: str, *, expected_modified: str | None = None) -> dict:
	from ai_fr_hg.ai.folders import rename_folder as rename_native_folder

	folder = _normalize_folder_path(folder)
	if folder == _HOME:
		frappe.throw(_("The root folder cannot be renamed."), InvalidFolderNameError)
	parent_folder = folder.rsplit("/", 1)[0]
	with _atomic("rename_folder"):
		_lock_names("File", [parent_folder, folder])
		_lock("File", folder, expected_modified)
		folders, documents = _preflight_subtree(folder, "write")
		files = _files_in_folders(folders)
		_preflight_files(files, "write")
		_lock_subtree(folders, documents, files)
		# A descendant may have committed while its folder row was still being
		# acquired. Re-discover and re-authorize after locking so rename cannot
		# mutate a late, unauthorized AI Document through Frappe's Link cascade.
		folders, documents = _preflight_subtree(folder, "write")
		files = _files_in_folders(folders)
		_preflight_files(files, "write")
		_lock_subtree(folders, documents, files)
		_folder(folder.rsplit("/", 1)[0], "write")
		result = rename_native_folder(folder, new_name, user=frappe.session.user)
		# File names are path identities; file_name remains the display identity.
		frappe.db.set_value("File", result["name"], "file_name", _clean_name(new_name), update_modified=True)
		_sync_document_paths(result["name"])
		_audit(
			"AI Document Tree Folder Renamed",
			_("Folder {0} was renamed to {1}.").format(folder, result["name"]),
			details={"old": folder, "new": result["name"], "descendant_folders": len(folders) - 1},
			reference=result["name"],
		)
		return {**result, "node": result["name"]}


def move_document(
	document: str,
	target_folder: str | None = None,
	*,
	expected_modified: str | None = None,
) -> dict:
	from ai_fr_hg.ai.folders import copy_file, move_file

	target_folder = _normalize_folder_path(target_folder or _HOME)
	snapshot = _document(document, "write")
	snapshot_folder = snapshot.folder or _HOME
	snapshot_source_type = snapshot.source_type
	snapshot_source_record = snapshot.get("source_file_record")
	snapshot_source_url = snapshot.source_file
	source_file_name = None
	snapshot_source_folder = None
	if snapshot_source_type == "File":
		from ai_fr_hg.ai.ingestion import _file_doc

		source_file = _file_doc(snapshot_source_url, snapshot_source_record, snapshot.name)
		source_file_name = source_file.name
		snapshot_source_folder = source_file.folder or _HOME
	with _atomic("move_document"):
		_lock_names("File", [target_folder, snapshot_folder, snapshot_source_folder])
		_lock_names("File", [source_file_name])
		_lock("AI Document", document, expected_modified)
		doc = _document(document, "write")
		if (
			(doc.folder or _HOME) != snapshot_folder
			or doc.source_type != snapshot_source_type
			or doc.get("source_file_record") != snapshot_source_record
			or doc.source_file != snapshot_source_url
		):
			frappe.throw(
				_("The document source or location changed. Refresh and try again."),
				frappe.TimestampMismatchError,
			)
		_check_write_access(target_folder, user=frappe.session.user)
		old_folder = doc.folder or _HOME
		_folder(old_folder, "write")
		if old_folder == target_folder:
			return {
				"node": document_node_value(doc.name),
				"name": doc.name,
				"folder": target_folder,
				"unchanged": True,
			}
		if _document_collision(target_folder, doc.organization_name, exclude=doc.name):
			frappe.throw(
				_("A document named {0} already exists in the destination.").format(
					frappe.bold(doc.organization_name)
				),
				FolderAlreadyExistsError,
			)

		new_file_record = doc.get("source_file_record")
		new_file_url = doc.source_file
		if doc.source_type == "File":
			file_doc = frappe.get_doc("File", source_file_name)
			if (file_doc.folder or _HOME) != snapshot_source_folder:
				frappe.throw(
					_("The source file location changed. Refresh and try again."),
					frappe.TimestampMismatchError,
				)
			new_file_record = file_doc.name
			_check_permission("File", "write", doc=file_doc, user=frappe.session.user)
			remaining_reference = frappe.db.get_value(
				"AI Document",
				{"source_file_record": new_file_record, "name": ["!=", doc.name]},
				"name",
				order_by="name asc",
			)
			if not remaining_reference:
				remaining_reference = frappe.db.get_value(
					"AI Document",
					{
						"source_file_record": ["is", "not set"],
						"source_file": doc.source_file,
						"name": ["!=", doc.name],
					},
					"name",
					order_by="name asc",
				)
			if remaining_reference:
				# Keep other document identities at their current location while this
				# identity moves; the physical bytes remain deduplicated by File. If
				# the shared File was attached to the moving identity, transfer native
				# attachment ownership to a deterministic remaining source identity.
				if file_doc.attached_to_doctype == "AI Document" and file_doc.attached_to_name == doc.name:
					frappe.db.set_value(
						"File",
						file_doc.name,
						{
							"attached_to_doctype": "AI Document",
							"attached_to_name": remaining_reference,
							"attached_to_field": "source_file",
						},
						update_modified=False,
					)
				physical_name = _available_file_name(target_folder, file_doc.file_name)
				with suppress_ai_document_ingestion():
					copied = copy_file(
						new_file_record,
						target_folder,
						new_name=physical_name,
						user=frappe.session.user,
						attached_to_doctype="AI Document",
						attached_to_name=doc.name,
						attached_to_field="source_file",
					)
				new_file_record = copied["name"]
				new_file_url = frappe.db.get_value("File", new_file_record, "file_url")
				frappe.db.set_value(
					"File",
					new_file_record,
					{"attached_to_doctype": "AI Document", "attached_to_name": doc.name},
					update_modified=False,
				)
			else:
				move_file(new_file_record, target_folder, user=frappe.session.user)

		frappe.db.set_value(
			"AI Document",
			doc.name,
			{
				"folder": target_folder,
				"source_folder": target_folder,
				"source_file": new_file_url,
				"source_file_record": new_file_record,
				"organization_revision": cint(doc.organization_revision) + 1,
			},
			update_modified=True,
		)
		_audit(
			"AI Document Tree Document Moved",
			_("Document {0} was moved from {1} to {2}.").format(doc.name, old_folder, target_folder),
			details={
				"from": old_folder,
				"to": target_folder,
				"stable_identity": doc.name,
				"source_file_record": new_file_record,
			},
			reference=doc.name,
		)
		return {"node": document_node_value(doc.name), "name": doc.name, "folder": target_folder}


def move_folder(
	folder: str,
	target_folder: str | None = None,
	*,
	expected_modified: str | None = None,
	enqueue: bool | None = None,
	_expected_subtree_state: str | None = None,
) -> dict:
	from ai_fr_hg.ai.folders import move_folder as move_native_folder

	folder = _normalize_folder_path(folder)
	target_folder = _normalize_folder_path(target_folder or _HOME)
	if folder == _HOME:
		frappe.throw(_("The root folder cannot be moved."), InvalidFolderNameError)
	if target_folder == folder or target_folder.startswith(folder + "/"):
		frappe.throw(_("A folder cannot be moved into itself or its descendant."), CircularFolderError)
	source_parent = folder.rsplit("/", 1)[0]
	folders, documents = _preflight_subtree(folder, "write")
	files = _files_in_folders(folders)
	_preflight_files(files, "write")
	_folder(folder.rsplit("/", 1)[0], "write")
	_check_write_access(target_folder, user=frappe.session.user)
	item_count = len(folders) + len(documents) + len(files)
	should_enqueue = item_count > BACKGROUND_THRESHOLD if enqueue is None else bool(enqueue)
	if should_enqueue:
		with _atomic("queue_move_folder"):
			_lock_names("File", [source_parent, folder, target_folder])
			source_modified = _lock("File", folder, expected_modified)
			folders, documents = _preflight_subtree(folder, "write")
			files = _files_in_folders(folders)
			_preflight_files(files, "write")
			_check_write_access(target_folder, user=frappe.session.user)
			subtree_state = _subtree_state(folders, documents, files)
			job_id = f"ai-document-tree-move::{frappe.generate_hash(length=10)}"
			# Persist the fail-closed audit row before registering an after-commit
			# callback. If either step fails, the surrounding savepoint prevents a
			# job from being accepted without its audit record.
			_audit(
				"AI Document Tree Folder Move Queued",
				_("Recursive move of {0} was queued.").format(folder),
				details={"source": folder, "target": target_folder, "job_id": job_id, "items": item_count},
				reference=folder,
			)
			frappe.enqueue(
				"ai_fr_hg.ai.document_tree._move_folder_job",
				queue="long",
				timeout=7200,
				job_id=job_id,
				deduplicate=False,
				enqueue_after_commit=True,
				folder=folder,
				target_folder=target_folder,
				expected_modified=str(source_modified),
				expected_subtree_state=subtree_state,
				user=frappe.session.user,
			)
			return {"status": "Queued", "job_id": job_id, "source": folder, "target_folder": target_folder}

	with _atomic("move_folder"):
		_lock_names("File", [source_parent, folder, target_folder])
		_lock("File", folder, expected_modified)
		folders, documents = _preflight_subtree(folder, "write")
		files = _files_in_folders(folders)
		_preflight_files(files, "write")
		_lock_subtree(folders, documents, files)
		# Re-discover after acquiring the parent-folder locks. Inserts or moves
		# that committed while the first snapshot was being locked must either be
		# included (synchronous operation) or fail the queued-state fingerprint.
		folders, documents = _preflight_subtree(folder, "write")
		files = _files_in_folders(folders)
		_preflight_files(files, "write")
		_lock_subtree(folders, documents, files)
		_check_subtree_state(_expected_subtree_state, folders, documents, files)
		_folder(folder.rsplit("/", 1)[0], "write")
		_check_write_access(target_folder, user=frappe.session.user)
		result = move_native_folder(folder, target_folder, user=frappe.session.user)
		_sync_document_paths(result["name"])
		_audit(
			"AI Document Tree Folder Moved",
			_("Folder {0} was moved to {1}.").format(folder, result["name"]),
			details={"old": folder, "new": result["name"], "descendant_folders": len(folders) - 1},
			reference=result["name"],
		)
		return {**result, "node": result["name"], "status": "Completed"}


def _sync_document_paths(root: str) -> None:
	"""Synchronize denormalized provenance after Frappe updates folder Links."""
	folders = _folder_paths(root)
	for batch in _chunks(folders):
		rows = frappe.get_all(
			"AI Document",
			filters={"folder": ["in", batch]},
			fields=["name", "folder", "organization_revision"],
			limit_page_length=0,
		)
		for row in rows:
			frappe.db.set_value(
				"AI Document",
				row.name,
				{
					"source_folder": row.folder,
					"organization_revision": cint(row.organization_revision) + 1,
				},
				update_modified=True,
			)


_COPY_RESET_FIELDS = {
	"status": "Draft",
	"summary": None,
	"metadata": None,
	"mime_type": None,
	"file_size": 0,
	"page_count": 0,
	"word_count": 0,
	"character_count": 0,
	"reader_used": None,
	"chunk_count": 0,
	"embedded_chunk_count": 0,
	"indexed_on": None,
	"processing_duration_ms": 0,
	"retry_count": 0,
	"processing_requested_by": None,
	"processing_requested_on": None,
	"processing_job_id": None,
	"error_type": None,
	"error_message": None,
	"extracted_data": None,
	"confidence": 0,
}


def copy_document(
	document: str,
	target_folder: str | None = None,
	new_name: str | None = None,
	*,
	expected_modified: str | None = None,
) -> dict:
	"""Create an independent AI Document identity without copying derivatives.

	The source is untouched.  Source bytes are copied through native File, tags
	and source configuration are preserved, and the checksum may remain equal as
	content identity.  Chunks, embeddings, summaries, extraction/processing
	artifacts, timestamps, shares, and unrelated attachments are not cloned.
	"""
	from ai_fr_hg.ai.folders import copy_file

	target_folder = _normalize_folder_path(target_folder or _HOME)
	snapshot = _document(document, "read")
	snapshot_folder = snapshot.folder or _HOME
	snapshot_source_type = snapshot.source_type
	snapshot_source_record = snapshot.get("source_file_record")
	snapshot_source_url = snapshot.source_file
	source_file_name = None
	if snapshot_source_type == "File":
		from ai_fr_hg.ai.ingestion import _file_doc

		source_file_name = _file_doc(snapshot_source_url, snapshot_source_record, snapshot.name).name
	with _atomic("copy_document"):
		_lock_names("File", [target_folder, snapshot_folder])
		_lock_names("File", [source_file_name])
		_lock("AI Document", document, expected_modified)
		source = _document(document, "read")
		if (
			(source.folder or _HOME) != snapshot_folder
			or source.source_type != snapshot_source_type
			or source.get("source_file_record") != snapshot_source_record
			or source.source_file != snapshot_source_url
		):
			frappe.throw(
				_("The document source or location changed. Refresh and try again."),
				frappe.TimestampMismatchError,
			)
		if not frappe.has_permission("AI Document", "create", doc=source, user=frappe.session.user):
			frappe.throw(
				_("You do not have permission to create this document copy."), frappe.PermissionError
			)
		_folder(source.folder or _HOME, "read")
		_check_write_access(target_folder, user=frappe.session.user)
		source_file_doc = None
		if source_file_name:
			source_file_doc = frappe.get_doc("File", source_file_name)
			_check_permission("File", "read", doc=source_file_doc, user=frappe.session.user)
		requested = new_name or source.organization_name or source.title
		organization_name = resolve_document_name(
			target_folder,
			requested,
			copy_on_collision=new_name is None,
		)

		new_doc = frappe.copy_doc(source)
		new_doc.name = None
		new_doc.title = organization_name
		new_doc.organization_name = organization_name
		new_doc.folder = target_folder
		new_doc.source_folder = target_folder
		new_doc.organization_revision = 0
		new_doc.copied_from = source.name
		new_doc.copied_on = now_datetime()
		new_doc.owner = frappe.session.user
		new_doc.update(_COPY_RESET_FIELDS)
		if source.source_type != "Text":
			new_doc.content = None
		new_doc.flags.skip_auto_process = True
		from ai_fr_hg.ai_knowledge.doctype.ai_document.ai_document import (
			allow_copy_provenance,
			allow_deferred_file_source_sync,
		)

		# The AI Document identity must exist before its native File can validate
		# the attachment Dynamic Link. Both rows are completed in this savepoint;
		# the private context cannot be forged through document/client flags.
		with allow_copy_provenance(), allow_deferred_file_source_sync():
			new_doc.insert()

		new_file_record = None
		if source_file_doc:
			physical_name = _available_file_name(target_folder, source_file_doc.file_name)
			with suppress_ai_document_ingestion():
				file_result = copy_file(
					source_file_doc.name,
					target_folder,
					new_name=physical_name,
					user=frappe.session.user,
					attached_to_doctype="AI Document",
					attached_to_name=new_doc.name,
					attached_to_field="source_file",
				)
			new_file_record = file_result["name"]
			new_file_url = frappe.db.get_value("File", new_file_record, "file_url")
			frappe.db.set_value(
				"File",
				new_file_record,
				{
					"attached_to_doctype": "AI Document",
					"attached_to_name": new_doc.name,
					"attached_to_field": "source_file",
				},
				update_modified=False,
			)
			frappe.db.set_value(
				"AI Document",
				new_doc.name,
				{"source_file": new_file_url, "source_file_record": new_file_record},
				update_modified=False,
			)

		_audit(
			"AI Document Tree Document Copied",
			_("Document {0} was copied to new identity {1}.").format(source.name, new_doc.name),
			details={
				"source": source.name,
				"copy": new_doc.name,
				"target_folder": target_folder,
				"organization_name": organization_name,
				"source_file_record": new_file_record,
				"derivatives_copied": False,
				"shares_copied": False,
				"unrelated_attachments_copied": False,
			},
			reference=new_doc.name,
		)
		return {
			"node": document_node_value(new_doc.name),
			"name": new_doc.name,
			"title": organization_name,
			"folder": target_folder,
			"copied_from": source.name,
			"status": "Draft",
		}


def copy_folder(
	folder: str,
	target_folder: str | None = None,
	new_name: str | None = None,
	*,
	expected_modified: str | None = None,
	enqueue: bool | None = None,
	_expected_subtree_state: str | None = None,
) -> dict:
	folder = _normalize_folder_path(folder)
	target_folder = _normalize_folder_path(target_folder or _HOME)
	if folder == _HOME:
		frappe.throw(_("The root folder cannot be copied."), InvalidFolderNameError)
	if target_folder == folder or target_folder.startswith(folder + "/"):
		frappe.throw(_("A folder cannot be copied into itself or its descendant."), CircularFolderError)

	folders, documents = _preflight_subtree(folder, "read")
	_preflight_document_copies(documents)
	_folder(folder.rsplit("/", 1)[0], "read")
	_check_write_access(target_folder, user=frappe.session.user)
	should_enqueue = (
		(len(folders) + len(documents) > BACKGROUND_THRESHOLD) if enqueue is None else bool(enqueue)
	)
	if should_enqueue:
		with _atomic("queue_copy_folder"):
			_lock_names("File", [folder, target_folder])
			source_modified = _lock("File", folder, expected_modified)
			# Re-run authority after locking so the queued snapshot cannot differ
			# from the subtree that was permission-checked.
			folders, documents = _preflight_subtree(folder, "read")
			source_files = _preflight_document_copies(documents)
			_check_write_access(target_folder, user=frappe.session.user)
			subtree_state = _subtree_state(folders, documents, source_files)
			job_id = f"ai-document-tree-copy::{frappe.generate_hash(length=10)}"
			_audit(
				"AI Document Tree Folder Copy Queued",
				_("Recursive copy of {0} was queued.").format(folder),
				details={
					"source": folder,
					"target": target_folder,
					"job_id": job_id,
					"items": len(folders) + len(documents),
				},
				reference=folder,
			)
			frappe.enqueue(
				"ai_fr_hg.ai.document_tree._copy_folder_job",
				queue="long",
				timeout=7200,
				job_id=job_id,
				deduplicate=False,
				enqueue_after_commit=True,
				folder=folder,
				target_folder=target_folder,
				new_name=new_name,
				expected_modified=str(source_modified),
				expected_subtree_state=subtree_state,
				user=frappe.session.user,
			)
			return {"status": "Queued", "job_id": job_id, "source": folder, "target_folder": target_folder}

	from ai_fr_hg.ai.folders import create_folder as create_native_folder

	with _atomic("copy_folder"):
		_lock_names("File", [folder, target_folder])
		_lock("File", folder, expected_modified)
		folders, documents = _preflight_subtree(folder, "read")
		source_files = _preflight_document_copies(documents)
		_lock_subtree(folders, documents, source_files)
		folders, documents = _preflight_subtree(folder, "read")
		source_files = _preflight_document_copies(documents)
		_lock_subtree(folders, documents, source_files)
		_check_subtree_state(_expected_subtree_state, folders, documents, source_files)
		_folder(folder.rsplit("/", 1)[0], "read")
		source_doc = _get_folder_doc(folder)
		root_name = _available_folder_name(
			target_folder,
			new_name or source_doc.file_name,
			copy_on_collision=new_name is None,
		)
		settings_rows = frappe.get_all(
			"AI Folder Settings",
			filters={"folder": ["in", folders]},
			fields=["folder", "description", "knowledge_base"],
			limit_page_length=0,
		)
		settings = {row.folder: row for row in settings_rows}
		root_settings = settings.get(folder)
		created = create_native_folder(
			root_name,
			parent_folder=target_folder,
			description=root_settings.description if root_settings else None,
			knowledge_base=root_settings.knowledge_base if root_settings else None,
			user=frappe.session.user,
		)
		mapping = {folder: created["name"]}
		total = len(folders) + len(documents)
		_progress(1, total, _("Copying folder subtree"))
		for folder_index, source_folder in enumerate(
			sorted(folders[1:], key=lambda value: (value.count("/"), value)), start=2
		):
			parent = source_folder.rsplit("/", 1)[0]
			row = _get_folder_doc(source_folder)
			folder_settings = settings.get(source_folder)
			result = create_native_folder(
				row.file_name,
				parent_folder=mapping[parent],
				description=folder_settings.description if folder_settings else None,
				knowledge_base=folder_settings.knowledge_base if folder_settings else None,
				user=frappe.session.user,
			)
			mapping[source_folder] = result["name"]
			_progress(folder_index, total, _("Copying folder subtree"))

		copies = []
		for document_index, row in enumerate(
			sorted(documents, key=lambda item: (item.folder, item.organization_name or "", item.name)),
			start=1,
		):
			copies.append(copy_document(row.name, mapping[row.folder]))
			_progress(len(folders) + document_index, total, _("Copying folder subtree"))
		_audit(
			"AI Document Tree Folder Copied",
			_("Folder {0} was recursively copied to {1}.").format(folder, created["name"]),
			details={
				"source": folder,
				"copy": created["name"],
				"folder_count": len(mapping),
				"document_count": len(copies),
			},
			reference=created["name"],
		)
		return {
			"status": "Completed",
			"node": created["name"],
			"name": created["name"],
			"folder_count": len(mapping),
			"document_count": len(copies),
			"copied_from": folder,
		}


def delete_document(document: str, *, expected_modified: str | None = None) -> dict:
	snapshot = _document(document, "delete")
	snapshot_folder = snapshot.folder or _HOME
	snapshot_source_record = snapshot.get("source_file_record")
	snapshot_source_url = snapshot.source_file

	def attachment_state() -> list[tuple[str, str]]:
		return sorted(
			(row.name, row.folder or _HOME)
			for row in frappe.get_all(
				"File",
				filters={"attached_to_doctype": "AI Document", "attached_to_name": document},
				fields=["name", "folder"],
				limit_page_length=0,
			)
		)

	snapshot_attachments = attachment_state()
	source_file_folder = None
	if snapshot_source_record and frappe.db.exists("File", snapshot_source_record):
		source_file_folder = frappe.db.get_value("File", snapshot_source_record, "folder") or _HOME
	with _atomic("delete_document"):
		# Native deletion removes attachments after the parent lifecycle. Acquire
		# every known parent and physical File before the AI Document row so File
		# upload/move hooks and direct deletion share one deterministic order.
		_lock_names(
			"File",
			[
				snapshot_folder,
				source_file_folder,
				*[folder for _name, folder in snapshot_attachments],
			],
		)
		_lock_names(
			"File",
			[snapshot_source_record, *[name for name, _folder in snapshot_attachments]],
		)
		_lock("AI Document", document, expected_modified)
		doc = _document(document, "delete")
		if (
			(doc.folder or _HOME) != snapshot_folder
			or doc.get("source_file_record") != snapshot_source_record
			or doc.source_file != snapshot_source_url
			or attachment_state() != snapshot_attachments
		):
			frappe.throw(
				_("The document source, location, or attachments changed. Refresh and try again."),
				frappe.TimestampMismatchError,
			)
		_folder(snapshot_folder, "write")
		source_file_record = doc.get("source_file_record")
		folder = doc.folder or _HOME
		# Native Frappe deletion remains authoritative for attached-File retention.
		# ``delete_doc`` may remove Files attached to this AI Document according to
		# framework policy; unrelated/shared Files are protected by link checks.
		frappe.delete_doc("AI Document", doc.name, ignore_permissions=False)
		source_file_retained = bool(source_file_record and frappe.db.exists("File", source_file_record))
		_audit(
			"AI Document Tree Document Deleted",
			_("Document {0} was deleted using the framework attachment policy.").format(document),
			details={
				"document": document,
				"folder": folder,
				"source_file_record": source_file_record,
				"source_file_retained": source_file_retained,
			},
			# Dynamic Links are validated on insert. Keep the deleted identity in
			# immutable details instead of creating a broken audit-log link.
			reference=None,
		)
		return {
			"deleted": document,
			"folder": folder,
			"source_file_retained": source_file_retained,
		}


def _files_in_folders(folders: list[str]) -> list:
	rows = []
	for batch in _chunks(folders):
		rows.extend(
			frappe.get_all(
				"File",
				filters={"folder": ["in", batch], "is_folder": 0},
				fields=[
					"name",
					"folder",
					"file_url",
					"owner",
					"is_private",
					"attached_to_doctype",
					"attached_to_name",
				],
				limit_page_length=0,
			)
		)
	return rows


def _preflight_files(files: list, permission: str) -> None:
	if any(
		not frappe.has_permission(
			"File",
			permission,
			doc=_permission_row("File", row),
			user=frappe.session.user,
		)
		for row in files
	):
		frappe.throw(_("You do not have permission for the complete folder subtree."), FolderPermissionError)


def _assert_no_unmanaged_files(files: list, documents: list) -> None:
	"""Do not silently destroy Files hidden by the AI Document tree."""
	if not files:
		return
	represented = {row.name for row in _source_files_for_documents(documents)}
	unmanaged = [row for row in files if row.name not in represented]
	if unmanaged:
		frappe.throw(
			_(
				"This folder contains {0} file(s) that are not represented by AI Documents. "
				"Move or delete them from the File view before deleting this folder."
			).format(len(unmanaged)),
			frappe.LinkExistsError,
		)


def delete_folder(
	folder: str,
	*,
	recursive: bool = False,
	expected_modified: str | None = None,
	enqueue: bool | None = None,
	_expected_subtree_state: str | None = None,
) -> dict:
	folder = _normalize_folder_path(folder)
	if folder == _HOME:
		frappe.throw(_("The root folder cannot be deleted."), InvalidFolderNameError)
	source_parent = folder.rsplit("/", 1)[0]
	_folder(source_parent, "write")
	folders, documents = _preflight_subtree(folder, "delete")
	files = _files_in_folders(folders)
	_preflight_files(files, "delete")
	_assert_no_unmanaged_files(files, documents)
	item_count = len(folders) + len(documents) + len(files)
	if item_count > 1 and not recursive:
		frappe.throw(_("The folder is not empty. Confirm recursive deletion first."), FolderNotEmptyError)
	should_enqueue = (item_count > BACKGROUND_THRESHOLD) if enqueue is None else bool(enqueue)
	if should_enqueue:
		with _atomic("queue_delete_folder"):
			_lock_names("File", [source_parent, folder])
			source_modified = _lock("File", folder, expected_modified)
			folders, documents = _preflight_subtree(folder, "delete")
			files = _files_in_folders(folders)
			_preflight_files(files, "delete")
			subtree_state = _subtree_state(folders, documents, files)
			job_id = f"ai-document-tree-delete::{frappe.generate_hash(length=10)}"
			_audit(
				"AI Document Tree Folder Delete Queued",
				_("Recursive deletion of {0} was queued.").format(folder),
				details={"folder": folder, "job_id": job_id, "items": item_count},
				reference=folder,
			)
			frappe.enqueue(
				"ai_fr_hg.ai.document_tree._delete_folder_job",
				queue="long",
				timeout=7200,
				job_id=job_id,
				deduplicate=False,
				enqueue_after_commit=True,
				folder=folder,
				expected_modified=str(source_modified),
				expected_subtree_state=subtree_state,
				user=frappe.session.user,
			)
			return {"status": "Queued", "job_id": job_id, "folder": folder, "items": item_count}

	with _atomic("delete_folder"):
		_lock_names("File", [source_parent, folder])
		_lock("File", folder, expected_modified)
		_folder(source_parent, "write")
		folders, documents = _preflight_subtree(folder, "delete")
		files = _files_in_folders(folders)
		_preflight_files(files, "delete")
		_lock_subtree(folders, documents, files)
		folders, documents = _preflight_subtree(folder, "delete")
		files = _files_in_folders(folders)
		_preflight_files(files, "delete")
		_assert_no_unmanaged_files(files, documents)
		_lock_subtree(folders, documents, files)
		_check_subtree_state(_expected_subtree_state, folders, documents, files)

		# A source File cannot be destroyed while an AI Document outside the
		# selected subtree still owns that stable source identity. Stream reference
		# pages because one physical File may be shared by arbitrarily many legacy
		# documents.
		folder_set = set(folders)

		def has_outside_reference(filters: dict[str, Any]) -> bool:
			offset = 0
			while True:
				references = frappe.get_all(
					"AI Document",
					filters=filters,
					fields=["name", "folder"],
					order_by="name asc",
					limit_start=offset,
					limit_page_length=400,
				)
				if not references:
					return False
				if any((row.folder or _HOME) not in folder_set for row in references):
					return True
				offset += len(references)

		for file_batch in _chunks(files):
			file_names = [row.name for row in file_batch]
			file_urls = [row.file_url for row in file_batch if row.file_url]
			shared = has_outside_reference({"source_file_record": ["in", file_names]})
			if not shared and file_urls:
				shared = has_outside_reference(
					{
						"source_file_record": ["is", "not set"],
						"source_file": ["in", file_urls],
					}
				)
			if shared:
				frappe.throw(
					_("A file in this folder is still used by a document outside the selected subtree."),
					frappe.LinkExistsError,
				)

		total = len(documents) + len(files) + len(folders)
		completed = 0
		document_names = {row.name for row in documents}
		# Prevent native per-document attachment cleanup from trying to delete a
		# shared in-subtree File before every authoritative AI Document link has
		# been removed. The locked Files are deleted explicitly just below.
		for row in files:
			if row.attached_to_doctype == "AI Document" and row.attached_to_name in document_names:
				frappe.db.set_value(
					"File",
					row.name,
					{
						"attached_to_doctype": None,
						"attached_to_name": None,
						"attached_to_field": None,
					},
					update_modified=False,
				)
		knowledge_bases = {row.knowledge_base for row in documents if row.knowledge_base}
		for row in documents:
			frappe.delete_doc(
				"AI Document",
				row.name,
				ignore_permissions=False,
				flags={"skip_knowledge_base_stats": True},
			)
			completed += 1
			_progress(completed, total, _("Deleting folder subtree"))
		for row in files:
			frappe.delete_doc("File", row.name, ignore_permissions=False)
			completed += 1
			_progress(completed, total, _("Deleting folder subtree"))
		from ai_fr_hg.ai.folders import _delete_folder_record

		for path in sorted(folders, key=lambda value: (value.count("/"), value), reverse=True):
			_delete_folder_record(path)
			completed += 1
			_progress(completed, total, _("Deleting folder subtree"))
		from ai_fr_hg.ai.knowledge import update_knowledge_base_stats

		for knowledge_base in sorted(knowledge_bases):
			if frappe.db.exists("AI Knowledge Base", knowledge_base):
				update_knowledge_base_stats(knowledge_base)
		_audit(
			"AI Document Tree Folder Deleted",
			_("Folder {0} and its authorized subtree were deleted.").format(folder),
			details={
				"folder": folder,
				"folders": len(folders),
				"documents": len(documents),
				"files": len(files),
			},
			# The File row no longer exists; retain its identity in details rather
			# than inserting a broken Dynamic Link into the audit log.
			reference=None,
		)
		return {
			"status": "Completed",
			"deleted": folder,
			"folder_count": len(folders),
			"document_count": len(documents),
			"file_count": len(files),
		}


# ---------------------------------------------------------------------------
# Mixed-node and bulk entry points
# ---------------------------------------------------------------------------


def rename_node(node: str, new_name: str, *, expected_modified: str | None = None) -> dict:
	node_type, name = split_node_value(node)
	if node_type == "document":
		return rename_document(name, new_name, expected_modified=expected_modified)
	if node_type == "folder":
		return rename_folder(name, new_name, expected_modified=expected_modified)
	frappe.throw(_("The selected tree node cannot be renamed."), frappe.ValidationError)


def move_node(
	node: str,
	target_folder: str | None = None,
	*,
	expected_modified: str | None = None,
) -> dict:
	node_type, name = split_node_value(node)
	if node_type == "document":
		return move_document(name, target_folder, expected_modified=expected_modified)
	if node_type == "folder":
		return move_folder(name, target_folder, expected_modified=expected_modified)
	frappe.throw(_("The selected tree node cannot be moved."), frappe.ValidationError)


def copy_node(
	node: str,
	target_folder: str | None = None,
	new_name: str | None = None,
	*,
	expected_modified: str | None = None,
) -> dict:
	node_type, name = split_node_value(node)
	if node_type == "document":
		return copy_document(name, target_folder, new_name, expected_modified=expected_modified)
	if node_type == "folder":
		return copy_folder(name, target_folder, new_name, expected_modified=expected_modified)
	frappe.throw(_("The selected tree node cannot be copied."), frappe.ValidationError)


def delete_node(
	node: str,
	*,
	recursive: bool = False,
	expected_modified: str | None = None,
) -> dict:
	node_type, name = split_node_value(node)
	if node_type == "document":
		return delete_document(name, expected_modified=expected_modified)
	if node_type == "folder":
		return delete_folder(name, recursive=recursive, expected_modified=expected_modified)
	frappe.throw(_("The selected tree node cannot be deleted."), frappe.ValidationError)


def _coerce_nodes(nodes: list[str]) -> list[str]:
	result = []
	seen = set()
	for node in nodes:
		node = str(node or "")
		if not node:
			continue
		node_type, name = split_node_value(node)
		if node_type not in {"folder", "document"} or not name:
			frappe.throw(_("Bulk operations only accept folder and document nodes."), frappe.ValidationError)
		if node_type == "folder" and name == _HOME:
			frappe.throw(_("The root folder cannot be used in a bulk mutation."), frappe.ValidationError)
		canonical = document_node_value(name) if node_type == "document" else name
		if canonical in seen:
			continue
		seen.add(canonical)
		result.append(canonical)
	return result


def _prune_nested_nodes(nodes: list[str]) -> list[str]:
	"""Avoid applying an operation twice when an ancestor is also selected.

	Document locations are resolved in bounded batches rather than one query per
	selected document. Authorization is intentionally completed before callers
	invoke this helper, so pruning can never hide a forbidden explicit selection.
	"""
	parsed = [(node, *split_node_value(node)) for node in nodes]
	folders = {name for _node, node_type, name in parsed if node_type == "folder"}
	document_names = [name for _node, node_type, name in parsed if node_type == "document"]
	document_folders = {
		row.name: (row.folder or _HOME)
		for row in _rows_by_name("AI Document", document_names, ["name", "folder"])
	}

	result = []
	for node, node_type, name in parsed:
		if node_type == "folder" and any(
			name.startswith(parent + "/") for parent in folders if parent != name
		):
			continue
		if node_type == "document":
			folder = document_folders.get(name, _HOME)
			if any(folder == parent or folder.startswith(parent + "/") for parent in folders):
				continue
		result.append(node)
	return result


def _preflight_bulk_snapshot(
	nodes: list[str],
	*,
	permission: str,
	file_permission: str,
	include_document_files: bool,
) -> tuple[list[str], list, list]:
	"""Return one deduplicated, authorized state set for a bulk mutation."""
	folders: set[str] = set()
	documents: dict[str, Any] = {}
	files: dict[str, Any] = {}
	for node in nodes:
		node_type, name = split_node_value(node)
		if node_type == "document":
			doc = _document(name, permission)
			_folder(doc.folder or _HOME, "write")
			documents[doc.name] = doc
			if include_document_files and doc.source_type == "File":
				from ai_fr_hg.ai.ingestion import _file_doc

				file_doc = _file_doc(doc.source_file, doc.get("source_file_record"), doc.name)
				_check_permission("File", file_permission, doc=file_doc, user=frappe.session.user)
				files[file_doc.name] = file_doc
			continue

		subtree_folders, subtree_documents = _preflight_subtree(name, permission)
		physical_files = _files_in_folders(subtree_folders)
		_preflight_files(physical_files, file_permission)
		folders.update(subtree_folders)
		documents.update({row.name: row for row in subtree_documents})
		files.update({row.name: row for row in physical_files})
	ordered_documents = [documents[name] for name in sorted(documents)]
	ordered_files = [files[name] for name in sorted(files)]
	if permission == "delete" and file_permission == "delete":
		_assert_no_unmanaged_files(ordered_files, ordered_documents)
	return sorted(folders), ordered_documents, ordered_files


def bulk_move_nodes(
	nodes: list[str],
	target_folder: str,
	*,
	enqueue: bool | None = None,
	_expected_bulk_state: str | None = None,
) -> dict:
	nodes = _coerce_nodes(nodes)
	if not nodes:
		frappe.throw(_("Select at least one item."), frappe.ValidationError)
	target_folder = _normalize_folder_path(target_folder or _HOME)
	_check_write_access(target_folder, user=frappe.session.user)
	folders, documents, files = _preflight_bulk_snapshot(
		nodes,
		permission="write",
		file_permission="write",
		include_document_files=True,
	)
	work_count = len(folders) + len(documents) + len(files)
	nodes = _prune_nested_nodes(nodes)
	folders, documents, files = _preflight_bulk_snapshot(
		nodes,
		permission="write",
		file_permission="write",
		include_document_files=True,
	)
	selected_folders = {
		name for node in nodes for node_type, name in [split_node_value(node)] if node_type == "folder"
	}
	parent_folders = sorted(
		{row.folder or _HOME for row in documents}
		| selected_folders
		| {folder.rsplit("/", 1)[0] for folder in selected_folders if folder != _HOME}
	)
	should_enqueue = work_count > BACKGROUND_THRESHOLD if enqueue is None else bool(enqueue)
	if should_enqueue:
		with _atomic("queue_bulk_move"):
			expected_bulk_state = _subtree_state(folders, documents, files)
			job_id = f"ai-document-tree-bulk-move::{frappe.generate_hash(length=10)}"
			_audit(
				"AI Document Tree Bulk Move Queued",
				_("Bulk move of {0} items was queued.").format(len(nodes)),
				details={"nodes": nodes, "target": target_folder, "job_id": job_id},
			)
			frappe.enqueue(
				"ai_fr_hg.ai.document_tree._bulk_move_job",
				queue="long",
				timeout=7200,
				job_id=job_id,
				deduplicate=False,
				enqueue_after_commit=True,
				nodes=nodes,
				target_folder=target_folder,
				expected_bulk_state=expected_bulk_state,
				user=frappe.session.user,
			)
			return {"status": "Queued", "job_id": job_id, "count": len(nodes), "target_folder": target_folder}

	with _atomic("bulk_move"):
		_lock_names("File", [target_folder, *parent_folders])
		_lock_subtree(folders, documents, files)
		folders, documents, files = _preflight_bulk_snapshot(
			nodes,
			permission="write",
			file_permission="write",
			include_document_files=True,
		)
		parent_folders = sorted(
			{row.folder or _HOME for row in documents}
			| selected_folders
			| {folder.rsplit("/", 1)[0] for folder in selected_folders if folder != _HOME}
		)
		_lock_names("File", [target_folder, *parent_folders])
		_lock_subtree(folders, documents, files)
		_check_subtree_state(_expected_bulk_state, folders, documents, files)
		results = []
		for node in nodes:
			node_type, name = split_node_value(node)
			if node_type == "folder":
				results.append(move_folder(name, target_folder, enqueue=False))
			else:
				results.append(move_document(name, target_folder))
		_audit(
			"AI Document Tree Bulk Move Completed",
			_("Bulk move of {0} items completed.").format(len(results)),
			details={"nodes": nodes, "target": target_folder},
		)
		return {"status": "Completed", "moved": results, "target_folder": target_folder}


def bulk_delete_nodes(
	nodes: list[str],
	*,
	recursive: bool = False,
	enqueue: bool | None = None,
	_expected_bulk_state: str | None = None,
) -> dict:
	nodes = _coerce_nodes(nodes)
	if not nodes:
		frappe.throw(_("Select at least one item."), frappe.ValidationError)
	folders, documents, files = _preflight_bulk_snapshot(
		nodes,
		permission="delete",
		file_permission="delete",
		include_document_files=False,
	)
	work_count = len(folders) + len(documents) + len(files)
	nodes = _prune_nested_nodes(nodes)
	folders, documents, files = _preflight_bulk_snapshot(
		nodes,
		permission="delete",
		file_permission="delete",
		include_document_files=False,
	)
	selected_folders = {
		name for node in nodes for node_type, name in [split_node_value(node)] if node_type == "folder"
	}
	parent_folders = sorted(
		{row.folder or _HOME for row in documents}
		| selected_folders
		| {folder.rsplit("/", 1)[0] for folder in selected_folders if folder != _HOME}
	)
	if not recursive and selected_folders:
		has_nested_folders = any(
			folder not in selected_folders
			and any(folder.startswith(selected + "/") for selected in selected_folders)
			for folder in folders
		)
		has_nested_documents = any(
			any(
				(row.folder or _HOME) == selected or (row.folder or _HOME).startswith(selected + "/")
				for selected in selected_folders
			)
			for row in documents
		)
		if has_nested_folders or has_nested_documents or files:
			frappe.throw(
				_("A selected folder is not empty. Confirm recursive deletion first."), FolderNotEmptyError
			)
	should_enqueue = work_count > BACKGROUND_THRESHOLD if enqueue is None else bool(enqueue)
	if should_enqueue:
		with _atomic("queue_bulk_delete"):
			expected_bulk_state = _subtree_state(folders, documents, files)
			job_id = f"ai-document-tree-bulk-delete::{frappe.generate_hash(length=10)}"
			_audit(
				"AI Document Tree Bulk Delete Queued",
				_("Bulk deletion of {0} items was queued.").format(len(nodes)),
				details={"nodes": nodes, "recursive": recursive, "job_id": job_id},
			)
			frappe.enqueue(
				"ai_fr_hg.ai.document_tree._bulk_delete_job",
				queue="long",
				timeout=7200,
				job_id=job_id,
				deduplicate=False,
				enqueue_after_commit=True,
				nodes=nodes,
				recursive=recursive,
				expected_bulk_state=expected_bulk_state,
				user=frappe.session.user,
			)
			return {"status": "Queued", "job_id": job_id, "count": len(nodes)}

	with _atomic("bulk_delete"):
		_lock_names("File", parent_folders)
		_lock_subtree(folders, documents, files)
		folders, documents, files = _preflight_bulk_snapshot(
			nodes,
			permission="delete",
			file_permission="delete",
			include_document_files=False,
		)
		parent_folders = sorted(
			{row.folder or _HOME for row in documents}
			| selected_folders
			| {folder.rsplit("/", 1)[0] for folder in selected_folders if folder != _HOME}
		)
		_lock_names("File", parent_folders)
		_lock_subtree(folders, documents, files)
		_check_subtree_state(_expected_bulk_state, folders, documents, files)
		results = []
		for node in nodes:
			node_type, name = split_node_value(node)
			if node_type == "folder":
				results.append(delete_folder(name, recursive=recursive, enqueue=False))
			else:
				results.append(delete_document(name))
		_audit(
			"AI Document Tree Bulk Delete Completed",
			_("Bulk deletion of {0} items completed.").format(len(results)),
			details={"nodes": nodes, "recursive": recursive},
		)
		return {"status": "Completed", "deleted": results}


# ---------------------------------------------------------------------------
# Background workers restore and re-check the initiating user's authority.
# Successful jobs rely on Frappe's native worker transaction commit; failures
# are rolled back by the framework.
# ---------------------------------------------------------------------------


@contextmanager
def _as_user(user: str):
	previous = frappe.session.user
	if user != previous:
		# Security-reviewed worker boundary: enqueue captures the requester and
		# every called service rechecks that user's document permissions.
		frappe.set_user(user)  # nosemgrep
	try:
		yield
	finally:
		if frappe.session.user != previous:
			frappe.set_user(previous)  # nosemgrep


def _copy_folder_job(
	folder: str,
	target_folder: str,
	new_name: str | None,
	expected_modified: str,
	expected_subtree_state: str,
	user: str,
) -> dict:
	with _as_user(user):
		result = copy_folder(
			folder,
			target_folder,
			new_name,
			expected_modified=expected_modified,
			enqueue=False,
			_expected_subtree_state=expected_subtree_state,
		)
		return result


def _move_folder_job(
	folder: str,
	target_folder: str,
	expected_modified: str,
	expected_subtree_state: str,
	user: str,
) -> dict:
	with _as_user(user):
		_progress(1, 2, _("Moving folder subtree"))
		result = move_folder(
			folder,
			target_folder,
			expected_modified=expected_modified,
			enqueue=False,
			_expected_subtree_state=expected_subtree_state,
		)
		_progress(2, 2, _("Moving folder subtree"))
		return result


def _delete_folder_job(
	folder: str,
	expected_modified: str,
	expected_subtree_state: str,
	user: str,
) -> dict:
	with _as_user(user):
		result = delete_folder(
			folder,
			recursive=True,
			expected_modified=expected_modified,
			enqueue=False,
			_expected_subtree_state=expected_subtree_state,
		)
		return result


def _bulk_move_job(nodes: list[str], target_folder: str, expected_bulk_state: str, user: str) -> dict:
	with _as_user(user):
		result = bulk_move_nodes(
			nodes,
			target_folder,
			enqueue=False,
			_expected_bulk_state=expected_bulk_state,
		)
		return result


def _bulk_delete_job(nodes: list[str], recursive: bool, expected_bulk_state: str, user: str) -> dict:
	with _as_user(user):
		result = bulk_delete_nodes(
			nodes,
			recursive=recursive,
			enqueue=False,
			_expected_bulk_state=expected_bulk_state,
		)
		return result
