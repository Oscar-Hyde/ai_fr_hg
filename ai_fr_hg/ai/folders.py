# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Canonical folder and attachment organization service.

Frappe's native ``File`` DocType is the single source of truth for folders
and files (``is_folder``, ``folder``, ``file_name``, ``file_url``,
``attached_to_doctype``/``attached_to_name``).  This module owns every
folder/file organization operation — creation, rename, move, delete, copy,
search, breadcrumb, tree, bulk operations and file-to-folder assignment —
with deterministic validation, permission checks and audit provenance.

Every UI action, API, background job and AI-driven file placement funnels
through this one service (Master §3.2-§3.5, File & Folder §6).

Folder paths are Frappe-native ``File.name`` values:
  - Root is ``"Home"`` (``is_home_folder = 1``)
  - Child ``Home/Invoices`` has ``folder = "Home"`` and ``file_name = "Invoices"``
  - Deeply nested ``Home/Projects/Acme/Contracts`` chains via ``folder``

The ``File.folder`` link is the parent folder's ``name``.  ``File.file_name``
is the display name within that parent (no slashes).
"""

from __future__ import annotations

import re
from contextlib import contextmanager
from typing import Any

import frappe
from frappe import _
from frappe.utils import cint, now_datetime

from ai_fr_hg.ai.exceptions import (
	AmbiguousFileIdentityError,
	CircularFolderError,
	FileNotFoundError,
	FolderAlreadyExistsError,
	FolderNotEmptyError,
	FolderNotFoundError,
	FolderPermissionError,
	InvalidFolderNameError,
)

# ---------------------------------------------------------------------------
# Constants and typed error helpers
# ---------------------------------------------------------------------------

_HOME = "Home"
_ATTACHMENTS = "Home/Attachments"
_MAX_FOLDER_DEPTH = 20
_MAX_NAME_LENGTH = 140
_INVALID_NAME_PATTERN = re.compile(r"[\\/:*?\"<>|]")
_RESERVED_NAMES = {"", ".", ".."}


def _throw(exc_class, message: str) -> None:
	frappe.throw(_(message), exc=exc_class)


@contextmanager
def _mutation_savepoint(operation: str):
	"""Make a service mutation rollback-safe even for an in-process caller."""
	save_point = f"folder_{operation}_{frappe.generate_hash(length=10)}"
	frappe.db.savepoint(save_point)
	try:
		yield
	except Exception:
		frappe.db.rollback(save_point=save_point)
		raise
	else:
		frappe.db.release_savepoint(save_point)


# ---------------------------------------------------------------------------
# Basic validation and path helpers
# ---------------------------------------------------------------------------


def _clean_name(name: str) -> str:
	"""Strip and validate a file/folder name (no slashes, no empties)."""
	if name is None:
		_throw(InvalidFolderNameError, "Folder or file name cannot be empty.")
	name = str(name).strip()
	if not name or name in _RESERVED_NAMES:
		_throw(InvalidFolderNameError, "Folder or file name cannot be empty.")
	if len(name) > _MAX_NAME_LENGTH:
		_throw(InvalidFolderNameError, f"Name cannot exceed {_MAX_NAME_LENGTH} characters.")
	if _INVALID_NAME_PATTERN.search(name):
		_throw(InvalidFolderNameError, 'Name cannot contain / \\ : * ? " < > |')
	if "/" in name or "\\" in name:
		_throw(InvalidFolderNameError, "Name cannot contain path separators.")
	return name


def _normalize_folder_path(folder: str | None) -> str:
	"""Normalize a folder identifier to its Frappe File name."""
	if not folder:
		return _HOME
	folder = str(folder).strip()
	# Accept both File.name (Home/Foo) and legacy folder path strings.
	folder = folder.replace("\\", "/").strip("/")
	if not folder:
		return _HOME
	# Ensure leading Home for canonical consistency unless already Home-relative.
	# Frappe folders are always under Home.
	if folder == "Home":
		return "Home"
	if not folder.startswith("Home/"):
		# If caller passes just "Foo", assume it's under Home.
		# If they pass "Home/Foo", keep it.
		if "/" not in folder:
			return f"Home/{folder}"
		# Fallback: try as-is first, validation will catch invalid.
		return folder
	return folder


def _folder_name_from_path(path: str) -> str:
	"""Extract display file_name from a folder path."""
	return path.rsplit("/", 1)[-1] if "/" in path else path


def _parent_from_path(path: str) -> str | None:
	"""Parent folder path for a given File.name path."""
	if path == _HOME or "/" not in path:
		return None
	return path.rsplit("/", 1)[0]


def _depth(path: str) -> int:
	return path.count("/")


def _ensure_home_exists() -> None:
	"""Ensure the canonical Home folder exists (idempotent)."""
	if not frappe.db.exists("File", _HOME):
		# Home creation participates in the caller's transaction. A savepoint
		# makes a concurrent insert race recoverable without committing unrelated
		# work or leaving PostgreSQL's transaction in an aborted state.
		save_point = f"ensure_home_{frappe.generate_hash(length=8)}"
		frappe.db.savepoint(save_point)
		try:
			doc = frappe.new_doc("File")
			doc.update(
				{
					"file_name": "Home",
					"is_folder": 1,
					"folder": None,
					"is_home_folder": 1,
				}
			)
			doc.flags.ignore_permissions = True
			doc.insert(ignore_permissions=True)
			frappe.db.release_savepoint(save_point)
		except Exception:
			frappe.db.rollback(save_point=save_point)
			if not frappe.db.exists("File", _HOME):
				raise


def _get_file_doc(name: str):
	"""Return File doc or throw FileNotFoundError."""
	if not frappe.db.exists("File", name):
		_throw(FileNotFoundError, f"File or folder '{name}' does not exist.")
	return frappe.get_doc("File", name)


def resolve_file_identity(file_url: str, file_record: str | None = None, document_name: str | None = None):
	"""Resolve one stable File identity; ambiguous legacy URLs fail closed.

	This is the single resolver used by ingestion (document sources) and the
	upload facade (attachment placement). Duplicate File rows may share a URL,
	so a URL without a stable identity must never select an arbitrary record.
	"""
	name = file_record
	if name:
		if not frappe.db.exists("File", name):
			_throw(FileNotFoundError, f"File record '{name}' does not exist.")
	else:
		# Legacy rows have only a URL. An exact attachment to the requesting AI
		# Document is stable enough to backfill; otherwise URL/content identity
		# cannot distinguish duplicate File rows.
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
			_throw(
				AmbiguousFileIdentityError,
				f"More than one File is attached as the source of AI Document '{document_name}'.",
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
				_throw(
					AmbiguousFileIdentityError,
					f"More than one File record uses '{file_url}'; provide the exact File identity.",
				)
			name = matches[0] if matches else None
	if not name:
		_throw(FileNotFoundError, f"File record not found for '{file_url}'.")
	file_doc = frappe.get_doc("File", name)
	if file_doc.file_url != file_url:
		_throw(AmbiguousFileIdentityError, f"File record '{name}' does not match '{file_url}'.")
	return file_doc


def _get_folder_doc(folder_path: str):
	"""Return folder File doc or throw FolderNotFoundError."""
	normalized = _normalize_folder_path(folder_path)
	if not frappe.db.exists("File", normalized):
		_throw(FolderNotFoundError, f"Folder '{folder_path}' does not exist.")
	doc = frappe.get_doc("File", normalized)
	if not cint(doc.is_folder):
		_throw(FolderNotFoundError, f"'{folder_path}' is not a folder.")
	return doc


def _assert_folder_exists(folder_path: str) -> str:
	"""Validate folder exists and is a folder, return normalized path."""
	normalized = _normalize_folder_path(folder_path)
	if not frappe.db.exists("File", normalized):
		_throw(FolderNotFoundError, f"Folder '{folder_path}' does not exist.")
	doc = frappe.db.get_value("File", normalized, ["is_folder"], as_dict=True)
	if not doc or not cint(doc.is_folder):
		_throw(FolderNotFoundError, f"'{folder_path}' is not a folder.")
	return normalized


def _lock_folder_rows(*folder_paths: str | None) -> None:
	"""Serialize child membership changes against recursive tree snapshots."""
	for folder_path in sorted({_normalize_folder_path(path) for path in folder_paths if path}):
		locked = frappe.db.get_value("File", folder_path, "name", for_update=True)
		if not locked:
			_throw(FolderNotFoundError, f"Folder '{folder_path}' does not exist.")


def _lock_file_rows(*file_names: str | None) -> None:
	"""Lock non-parent File rows after their parent folders are locked."""
	for file_name in sorted({name for name in file_names if name}):
		if not frappe.db.get_value("File", file_name, "name", for_update=True):
			_throw(FileNotFoundError, f"File '{file_name}' does not exist.")


def _check_permission(
	doc_or_doctype: str | Any,
	permission_type: str = "write",
	user: str | None = None,
	doc: Any | None = None,
) -> None:
	"""Check File permission for the given user; throw typed error on deny."""
	user = user or frappe.session.user
	# Use Frappe's native permission check — never a custom ACL.
	if isinstance(doc_or_doctype, str) and doc is None:
		# doctype level check
		if not frappe.has_permission(doc_or_doctype, permission_type, user=user):
			_throw(
				FolderPermissionError, f"User {user} lacks {permission_type} permission for {doc_or_doctype}."
			)
		return
	# document level check
	target = doc if doc is not None else doc_or_doctype
	doctype = target.doctype if hasattr(target, "doctype") else "File"
	if not frappe.has_permission(doctype, permission_type, doc=target, user=user):
		_throw(
			FolderPermissionError,
			f"User {user} is not allowed to {permission_type} '{getattr(target, 'name', doctype)}'.",
		)


def _check_write_access(folder_path: str, user: str | None = None) -> None:
	"""Check that user may write into a folder."""
	user = user or frappe.session.user
	folder_path = _normalize_folder_path(folder_path)
	folder_doc = _get_folder_doc(folder_path)
	_check_permission("File", "write", user=user, doc=folder_doc)
	# Also check generic File create permission for new children.
	if not frappe.has_permission("File", "create", user=user):
		_throw(FolderPermissionError, f"User {user} cannot create files in '{folder_path}'.")


def _is_descendant(potential_descendant: str, ancestor: str) -> bool:
	"""Return True if potential_descendant is inside ancestor (or equal)."""
	ancestor = _normalize_folder_path(ancestor)
	potential_descendant = _normalize_folder_path(potential_descendant)
	if potential_descendant == ancestor:
		return True
	return potential_descendant.startswith(ancestor + "/")


def _assert_no_circular(source_folder: str, target_folder: str) -> None:
	"""Raise CircularFolderError if moving source into its own descendant."""
	source = _normalize_folder_path(source_folder)
	target = _normalize_folder_path(target_folder)
	if source == target:
		_throw(CircularFolderError, "A folder cannot be moved into itself.")
	if _is_descendant(target, source):
		_throw(CircularFolderError, "A folder cannot become its own descendant.")


def _assert_unique_in_parent(file_name: str, parent_folder: str, is_folder: bool | None = None) -> None:
	"""Raise if a child with same file_name already exists in parent."""
	parent_folder = _normalize_folder_path(parent_folder)
	file_name = file_name.strip()
	filters = {"folder": parent_folder, "file_name": file_name}
	if is_folder is not None:
		filters["is_folder"] = 1 if is_folder else 0
	# Search by file_name within parent. Frappe's is_folder doesn't affect uniqueness
	# separately, but we allow file vs folder collision to be considered a collision
	# for UX clarity (no two children with same display name in one folder).
	count = frappe.db.count("File", {"folder": parent_folder, "file_name": file_name})
	if count:
		_throw(
			FolderAlreadyExistsError,
			f"An item named '{file_name}' already exists in folder '{parent_folder}'.",
		)


def _write_audit(
	action: str,
	message: str,
	details: dict | None = None,
	reference_doctype: str = "File",
	reference_name: str | None = None,
) -> None:
	"""Write a fail-closed audit record for a folder mutation."""
	from ai_fr_hg.ai.logging import write_audit_log

	write_audit_log(
		action=action,
		category="Data",
		message=message,
		details=details or {},
		reference_doctype=reference_doctype,
		reference_name=reference_name,
		raise_on_error=True,
	)


# ---------------------------------------------------------------------------
# Core operations
# ---------------------------------------------------------------------------


def get_folder_path(folder_name: str) -> list[dict]:
	"""Return breadcrumb path from Home to the given folder."""
	normalized = _normalize_folder_path(folder_name)
	_assert_folder_exists(normalized)
	parts = normalized.split("/")
	crumbs = []
	for i in range(len(parts)):
		path = "/".join(parts[: i + 1])
		if not frappe.db.exists("File", path):
			continue
		doc = frappe.db.get_value("File", path, ["file_name", "is_folder"], as_dict=True)
		crumbs.append(
			{
				"name": path,
				"file_name": doc.file_name if doc else parts[i],
				"is_folder": bool(doc.is_folder) if doc else True,
			}
		)
	return crumbs


def get_breadcrumbs(file_or_folder: str) -> list[dict]:
	"""Breadcrumb for a file or folder."""
	name = str(file_or_folder).strip()
	if not frappe.db.exists("File", name):
		_throw(FileNotFoundError, f"'{file_or_folder}' does not exist.")
	doc = frappe.get_doc("File", name)
	if cint(doc.is_folder):
		return get_folder_path(name)
	# For files, breadcrumbs are parent folder path + file itself.
	parent = doc.folder or _HOME
	crumbs = get_folder_path(parent)
	crumbs.append({"name": doc.name, "file_name": doc.file_name, "is_folder": False})
	return crumbs


# Frappe v17 rejects SQL-function strings in get_list fields. Use the native
# dict aggregate syntax so permission-aware counts stay on the list path.
_COUNT_FIELD = {"COUNT": "*", "as": "total"}
_SUM_SIZE_FIELD = {"SUM": "file_size", "as": "total"}


def _aggregate_total(rows) -> int:
	if not rows:
		return 0
	row = rows[0]
	if hasattr(row, "get"):
		return cint(row.get("total") or row.get("count") or row.get("sum") or 0)
	return cint(getattr(row, "total", getattr(row, "count", 0)))


def _permission_aware_count(
	doctype: str,
	filters: dict,
	*,
	or_filters: list | None = None,
) -> int:
	"""Count only rows exposed by Frappe's list permission query."""
	return _aggregate_total(
		frappe.get_list(
			doctype,
			filters=filters,
			or_filters=or_filters,
			fields=[_COUNT_FIELD],
			limit_page_length=1,
		)
	)


def _permission_aware_file_size(filters: dict, *, or_filters: list | None = None) -> int:
	"""Sum visible File sizes without bypassing Frappe query permissions."""
	return _aggregate_total(
		frappe.get_list(
			"File",
			filters=filters,
			or_filters=or_filters,
			fields=[_SUM_SIZE_FIELD],
			limit_page_length=1,
		)
	)


def _visible_ai_document_for_file(file_row, user: str | None = None):
	"""Return a permission-filtered canonical AI Document for a File row.

	Stable File identity is authoritative. A legacy URL-only association is
	accepted only when the File is attached to that exact AI Document or the URL
	identifies one File and one visible legacy document. Duplicate content never
	causes an arbitrary document identity to be displayed for another File.
	"""
	fields = ["name", "status", "knowledge_base"]
	rows = frappe.get_list(
		"AI Document",
		filters={"source_file_record": file_row.name},
		fields=fields,
		order_by="creation asc, name asc",
		limit_page_length=1,
	)
	file_url = file_row.get("file_url")
	if rows or not file_url:
		return rows[0] if rows else None

	attached_document = (
		file_row.get("attached_to_name") if file_row.get("attached_to_doctype") == "AI Document" else None
	)
	if attached_document:
		rows = frappe.get_list(
			"AI Document",
			filters={
				"name": attached_document,
				"source_file": file_url,
				"source_file_record": ["is", "not set"],
			},
			fields=fields,
			limit_page_length=1,
		)
		if rows:
			return rows[0]

	matching_files = frappe.get_all(
		"File",
		filters={"file_url": file_url, "is_folder": 0},
		pluck="name",
		order_by="creation asc, name asc",
		limit_page_length=2,
	)
	if matching_files != [file_row.name]:
		return None
	rows = frappe.get_list(
		"AI Document",
		filters={
			"source_file": file_url,
			"source_file_record": ["is", "not set"],
		},
		fields=fields,
		order_by="creation asc, name asc",
		limit_page_length=2,
	)
	return rows[0] if len(rows) == 1 else None


def list_folder_contents(
	folder: str | None = None,
	*,
	include_files: bool = True,
	include_folders: bool = True,
	filters: dict | None = None,
	order_by: str = "file_name asc",
	limit: int = 50,
	offset: int = 0,
	search_text: str | None = None,
) -> dict:
	"""List immediate children of a folder with permission filtering."""
	limit = max(1, min(cint(limit) or 50, 200))
	offset = max(0, cint(offset))
	allowed_order = {
		"file_name asc",
		"file_name desc",
		"creation asc",
		"creation desc",
		"modified asc",
		"modified desc",
		"file_size asc",
		"file_size desc",
	}
	order_by = str(order_by or "file_name asc").strip().lower()
	if order_by not in allowed_order:
		frappe.throw(_("Unsupported folder sort order."), frappe.ValidationError)
	folder = _normalize_folder_path(folder or _HOME)
	_assert_folder_exists(folder)
	# Permission check: user must have read access to the folder.
	folder_doc = _get_folder_doc(folder)
	_check_permission("File", "read", doc=folder_doc)

	base_filters: dict = {"folder": folder}
	if not include_files and not include_folders:
		return {"folder": folder, "items": [], "total": 0}
	if include_files != include_folders:
		base_filters["is_folder"] = 1 if include_folders and not include_files else 0

	if filters:
		base_filters.update(filters)

	# Apply search text if provided.
	or_filters = None
	if search_text:
		text = str(search_text).strip()
		if text:
			or_filters = [["file_name", "like", f"%{text}%"], ["file_url", "like", f"%{text}%"]]

	# Frappe's native list path applies role, user-permission, and sharing query
	# conditions before pagination. Per-row checks below remain a fail-closed
	# defense for File's attachment-aware controller permissions.
	items = frappe.get_list(
		"File",
		filters=base_filters,
		or_filters=or_filters,
		fields=[
			"name",
			"file_name",
			"is_folder",
			"folder",
			"file_url",
			"file_size",
			"file_type",
			"is_private",
			"attached_to_doctype",
			"attached_to_name",
			"attached_to_field",
			"owner",
			"creation",
			"modified",
			"modified_by",
		],
		order_by=order_by,
		limit_page_length=limit,
		limit_start=offset,
	)
	# ``get_list`` is Frappe's authoritative permission-aware list path, so its
	# pagination and aggregate count operate over the same visible row set. Avoid
	# a second per-row ``get_doc`` permission pass that causes N+1 queries and
	# sparse pages. Linked AI Document metadata is independently permission-aware.
	for item in items:
		if not item.get("is_folder"):
			ai_doc = _visible_ai_document_for_file(item)
			if ai_doc:
				item["ai_document"] = ai_doc.name
				item["ingestion_status"] = ai_doc.status

	total = _permission_aware_count("File", base_filters, or_filters=or_filters)
	return {"folder": folder, "items": items, "total": total, "breadcrumbs": get_folder_path(folder)}


def get_tree(
	root: str | None = None,
	*,
	max_depth: int = 4,
	include_files: bool = False,
	user: str | None = None,
) -> dict:
	"""Return nested folder tree from root, permission-filtered."""
	root = _normalize_folder_path(root or _HOME)
	_assert_folder_exists(root)
	user = user or frappe.session.user

	def _build(path: str, depth: int) -> dict | None:
		if depth > max_depth:
			return None
		doc = _get_folder_doc(path)
		if not frappe.has_permission("File", "read", doc=doc, user=user):
			return None
		node = {
			"name": path,
			"file_name": doc.file_name,
			"folder": doc.folder,
			"is_folder": True,
			"children": [],
		}
		children = frappe.get_all(
			"File",
			filters={"folder": path, "is_folder": 1},
			fields=["name", "file_name"],
			order_by="file_name asc",
		)
		for child in children:
			child_doc = frappe.get_doc("File", child.name)
			if not frappe.has_permission("File", "read", doc=child_doc, user=user):
				continue
			sub = _build(child.name, depth + 1)
			if sub:
				node["children"].append(sub)
		# Optionally include files at this level
		if include_files:
			files = frappe.get_all(
				"File",
				filters={"folder": path, "is_folder": 0},
				fields=["name", "file_name", "file_url"],
				order_by="file_name asc",
				limit_page_length=100,
			)
			for f in files:
				fdoc = frappe.get_doc("File", f.name)
				if not frappe.has_permission("File", "read", doc=fdoc, user=user):
					continue
				node["children"].append(
					{
						"name": f.name,
						"file_name": f.file_name,
						"folder": path,
						"is_folder": False,
						"file_url": f.file_url,
					}
				)
		return node

	return _build(root, 0) or {
		"name": root,
		"file_name": _folder_name_from_path(root),
		"is_folder": True,
		"children": [],
	}


def get_default_folder(
	user: str | None = None,
	doctype: str | None = None,
	docname: str | None = None,
) -> str:
	"""Return a sensible default folder for an upload.

	Resolution order:
	  1. The configured shared storage folder, but only when it exists and the
	     requesting user may write it (it is a shared, manager-configured
	     destination - not a per-user home).
	  2. If doctype/docname provided, the folder of the parent record's
	     attachments or a conventional per-DocType folder.
	  3. The shared "Shared Uploads" folder under Home (auto-created).
	  4. Home/Attachments (Frappe default).
	  5. Home
	"""
	user = user or frappe.session.user
	# Try the configured storage folder when the app settings DocType is already
	# installed. Unexpected database/permission failures propagate rather than
	# leaving the site in an aborted transaction and silently choosing a path.
	if frappe.db.exists("DocType", "AI Platform Settings"):
		storage_folder = frappe.db.get_single_value("AI Platform Settings", "storage_folder")
		if (
			storage_folder
			and frappe.db.exists("File", storage_folder)
			and cint(frappe.db.get_value("File", storage_folder, "is_folder"))
		):
			storage_doc = frappe.get_doc("File", storage_folder)
			if frappe.has_permission("File", "write", doc=storage_doc, user=user):
				return _normalize_folder_path(storage_folder)

	if doctype and docname and frappe.db.exists(doctype, docname):
		attached_doc = frappe.get_doc(doctype, docname)
		if frappe.has_permission(doctype, "read", doc=attached_doc, user=user):
			# If the record already has an attachment, reuse its canonical parent.
			files = frappe.get_all(
				"File",
				filters={"attached_to_doctype": doctype, "attached_to_name": docname},
				fields=["folder"],
				limit_page_length=1,
			)
			if files and files[0].folder and frappe.db.exists("File", files[0].folder):
				folder_doc = frappe.get_doc("File", files[0].folder)
				if cint(folder_doc.is_folder) and frappe.has_permission(
					"File", "read", doc=folder_doc, user=user
				):
					return files[0].folder
			# Otherwise prefer an existing conventional per-DocType folder.
			candidate = f"Home/{doctype}"
			if frappe.db.exists("File", candidate):
				candidate_doc = frappe.get_doc("File", candidate)
				if frappe.has_permission("File", "read", doc=candidate_doc, user=user):
					return candidate

	# Shared Uploads. Default discovery must not catch-and-commit a partial
	# folder creation (for example when fail-closed audit persistence raises).
	# Users without File creation/write authority simply continue to the native
	# Attachments/Home fallbacks.
	candidate_user_folder = "Home/Shared Uploads"
	_ensure_home_exists()
	if not frappe.db.exists("File", candidate_user_folder):
		home_doc = frappe.get_doc("File", _HOME)
		# Capability probes select a default only; create_folder independently
		# enforces both permissions before any mutation.
		can_create_default = bool(
			frappe.has_permission("File", "create", user=user)  # nosemgrep
			and frappe.has_permission("File", "write", doc=home_doc, user=user)  # nosemgrep
		)
		if can_create_default:
			try:
				create_folder("Shared Uploads", parent_folder=_HOME, is_private=0, user=user)
			except FolderAlreadyExistsError:
				# A concurrent creator won after our existence check.
				pass
	if frappe.db.exists("File", candidate_user_folder):
		return candidate_user_folder

	# Fallback to Home/Attachments if exists
	if frappe.db.exists("File", _ATTACHMENTS):
		return _ATTACHMENTS
	# Final fallback
	return _HOME


def create_folder(
	folder_name: str,
	parent_folder: str | None = None,
	*,
	is_private: int | None = None,
	description: str | None = None,
	knowledge_base: str | None = None,
	user: str | None = None,
) -> dict:
	"""Create a new folder under parent_folder.

	Validates uniqueness within parent, permission, depth, and no circular nesting
	trivially (parent cannot be descendant of new folder since new folder has no descendants).
	"""
	user = user or frappe.session.user
	_ensure_home_exists()
	parent_folder = _normalize_folder_path(parent_folder or _HOME)
	_assert_folder_exists(parent_folder)
	_check_write_access(parent_folder, user=user)
	_lock_folder_rows(parent_folder)

	cleaned = _clean_name(folder_name)
	_assert_unique_in_parent(cleaned, parent_folder, is_folder=True)

	if _depth(parent_folder) + 1 > _MAX_FOLDER_DEPTH:
		_throw(InvalidFolderNameError, "Folder nesting exceeds maximum depth.")

	# Determine privacy from the locked canonical parent. Unexpected database or
	# lifecycle failures must abort creation rather than silently making a child
	# public.
	if is_private is None:
		parent_doc = _get_folder_doc(parent_folder)
		is_private = cint(parent_doc.is_private)
	else:
		is_private = cint(is_private)

	# Create via Frappe's File DocType
	file_doc = frappe.new_doc("File")
	file_doc.update(
		{
			"file_name": cleaned,
			"folder": parent_folder,
			"is_folder": 1,
			"is_private": is_private,
		}
	)
	# is_home_folder stays 0 for non-root
	file_doc.flags.ignore_permissions = False
	# Insert will trigger File autoname to set name = parent/file_name. Recover
	# to a savepoint before translating duplicate errors on PostgreSQL.
	save_point = f"create_folder_{frappe.generate_hash(length=8)}"
	frappe.db.savepoint(save_point)
	try:
		file_doc.insert()
		frappe.db.release_savepoint(save_point)
	except Exception as exc:
		frappe.db.rollback(save_point=save_point)
		if "already exists" in str(exc).lower() or "duplicate" in str(exc).lower():
			_throw(
				FolderAlreadyExistsError, f"An item named '{cleaned}' already exists in '{parent_folder}'."
			)
		raise

	# Requested metadata is part of the same transaction; never return a
	# partially configured folder when settings persistence fails.
	if description or knowledge_base:
		meta = frappe.new_doc("AI Folder Settings")
		meta.update(
			{
				"folder": file_doc.name,
				"description": description or "",
				"knowledge_base": knowledge_base,
			}
		)
		meta.flags.ignore_permissions = True
		meta.insert(ignore_permissions=True)

	_write_audit(
		"Folder Created",
		f"Folder '{file_doc.name}' created under '{parent_folder}'.",
		details={"folder": file_doc.name, "parent": parent_folder, "owner": user},
		reference_name=file_doc.name,
	)
	# Also write to AI Folder Settings provenance if exists
	track_folder_operation("create", file_doc.name, parent_folder, user)

	return {"name": file_doc.name, "file_name": cleaned, "folder": parent_folder, "is_folder": 1}


def rename_folder(folder_name: str, new_name: str, *, user: str | None = None) -> dict:
	"""Rename a folder, updating all descendant paths."""
	user = user or frappe.session.user
	folder_name = _normalize_folder_path(folder_name)
	if folder_name == _HOME:
		_throw(InvalidFolderNameError, "The Home folder cannot be renamed.")
	_assert_folder_exists(folder_name)
	cleaned = _clean_name(new_name)

	doc = _get_folder_doc(folder_name)
	_check_permission("File", "write", doc=doc, user=user)

	parent = doc.folder or _HOME
	_lock_folder_rows(parent, folder_name)
	doc = _get_folder_doc(folder_name)
	if (doc.folder or _HOME) != parent:
		frappe.throw(_("The folder location changed. Refresh and try again."), frappe.TimestampMismatchError)
	if folder_name.rsplit("/", 1)[-1] == cleaned:
		# No change
		return {"name": folder_name, "file_name": cleaned, "folder": parent}

	_assert_unique_in_parent(cleaned, parent, is_folder=True)
	new_path = (
		f"{parent}/{cleaned}"
		if parent and parent != _HOME
		else f"{_HOME}/{cleaned}"
		if parent == _HOME
		else cleaned
	)
	# Ensure new_path doesn't already exist as File
	if frappe.db.exists("File", new_path):
		_throw(FolderAlreadyExistsError, f"An item named '{cleaned}' already exists in '{parent}'.")

	# Gather the complete subtree before rename. The exact Python checks are
	# required because valid folder names may contain SQL LIKE wildcards.
	old_prefix = folder_name + "/"
	descendants = [
		row
		for row in frappe.get_all(
			"File",
			filters={"name": ["like", f"{old_prefix}%"]},
			fields=[
				"name",
				"folder",
				"is_folder",
				"file_name",
				"owner",
				"is_private",
				"attached_to_doctype",
				"attached_to_name",
			],
			limit_page_length=0,
		)
		if row.name.startswith(old_prefix)
	]
	files_in_tree = [
		row
		for row in frappe.get_all(
			"File",
			filters={"folder": ["like", f"{folder_name}%"]},
			fields=[
				"name",
				"folder",
				"is_folder",
				"file_name",
				"owner",
				"is_private",
				"attached_to_doctype",
				"attached_to_name",
			],
			limit_page_length=0,
		)
		if row.folder == folder_name or row.folder.startswith(old_prefix)
	]
	# Merge without duplicates.
	all_affected = {d.name: d for d in descendants}
	for d in files_in_tree:
		all_affected[d.name] = d
	# Also the folder itself
	all_affected[doc.name] = doc

	_lock_folder_rows(*[row.name for row in all_affected.values() if cint(row.is_folder)])
	_lock_file_rows(*[row.name for row in all_affected.values() if not cint(row.is_folder)])
	fresh_names = {
		row.name
		for row in frappe.get_all(
			"File",
			filters={"name": ["like", f"{old_prefix}%"]},
			fields=["name"],
			limit_page_length=0,
		)
		if row.name.startswith(old_prefix)
	}
	fresh_names.update(
		row.name
		for row in frappe.get_all(
			"File",
			filters={"folder": ["like", f"{folder_name}%"]},
			fields=["name", "folder"],
			limit_page_length=0,
		)
		if row.folder == folder_name or row.folder.startswith(old_prefix)
	)
	fresh_names.add(folder_name)
	if fresh_names != set(all_affected):
		frappe.throw(_("The folder subtree changed. Refresh and try again."), frappe.TimestampMismatchError)

	# Permission-check every affected row without loading the subtree a second
	# time. This avoids a document-fetch N+1 on large renames.
	for name, inner in all_affected.items():
		if not frappe.has_permission("File", "write", doc=inner, user=user):
			_throw(FolderPermissionError, f"User {user} cannot modify '{name}' during rename.")

	# Perform rename via SQL to avoid Frappe's autoname side-effects, then clear cache.
	# The canonical way is frappe.rename_doc, but it may not cascade child paths.
	# We'll do it manually:
	# 1. Update the folder's own File record's file_name and name.
	# File.name for folders is constructed from parent + file_name. Changing file_name updates name.
	# Use frappe.rename_doc for the top folder to preserve framework hooks.
	frappe.rename_doc("File", folder_name, new_path, force=True, show_alert=False)
	# frappe.rename_doc already updates File.name, but child folder's File.folder still points to old path.
	# Need to fix child records.
	new_prefix = new_path + "/"
	for old_name, _details in sorted(all_affected.items(), key=lambda item: (item[0].count("/"), item[0])):
		if old_name == folder_name:
			continue
		# old_name is like Home/Old/Child or Home/Old/Child/Sub or file hash? Files have hash names not path-based.
		# For folders, name is path-based, so old_name startswith old_prefix.
		# For files, name is hash, but folder field is old path. So we need to handle both.
		if old_name.startswith(old_prefix):
			# This is a descendant folder (path-based name)
			suffix = old_name[len(old_prefix) :]
			expected_new_name = new_prefix + suffix
			if frappe.db.exists("File", old_name):
				frappe.rename_doc("File", old_name, expected_new_name, force=True, show_alert=False)
		else:
			# This is a file (hash name) whose folder field points to old location or descendant
			# Update its folder field if it is inside the renamed subtree.
			fetched = frappe.db.get_value("File", old_name, ["folder"], as_dict=True)
			if fetched and fetched.folder and fetched.folder.startswith(folder_name):
				# Replace prefix
				old_folder = fetched.folder
				if old_folder == folder_name:
					new_folder = new_path
				elif old_folder.startswith(old_prefix):
					new_folder = new_prefix + old_folder[len(old_prefix) :]
				else:
					continue
				frappe.db.set_value("File", old_name, "folder", new_folder, update_modified=False)

	# Also need to update direct files' folder that were not caught via name prefix (their name is hash)
	# Already handled in loop above if fetched.folder matches.

	# Update AI Folder Settings reference if exists
	if frappe.db.exists("AI Folder Settings", {"folder": folder_name}):
		frappe.db.set_value("AI Folder Settings", {"folder": folder_name}, "folder", new_path)
	# Update descendant AI Folder Settings
	settings = frappe.get_all(
		"AI Folder Settings",
		filters={"folder": ["like", f"{old_prefix}%"]},
		fields=["name", "folder"],
		limit_page_length=0,
	)
	for s in settings:
		if s.folder.startswith(old_prefix):
			new_sf = new_prefix + s.folder[len(old_prefix) :]
			frappe.db.set_value("AI Folder Settings", s.name, "folder", new_sf, update_modified=False)

	# Clear caches
	frappe.clear_document_cache("File", new_path)

	_write_audit(
		"Folder Renamed",
		f"Folder '{folder_name}' renamed to '{new_path}'.",
		details={"old": folder_name, "new": new_path, "owner": user},
		reference_name=new_path,
	)
	track_folder_operation("rename", new_path, folder_name, user)

	return {"name": new_path, "file_name": cleaned, "folder": parent, "old_name": folder_name}


def rename_file(file_name: str, new_name: str, *, user: str | None = None) -> dict:
	"""Rename a file (not a folder)."""
	user = user or frappe.session.user
	doc = _get_file_doc(file_name)
	if cint(doc.is_folder):
		_throw(InvalidFolderNameError, "Use rename_folder for folders.")
	_check_permission("File", "write", doc=doc, user=user)
	cleaned = _clean_name(new_name)
	parent = doc.folder or _HOME
	_lock_folder_rows(parent)
	_lock_file_rows(file_name)
	doc = _get_file_doc(file_name)
	if (doc.folder or _HOME) != parent:
		frappe.throw(_("The file location changed. Refresh and try again."), frappe.TimestampMismatchError)
	_assert_unique_in_parent(cleaned, parent, is_folder=False)
	# file_name is the display name; updating it doesn't change doc.name (which is hash)
	frappe.db.set_value("File", file_name, "file_name", cleaned, update_modified=False)
	frappe.clear_document_cache("File", file_name)
	_write_audit(
		"File Renamed",
		f"File '{file_name}' renamed from '{doc.file_name}' to '{cleaned}'.",
		details={"file": file_name, "old": doc.file_name, "new": cleaned},
		reference_name=file_name,
	)
	return {"name": file_name, "file_name": cleaned, "folder": parent}


def move_file(file_name: str, target_folder: str, *, user: str | None = None) -> dict:
	"""Move a file to a different folder without breaking its attachments."""
	user = user or frappe.session.user
	doc = _get_file_doc(file_name)
	if cint(doc.is_folder):
		_throw(InvalidFolderNameError, "Use move_folder for folders.")
	target_folder = _normalize_folder_path(target_folder)
	_assert_folder_exists(target_folder)
	_check_permission("File", "write", doc=doc, user=user)
	_check_write_access(target_folder, user=user)

	old_folder = doc.folder or _HOME
	_lock_folder_rows(old_folder, target_folder)
	_lock_file_rows(file_name)
	doc = _get_file_doc(file_name)
	if (doc.folder or _HOME) != old_folder:
		frappe.throw(_("The file location changed. Refresh and try again."), frappe.TimestampMismatchError)
	_check_permission("File", "write", doc=doc, user=user)
	if old_folder == target_folder:
		return {"name": file_name, "folder": target_folder}

	_assert_unique_in_parent(doc.file_name, target_folder, is_folder=False)

	frappe.db.set_value("File", file_name, "folder", target_folder, update_modified=False)
	frappe.db.set_value(
		"File",
		file_name,
		"is_private",
		cint(frappe.db.get_value("File", target_folder, "is_private")),
		update_modified=False,
	)
	frappe.clear_document_cache("File", file_name)

	_write_audit(
		"File Moved",
		f"File '{file_name}' moved from '{old_folder}' to '{target_folder}'.",
		details={"file": file_name, "from": old_folder, "to": target_folder, "owner": user},
		reference_name=file_name,
	)
	track_folder_operation("move_file", file_name, target_folder, user, details={"from": old_folder})
	# A physical move and its stable AI Document location are one mutation.
	_update_document_folder_provenance(file_name, target_folder)
	return {"name": file_name, "file_name": doc.file_name, "folder": target_folder}


def move_folder(folder_name: str, target_folder: str, *, user: str | None = None) -> dict:
	"""Move a folder (and all its descendants) into another folder."""
	user = user or frappe.session.user
	folder_name = _normalize_folder_path(folder_name)
	target_folder = _normalize_folder_path(target_folder)

	if folder_name == _HOME:
		_throw(InvalidFolderNameError, "The Home folder cannot be moved.")
	_assert_folder_exists(folder_name)
	_assert_folder_exists(target_folder)
	_assert_no_circular(folder_name, target_folder)

	source_doc = _get_folder_doc(folder_name)
	_check_permission("File", "write", doc=source_doc, user=user)
	_check_write_access(target_folder, user=user)

	old_parent = source_doc.folder or _HOME
	_lock_folder_rows(old_parent, folder_name, target_folder)
	source_doc = _get_folder_doc(folder_name)
	if (source_doc.folder or _HOME) != old_parent:
		frappe.throw(_("The folder location changed. Refresh and try again."), frappe.TimestampMismatchError)
	_check_permission("File", "write", doc=source_doc, user=user)
	if old_parent == target_folder:
		return {"name": folder_name, "folder": target_folder}

	_assert_unique_in_parent(source_doc.file_name, target_folder, is_folder=True)
	old_path = folder_name
	new_path = f"{target_folder}/{source_doc.file_name}"

	if frappe.db.exists("File", new_path):
		_throw(
			FolderAlreadyExistsError,
			f"An item named '{source_doc.file_name}' already exists in '{target_folder}'.",
		)

	# Collect the complete subtree before move. Keep exact prefix checks because
	# valid names may contain SQL LIKE wildcards.
	old_prefix = old_path + "/"
	descendants = sorted(
		(
			row
			for row in frappe.get_all(
				"File",
				filters={"name": ["like", f"{old_prefix}%"]},
				fields=[
					"name",
					"folder",
					"is_folder",
					"file_name",
					"owner",
					"is_private",
					"attached_to_doctype",
					"attached_to_name",
				],
				limit_page_length=0,
			)
			if row.name.startswith(old_prefix)
		),
		key=lambda row: (row.name.count("/"), row.name),
	)
	files_in_tree = [
		row
		for row in frappe.get_all(
			"File",
			filters={"folder": ["like", f"{old_path}%"]},
			fields=[
				"name",
				"folder",
				"is_folder",
				"file_name",
				"owner",
				"is_private",
				"attached_to_doctype",
				"attached_to_name",
			],
			limit_page_length=0,
		)
		if row.folder == old_path or row.folder.startswith(old_prefix)
	]
	affected = {row.name: row for row in descendants}
	affected.update({row.name: row for row in files_in_tree})
	affected[source_doc.name] = source_doc
	_lock_folder_rows(*[row.name for row in affected.values() if cint(row.is_folder)])
	_lock_file_rows(*[row.name for row in affected.values() if not cint(row.is_folder)])
	fresh_names = {
		row.name
		for row in frappe.get_all(
			"File",
			filters={"name": ["like", f"{old_prefix}%"]},
			fields=["name"],
			limit_page_length=0,
		)
		if row.name.startswith(old_prefix)
	}
	fresh_names.update(
		row.name
		for row in frappe.get_all(
			"File",
			filters={"folder": ["like", f"{old_path}%"]},
			fields=["name", "folder"],
			limit_page_length=0,
		)
		if row.folder == old_path or row.folder.startswith(old_prefix)
	)
	fresh_names.add(old_path)
	if fresh_names != set(affected):
		frappe.throw(_("The folder subtree changed. Refresh and try again."), frappe.TimestampMismatchError)
	for affected_row in affected.values():
		_check_permission("File", "write", doc=affected_row, user=user)

	deepest_relative_level = max(
		(row.name.count("/") - old_path.count("/") for row in descendants),
		default=0,
	)
	if _depth(target_folder) + 1 + deepest_relative_level > _MAX_FOLDER_DEPTH:
		_throw(InvalidFolderNameError, "Folder nesting exceeds maximum depth.")

	# Do move
	frappe.rename_doc("File", old_path, new_path, force=True, show_alert=False)
	new_prefix = new_path + "/"
	# Update descendant folder docs' names (path-based)
	for row in descendants:
		old_name = row.name
		if not old_name.startswith(old_prefix):
			continue
		suffix = old_name[len(old_prefix) :]
		expected_new_name = new_prefix + suffix
		if frappe.db.exists("File", old_name):
			frappe.rename_doc("File", old_name, expected_new_name, force=True, show_alert=False)

	# Update files whose folder is inside moved subtree
	for frow in files_in_tree:
		if not frow.folder or not frow.folder.startswith(old_path):
			continue
		old_folder = frow.folder
		if old_folder == old_path:
			new_folder = new_path
		elif old_folder.startswith(old_prefix):
			new_folder = new_prefix + old_folder[len(old_prefix) :]
		else:
			continue
		if frappe.db.exists("File", frow.name):
			frappe.db.set_value("File", frow.name, "folder", new_folder, update_modified=False)

	# Update AI Folder Settings linked records
	if frappe.db.exists("AI Folder Settings", {"folder": old_path}):
		frappe.db.set_value("AI Folder Settings", {"folder": old_path}, "folder", new_path)
	settings_rows = frappe.get_all(
		"AI Folder Settings",
		filters={"folder": ["like", f"{old_prefix}%"]},
		fields=["name", "folder"],
		limit_page_length=0,
	)
	for s in settings_rows:
		if s.folder.startswith(old_prefix):
			new_sf = new_prefix + s.folder[len(old_prefix) :]
			frappe.db.set_value("AI Folder Settings", s.name, "folder", new_sf, update_modified=False)

	frappe.clear_document_cache("File", new_path)

	_write_audit(
		"Folder Moved",
		f"Folder '{old_path}' moved from '{old_parent}' to '{target_folder}' (new path '{new_path}').",
		details={"old": old_path, "new": new_path, "target": target_folder, "owner": user},
		reference_name=new_path,
	)
	track_folder_operation("move_folder", new_path, target_folder, user, details={"old": old_path})
	return {
		"name": new_path,
		"file_name": source_doc.file_name,
		"folder": target_folder,
		"old_name": old_path,
	}


def _delete_folder_record(folder_name: str) -> None:
	"""Delete one empty File folder without bypassing external link checks.

	Folder settings and favorites are app-owned metadata whose Link fields would
	otherwise stop Frappe before the File ``on_trash`` hook can clean them.  A
	savepoint restores that metadata if any other authoritative link blocks the
	folder deletion.
	"""
	save_point = f"delete_folder_{frappe.generate_hash(length=10)}"
	frappe.db.savepoint(save_point)
	try:
		frappe.db.delete("AI Folder Settings", {"folder": folder_name})
		frappe.db.delete("AI Folder Favorite", {"folder": folder_name})
		frappe.delete_doc("File", folder_name, ignore_permissions=False)
	except Exception:
		frappe.db.rollback(save_point=save_point)
		raise
	else:
		frappe.db.release_savepoint(save_point)


def delete_folder(
	folder_name: str,
	*,
	recursive: bool = False,
	user: str | None = None,
) -> dict:
	"""Delete a folder atomically after locking and authorizing its subtree."""
	user = user or frappe.session.user
	folder_name = _normalize_folder_path(folder_name)
	if folder_name == _HOME:
		_throw(InvalidFolderNameError, "The Home folder cannot be deleted.")
	_assert_folder_exists(folder_name)
	doc = _get_folder_doc(folder_name)
	parent = doc.folder or _HOME
	prefix = folder_name + "/"

	def affected_rows() -> dict[str, Any]:
		rows = {
			row.name: row
			for row in frappe.get_all(
				"File",
				filters={"name": ["like", f"{prefix}%"]},
				fields=[
					"name",
					"folder",
					"is_folder",
					"file_name",
					"owner",
					"is_private",
					"attached_to_doctype",
					"attached_to_name",
				],
				limit_page_length=0,
			)
			if row.name.startswith(prefix)
		}
		for row in frappe.get_all(
			"File",
			filters={"folder": ["like", f"{folder_name}%"]},
			fields=[
				"name",
				"folder",
				"is_folder",
				"file_name",
				"owner",
				"is_private",
				"attached_to_doctype",
				"attached_to_name",
			],
			limit_page_length=0,
		):
			if row.folder == folder_name or row.folder.startswith(prefix):
				rows[row.name] = row
		rows[doc.name] = doc
		return rows

	affected = affected_rows()
	if len(affected) > 1 and not recursive:
		_throw(
			FolderNotEmptyError,
			f"Folder '{folder_name}' is not empty. Use recursive delete or empty it first.",
		)

	_lock_folder_rows(parent, *[row.name for row in affected.values() if cint(row.is_folder)])
	_lock_file_rows(*[row.name for row in affected.values() if not cint(row.is_folder)])
	if (frappe.db.get_value("File", folder_name, "folder") or _HOME) != parent:
		frappe.throw(_("The folder location changed. Refresh and try again."), frappe.TimestampMismatchError)
	fresh = affected_rows()
	if set(fresh) != set(affected):
		frappe.throw(_("The folder subtree changed. Refresh and try again."), frappe.TimestampMismatchError)
	for affected_row in fresh.values():
		_check_permission("File", "delete", doc=affected_row, user=user)

	files = sorted(
		(row for row in fresh.values() if not cint(row.is_folder)),
		key=lambda row: row.name,
	)
	folders = sorted(
		(row for row in fresh.values() if cint(row.is_folder)),
		key=lambda row: (row.name.count("/"), row.name),
		reverse=True,
	)
	# Keep Frappe's linked-document validation authoritative.  In particular,
	# an AI Document's stable ``source_file_record``/``folder`` links must block
	# this generic File API instead of being orphaned.  The mixed AI Document
	# tree service deletes its authorized AI Documents before their Files.
	with _mutation_savepoint("delete_folder"):
		for row in files:
			frappe.delete_doc("File", row.name, ignore_permissions=False)
		for row in folders:
			_delete_folder_record(row.name)

		_write_audit(
			"Folder Deleted",
			f"Folder '{folder_name}' deleted (recursive={recursive}).",
			details={"folder": folder_name, "recursive": recursive, "owner": user},
			# Dynamic Links reject missing targets; the deleted identity remains in
			# the immutable audit details and message.
			reference_name=None,
		)
		track_folder_operation("delete", folder_name, None, user, details={"recursive": recursive})
	return {"deleted": folder_name, "recursive": recursive}


def delete_file(file_name: str, *, user: str | None = None) -> dict:
	"""Delete a file (not a folder)."""
	user = user or frappe.session.user
	doc = _get_file_doc(file_name)
	if cint(doc.is_folder):
		_throw(InvalidFolderNameError, "Use delete_folder for folders.")
	_check_permission("File", "delete", doc=doc, user=user)
	parent = doc.folder or _HOME
	_lock_folder_rows(parent)
	_lock_file_rows(file_name)
	doc = _get_file_doc(file_name)
	if (doc.folder or _HOME) != parent:
		frappe.throw(_("The file location changed. Refresh and try again."), frappe.TimestampMismatchError)
	_check_permission("File", "delete", doc=doc, user=user)
	# Do not bypass Frappe's link checks: source Files referenced by authoritative
	# AI Documents must be removed through the document-tree lifecycle.
	with _mutation_savepoint("delete_file"):
		frappe.delete_doc("File", file_name, ignore_permissions=False)
		_write_audit(
			"File Deleted",
			f"File '{file_name}' ('{doc.file_name}') deleted.",
			details={"file": file_name, "folder": doc.folder},
			reference_name=file_name,
		)
		track_folder_operation("delete_file", file_name, doc.folder, user)
	return {"deleted": file_name}


def copy_file(
	file_name: str,
	target_folder: str,
	new_name: str | None = None,
	*,
	user: str | None = None,
	attached_to_doctype: str | None = None,
	attached_to_name: str | None = None,
	attached_to_field: str | None = None,
) -> dict:
	"""Copy a file into another folder."""
	user = user or frappe.session.user
	doc = _get_file_doc(file_name)
	if cint(doc.is_folder):
		_throw(
			InvalidFolderNameError, "Cannot copy a folder as a file. Use copy logic for folders if needed."
		)
	target_folder = _normalize_folder_path(target_folder)
	_assert_folder_exists(target_folder)
	_check_permission("File", "read", doc=doc, user=user)
	_check_write_access(target_folder, user=user)
	_lock_folder_rows(target_folder)
	_lock_file_rows(file_name)
	doc = _get_file_doc(file_name)
	_check_permission("File", "read", doc=doc, user=user)

	copy_name = _clean_name(new_name) if new_name else doc.file_name
	_assert_unique_in_parent(copy_name, target_folder, is_folder=False)

	# Use File's copy logic via creating new doc with copy_from_existing_file flag
	new_doc = frappe.new_doc("File")
	new_doc.update(
		{
			"file_name": copy_name,
			"folder": target_folder,
			"is_private": doc.is_private,
			"file_url": doc.file_url,
			"attached_to_doctype": attached_to_doctype or doc.attached_to_doctype,
			"attached_to_name": attached_to_name or doc.attached_to_name,
			"attached_to_field": attached_to_field or doc.attached_to_field,
		}
	)
	new_doc.flags.copy_from_existing_file = True
	new_doc.flags.ignore_permissions = False
	new_doc.insert()

	_write_audit(
		"File Copied",
		f"File '{file_name}' copied to '{target_folder}' as '{new_doc.name}'.",
		details={"source": file_name, "target_folder": target_folder, "new": new_doc.name},
		reference_name=new_doc.name,
	)
	track_folder_operation("copy", new_doc.name, target_folder, user, details={"source": file_name})
	return {"name": new_doc.name, "file_name": copy_name, "folder": target_folder, "source": file_name}


def set_file_folder(
	file_name: str,
	folder: str,
	*,
	user: str | None = None,
) -> dict:
	"""Re-file an already-attached file to a different folder (preserves attachment link)."""
	return move_file(file_name, folder, user=user)


def bulk_move(
	file_names: list[str],
	target_folder: str,
	*,
	user: str | None = None,
	enqueue: bool | None = None,
	_expected_state: dict[str, dict] | None = None,
) -> dict:
	"""Move files/folders as one authorized, stale-sensitive transaction."""
	if not file_names:
		_throw(InvalidFolderNameError, "No files specified for bulk move.")
	user = user or frappe.session.user
	target_folder = _normalize_folder_path(target_folder)
	_assert_folder_exists(target_folder)
	_check_write_access(target_folder, user=user)

	validated = []
	seen = set()
	for name in file_names:
		if name in seen:
			continue
		seen.add(name)
		if not frappe.db.exists("File", name):
			_throw(FileNotFoundError, f"File '{name}' does not exist.")
		doc = frappe.get_doc("File", name)
		_check_permission("File", "write", doc=doc, user=user)
		if cint(doc.is_folder):
			if name == _HOME:
				_throw(InvalidFolderNameError, "Home folder cannot be moved.")
			_assert_no_circular(name, target_folder)
		validated.append(doc)

	# Explicit selections are all authorized before nested items are pruned.
	selected_folders = {doc.name for doc in validated if cint(doc.is_folder)}
	validated = [
		doc
		for doc in validated
		if not any(
			(
				doc.name.startswith(folder + "/")
				if cint(doc.is_folder)
				else (doc.folder == folder or doc.folder.startswith(folder + "/"))
			)
			for folder in selected_folders
			if folder != doc.name
		)
	]
	selection_state = {
		doc.name: {
			"modified": str(doc.modified),
			"folder": doc.folder or _HOME,
			"is_folder": cint(doc.is_folder),
		}
		for doc in validated
	}
	# Folder roots carry a complete affected-state fingerprint, not just the
	# root's modified timestamp. A descendant inserted while a bulk job waits
	# must make the queued request stale rather than silently joining it.
	from ai_fr_hg.ai.document_tree import (
		_files_in_folders as tree_files_in_folders,
	)
	from ai_fr_hg.ai.document_tree import (
		_preflight_files as tree_preflight_files,
	)
	from ai_fr_hg.ai.document_tree import (
		_preflight_subtree as tree_preflight_subtree,
	)
	from ai_fr_hg.ai.document_tree import (
		_subtree_state as tree_subtree_state,
	)

	subtree_states = {}
	work_count = len(validated)
	for doc in validated:
		if not cint(doc.is_folder):
			continue
		subtree_folders, subtree_documents = tree_preflight_subtree(doc.name, "write")
		subtree_files = tree_files_in_folders(subtree_folders)
		tree_preflight_files(subtree_files, "write")
		subtree_states[doc.name] = tree_subtree_state(subtree_folders, subtree_documents, subtree_files)
		work_count += len(subtree_folders) + len(subtree_documents) + len(subtree_files) - 1
	state = {"selection": selection_state, "subtrees": subtree_states}
	if _expected_state is not None and state != _expected_state:
		frappe.throw(_("The bulk selection changed. Refresh and try again."), frappe.TimestampMismatchError)

	should_enqueue = work_count > 20 if enqueue is None else bool(enqueue)
	if should_enqueue:
		with _mutation_savepoint("queue_bulk_move"):
			job_id = f"ai-folder-bulk-move::{frappe.generate_hash(length=8)}"
			_write_audit(
				"Bulk Move Queued",
				f"Bulk move of {len(validated)} items to '{target_folder}' queued as {job_id}.",
				details={"count": len(validated), "target": target_folder, "job_id": job_id},
				reference_name=target_folder,
			)
			frappe.enqueue(
				"ai_fr_hg.ai.folders._bulk_move_job",
				queue="long",
				timeout=3600,
				job_id=job_id,
				deduplicate=False,
				enqueue_after_commit=True,
				file_names=[doc.name for doc in validated],
				target_folder=target_folder,
				expected_state=state,
				user=user,
			)
			return {"status": "Queued", "job_id": job_id, "count": len(validated), "target": target_folder}

	parent_rows = [target_folder]
	parent_rows.extend(doc.folder or _HOME for doc in validated)
	parent_rows.extend(doc.name for doc in validated if cint(doc.is_folder))
	_lock_folder_rows(*parent_rows)
	_lock_file_rows(*[doc.name for doc in validated if not cint(doc.is_folder)])
	locked_selection_state = {
		doc.name: {
			"modified": str(frappe.db.get_value("File", doc.name, "modified")),
			"folder": frappe.db.get_value("File", doc.name, "folder") or _HOME,
			"is_folder": cint(doc.is_folder),
		}
		for doc in validated
	}
	if locked_selection_state != selection_state:
		frappe.throw(_("The bulk selection changed. Refresh and try again."), frappe.TimestampMismatchError)

	with _mutation_savepoint("bulk_move"):
		moved = []
		for doc in validated:
			if cint(doc.is_folder):
				from ai_fr_hg.ai.document_tree import move_folder as move_tree_folder

				moved.append(
					move_tree_folder(
						doc.name,
						target_folder,
						enqueue=False,
						_expected_subtree_state=subtree_states[doc.name],
					)
				)
			else:
				moved.append(move_file(doc.name, target_folder, user=user))
		_write_audit(
			"Bulk Move Completed",
			f"Bulk move of {len(moved)} items to '{target_folder}' completed.",
			details={"moved": len(moved), "target": target_folder},
			reference_name=target_folder,
		)
	return {"status": "Completed", "moved": moved, "errors": [], "target": target_folder}


def _bulk_move_job(
	file_names: list[str],
	target_folder: str,
	expected_state: dict[str, dict],
	user: str,
) -> dict:
	"""Run a queued bulk move atomically under its requesting user."""
	previous = frappe.session.user
	if user and user != previous:
		# Security-reviewed worker boundary: the durable requester is passed into
		# every canonical service permission check and restored in finally.
		frappe.set_user(user)  # nosemgrep
	try:
		return bulk_move(
			file_names,
			target_folder,
			user=user,
			enqueue=False,
			_expected_state=expected_state,
		)
	finally:
		if user and user != previous:
			frappe.set_user(previous)  # nosemgrep


def search(
	query: str | None = None,
	*,
	folder: str | None = None,
	file_type: str | None = None,
	owner: str | None = None,
	attached_to_doctype: str | None = None,
	attached_to_name: str | None = None,
	knowledge_base: str | None = None,
	limit: int = 50,
	offset: int = 0,
) -> dict:
	"""Search and filter across hierarchy without bypassing list permissions."""
	limit = max(1, min(cint(limit) or 50, 200))
	offset = max(0, cint(offset))
	filters: dict = {}
	if folder:
		norm = _normalize_folder_path(folder)
		_assert_folder_exists(norm)
		_check_permission("File", "read", doc=_get_folder_doc(norm))
		# This legacy endpoint intentionally searches immediate children. The AI
		# Document Tree facade owns recursive, permission-safe mixed-node search.
		filters["folder"] = norm
	if file_type:
		filters["file_type"] = file_type
	if owner:
		filters["owner"] = owner
	if attached_to_doctype:
		filters["attached_to_doctype"] = attached_to_doctype
	if attached_to_name:
		filters["attached_to_name"] = attached_to_name

	# Handle query across file_name and file_url
	or_filters = None
	if query:
		text = str(query).strip()
		if text:
			or_filters = [
				["file_name", "like", f"%{text}%"],
				["folder", "like", f"%{text}%"],
				["file_url", "like", f"%{text}%"],
				["attached_to_name", "like", f"%{text}%"],
			]

	# Filter through permission-aware AI Document rows and stable File links.
	# URL/content identity must never authorize or associate a different File.
	if knowledge_base:
		docs = frappe.get_list(
			"AI Document",
			filters={"knowledge_base": knowledge_base, "source_file_record": ["is", "set"]},
			fields=["source_file_record"],
			order_by="modified desc",
			limit_page_length=max(200, limit * 4),
		)
		extra_names = [row.source_file_record for row in docs if row.source_file_record]
		if not extra_names:
			return {"query": query, "count": 0, "total": 0, "results": [], "filters": filters}
		filters["name"] = ["in", extra_names]

	results = frappe.get_list(
		"File",
		filters=filters,
		or_filters=or_filters,
		fields=[
			"name",
			"file_name",
			"is_folder",
			"folder",
			"file_url",
			"file_size",
			"file_type",
			"is_private",
			"attached_to_doctype",
			"attached_to_name",
			"owner",
			"creation",
			"modified",
		],
		order_by="modified desc",
		limit_page_length=limit,
		limit_start=offset,
	)
	# File and AI Document visibility are both enforced by their respective
	# permission-aware list queries. Keep pagination dense and avoid N+1 File
	# document loads by trusting the framework list result as the visible set.
	for row in results:
		row["ai_document"] = None
		row["ingestion_status"] = None
		ai_doc = _visible_ai_document_for_file(row)
		if ai_doc:
			row["ai_document"] = ai_doc.name
			row["ingestion_status"] = ai_doc.status

	total = _permission_aware_count("File", filters, or_filters=or_filters)
	return {"query": query, "count": len(results), "total": total, "results": results, "filters": filters}


def get_folder_info(folder_name: str, *, user: str | None = None) -> dict:
	"""Return detailed folder metadata including extension."""
	folder_name = _normalize_folder_path(folder_name)
	doc = _get_folder_doc(folder_name)
	_check_permission("File", "read", doc=doc, user=user)

	# Extended metadata
	settings = None
	if frappe.db.exists("AI Folder Settings", {"folder": folder_name}):
		settings = frappe.get_doc("AI Folder Settings", {"folder": folder_name}).as_dict()

	# Keep folder statistics on the same Frappe permission-aware query path as
	# listings. Escaping LIKE metacharacters prevents a legal ``%`` or ``_`` in
	# a folder name from widening the recursive prefix to unrelated paths.
	escaped_prefix = folder_name.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
	stats = {
		"folder_count": _permission_aware_count("File", {"folder": folder_name, "is_folder": 1}),
		"file_count": _permission_aware_count("File", {"folder": folder_name, "is_folder": 0}),
		"total_descendants": _permission_aware_count(
			"File",
			{},
			or_filters=[
				["folder", "=", folder_name],
				["folder", "like", f"{escaped_prefix}/%"],
			],
		),
	}
	size = _permission_aware_file_size({"folder": folder_name, "is_folder": 0})

	info = {
		"name": doc.name,
		"file_name": doc.file_name,
		"folder": doc.folder,
		"is_folder": True,
		"is_private": bool(doc.is_private),
		"is_home_folder": bool(doc.is_home_folder),
		"owner": doc.owner,
		"creation": doc.creation,
		"modified": doc.modified,
		"breadcrumbs": get_folder_path(folder_name),
		"settings": settings,
		"stats": {**stats, "total_size": size},
	}
	return info


def get_file_info(file_name: str, *, user: str | None = None) -> dict:
	doc = _get_file_doc(file_name)
	_check_permission("File", "read", doc=doc, user=user)
	data = {
		"name": doc.name,
		"file_name": doc.file_name,
		"folder": doc.folder,
		"is_folder": bool(cint(doc.is_folder)),
		"file_url": doc.file_url,
		"file_size": doc.file_size,
		"file_type": doc.file_type,
		"is_private": bool(doc.is_private),
		"attached_to_doctype": doc.attached_to_doctype,
		"attached_to_name": doc.attached_to_name,
		"owner": doc.owner,
		"creation": doc.creation,
		"modified": doc.modified,
		"breadcrumbs": get_breadcrumbs(doc.name),
	}
	if not data["is_folder"]:
		ai_doc = _visible_ai_document_for_file(doc, user=user)
		if ai_doc:
			data["ai_document"] = ai_doc
			data["folder_provenance"] = doc.folder
	return data


def list_favorites(user: str | None = None) -> list[dict]:
	"""List pinned/favorite folders for user."""
	user = user or frappe.session.user
	rows = frappe.get_all(
		"AI Folder Favorite",
		filters={"user": user},
		fields=["name", "folder", "creation"],
		order_by="creation desc",
	)
	# Filter by read permission and existence
	result = []
	for row in rows:
		if not frappe.db.exists("File", row.folder):
			continue
		fdoc = frappe.get_doc("File", row.folder)
		if not frappe.has_permission("File", "read", doc=fdoc, user=user):
			continue
		result.append(
			{
				"name": row.folder,
				"file_name": fdoc.file_name,
				"favorite_id": row.name,
				"creation": row.creation,
			}
		)
	return result


def add_favorite(folder: str, *, user: str | None = None) -> dict:
	user = user or frappe.session.user
	folder = _normalize_folder_path(folder)
	_assert_folder_exists(folder)
	_check_permission("File", "read", doc=_get_folder_doc(folder), user=user)
	if frappe.db.exists("AI Folder Favorite", {"user": user, "folder": folder}):
		return {"status": "already", "folder": folder}
	doc = frappe.new_doc("AI Folder Favorite")
	doc.update({"user": user, "folder": folder})
	doc.flags.ignore_permissions = True
	doc.insert(ignore_permissions=True)
	_write_audit(
		"Folder Favorited",
		f"Folder '{folder}' favorited by '{user}'.",
		details={"folder": folder, "user": user},
		reference_name=folder,
	)
	return {"status": "added", "folder": folder, "name": doc.name}


def remove_favorite(folder: str, *, user: str | None = None) -> dict:
	user = user or frappe.session.user
	folder = _normalize_folder_path(folder)
	name = frappe.db.get_value("AI Folder Favorite", {"user": user, "folder": folder}, "name")
	if not name:
		return {"status": "not_found", "folder": folder}
	frappe.delete_doc("AI Folder Favorite", name, force=True, ignore_permissions=True)
	_write_audit(
		"Folder Unfavorited",
		f"Folder '{folder}' unfavorited by '{user}'.",
		details={"folder": folder, "user": user},
		reference_name=folder,
	)
	return {"status": "removed", "folder": folder}


def get_recents(user: str | None = None, limit: int = 20) -> list[dict]:
	"""Return recently accessed/modified folders/files for user."""
	user = user or frappe.session.user
	# Query Files recently modified by user, plus audit log
	files = frappe.get_all(
		"File",
		filters={"modified_by": user},
		fields=["name", "file_name", "is_folder", "folder", "modified"],
		order_by="modified desc",
		limit_page_length=limit,
	)
	visible = []
	for f in files:
		doc = frappe.get_doc("File", f.name)
		if not frappe.has_permission("File", "read", doc=doc, user=user):
			continue
		visible.append(f)
	return visible


def get_tabs(user: str | None = None) -> list[dict]:
	"""Return saved view tabs (global shared + user). Tabs are backed by real queries."""
	user = user or frappe.session.user
	tabs = [
		{
			"id": "recent",
			"label": _("Recent"),
			"type": "filter",
			"query": {"modified_by": user},
			"icon": "clock",
		},
		{"id": "favorites", "label": _("Favorites"), "type": "favorite", "icon": "star"},
		{
			"id": "shared",
			"label": _("Public"),
			"type": "filter",
			"query": {"is_private": 0},
			"icon": "users",
		},
		{"id": "by_type", "label": _("By Type"), "type": "group", "icon": "tag"},
	]
	# Add top-level folders as tabs (backed by real folders)
	top_folders = frappe.get_all(
		"File",
		filters={"folder": _HOME, "is_folder": 1},
		fields=["name", "file_name"],
		order_by="file_name asc",
		limit_page_length=12,
	)
	for f in top_folders:
		doc = frappe.get_doc("File", f.name)
		if frappe.has_permission("File", "read", doc=doc, user=user):
			tabs.append(
				{
					"id": f"folder:{f.name}",
					"label": f.file_name,
					"type": "folder",
					"folder": f.name,
					"icon": "folder",
				}
			)
	return tabs


# ---------------------------------------------------------------------------
# File upload integration helpers
# ---------------------------------------------------------------------------


def assign_file_to_folder(
	file_name: str,
	folder: str,
	*,
	attached_to_doctype: str | None = None,
	attached_to_name: str | None = None,
	attached_to_field: str | None = None,
	user: str | None = None,
) -> dict:
	"""Assign an existing File to a folder, optionally updating attachment link.

	This is the canonical path for §4 Attachment Placement — the user chooses
	the destination folder at attach time. The folder is validated server-side.
	"""
	user = user or frappe.session.user
	doc = _get_file_doc(file_name)
	if cint(doc.is_folder):
		_throw(InvalidFolderNameError, "Cannot re-file a folder as a file.")
	# Validate folder
	target = (
		_normalize_folder_path(folder)
		if folder
		else get_default_folder(user=user, doctype=attached_to_doctype, docname=attached_to_name)
	)
	_assert_folder_exists(target)
	_check_permission("File", "write", doc=doc, user=user)
	_check_write_access(target, user=user)

	old_folder = doc.folder or _HOME
	_lock_folder_rows(old_folder, target)
	_lock_file_rows(file_name)
	doc = _get_file_doc(file_name)
	if (doc.folder or _HOME) != old_folder:
		frappe.throw(_("The file location changed. Refresh and try again."), frappe.TimestampMismatchError)
	_check_permission("File", "write", doc=doc, user=user)
	# A native FileUploader already persists its selected ``folder`` with the
	# initial File insert.  Treat a confirmation of that same destination as an
	# idempotent operation: the File itself must not be counted as a collision.
	# This is particularly important for attachment callbacks, which confirm the
	# server-side destination immediately after Frappe has created the File.
	if old_folder != target:
		_assert_unique_in_parent(doc.file_name, target, is_folder=False)

	updates: dict = {"folder": target}
	if attached_to_doctype:
		updates["attached_to_doctype"] = attached_to_doctype
	if attached_to_name:
		updates["attached_to_name"] = attached_to_name
	if attached_to_field:
		updates["attached_to_field"] = attached_to_field
	# Do not update ``is_private`` with db_set. Frappe stores public and private
	# uploads at different physical paths; changing the flag without moving the
	# file makes its existing file_url invalid. The native uploader owns that
	# choice when it writes the file, while this service owns its folder placement.

	attachment_changed = any(
		value and getattr(doc, field) != value
		for field, value in (
			("attached_to_doctype", attached_to_doctype),
			("attached_to_name", attached_to_name),
			("attached_to_field", attached_to_field),
		)
	)
	if old_folder == target and not attachment_changed:
		return {"name": file_name, "folder": target, "old_folder": old_folder, "unchanged": True}

	frappe.db.set_value("File", file_name, updates, update_modified=False)
	frappe.clear_document_cache("File", file_name)

	_write_audit(
		"File Re-filed",
		f"File '{file_name}' re-filed from '{old_folder}' to '{target}'.",
		details={"file": file_name, "from": old_folder, "to": target, "attachment": attached_to_name},
		reference_name=file_name,
	)
	track_folder_operation("assign", file_name, target, user, details={"from": old_folder})
	_update_document_folder_provenance(file_name, target)
	return {"name": file_name, "folder": target, "old_folder": old_folder, "unchanged": False}


def _update_document_folder_provenance(file_name: str, folder: str) -> None:
	"""Update AI Document's folder provenance if this File is its source_file."""
	doc = frappe.get_doc("File", file_name)
	if not doc.file_url:
		return
	# Stable File identity is authoritative.  URL fallback is restricted to
	# legacy rows that have not yet been backfilled, since multiple File records
	# may intentionally reference the same physical content after a copy.
	provenance_fields = [
		"name",
		"folder",
		"source_folder",
		"source_file",
		"source_file_record",
		"organization_revision",
	]
	meta = frappe.get_meta("AI Document")

	def sync_candidates(source_filters: dict) -> None:
		# Keyset pagination remains correct while legacy rows acquire their stable
		# source_file_record and leave the filter. Process stable rows first so a
		# backfilled legacy row cannot be visited twice.
		cursor = None
		while True:
			filters = dict(source_filters)
			if cursor:
				filters["name"] = [">", cursor]
			candidate_names = frappe.get_all(
				"AI Document",
				filters=filters,
				pluck="name",
				order_by="name asc",
				limit_page_length=400,
			)
			if not candidate_names:
				return
			cursor = candidate_names[-1]
			placeholders = ", ".join(["%s"] * len(candidate_names))
			# Only placeholder count is interpolated; every document name remains parameterized.
			frappe.db.sql(  # nosemgrep
				f"select name from `tabAI Document` where name in ({placeholders}) order by name for update",
				tuple(candidate_names),
			)
			locked_rows = frappe.get_all(
				"AI Document",
				filters={"name": ["in", candidate_names]},
				fields=provenance_fields,
				order_by="name asc",
				limit_page_length=400,
			)
			for row in locked_rows:
				# Recheck after locking: a concurrent document edit may have detached
				# the source while this File move waited for its row lock.
				if row.source_file_record != file_name and not (
					not row.source_file_record and row.source_file == doc.file_url
				):
					continue
				if meta.has_field("folder"):
					values = {"folder": folder}
					if meta.has_field("source_folder"):
						values["source_folder"] = folder
					if meta.has_field("source_file_record"):
						values["source_file_record"] = file_name
					if meta.has_field("organization_revision"):
						values["organization_revision"] = cint(row.organization_revision) + 1
					frappe.db.set_value("AI Document", row.name, values, update_modified=True)
				elif meta.has_field("source_folder"):
					frappe.db.set_value(
						"AI Document", row.name, "source_folder", folder, update_modified=True
					)
				else:
					# Compatibility fallback for pre-migration sites.
					import json

					existing = frappe.db.get_value("AI Document", row.name, "metadata")
					try:
						metadata = json.loads(existing) if existing else {}
					except Exception:
						metadata = {}
					metadata["folder"] = folder
					metadata["folder_updated_on"] = str(now_datetime())
					frappe.db.set_value(
						"AI Document", row.name, "metadata", frappe.as_json(metadata), update_modified=False
					)
				_write_audit(
					"Document Folder Provenance Updated",
					f"AI Document '{row.name}' folder provenance updated to '{folder}'.",
					details={"document": row.name, "folder": folder, "file": file_name},
					reference_doctype="AI Document",
					reference_name=row.name,
				)

	sync_candidates({"source_file_record": file_name})
	sync_candidates({"source_file_record": ["is", "not set"], "source_file": doc.file_url})


def track_folder_operation(
	action: str,
	target: str,
	source: str | None,
	user: str,
	details: dict | None = None,
) -> None:
	"""Write a reconstructable, fail-closed audit record for folder operations."""
	from ai_fr_hg.ai.logging import write_audit_log

	write_audit_log(
		action=f"Folder {action.title()}",
		category="Data",
		message=f"Folder operation '{action}' on '{target}' by '{user}'.",
		details={"action": action, "target": target, "source": source, "user": user, **(details or {})},
		reference_doctype="File",
		# File on_trash hooks run before Frappe's reverse-link validation. Never
		# create a new Dynamic Link that would make the row block its own delete;
		# immutable details retain the deleted identity.
		reference_name=(
			target if not action.startswith("delete") and frappe.db.exists("File", target) else None
		),
		raise_on_error=True,
	)
	if frappe.db.exists("AI Folder Settings", {"folder": target}):
		frappe.db.set_value(
			"AI Folder Settings",
			{"folder": target},
			{"last_operation": action, "last_operation_by": user, "last_operation_on": now_datetime()},
			update_modified=False,
		)


# ---------------------------------------------------------------------------
# Convenience helpers for UI and ingestion pipeline
# ---------------------------------------------------------------------------


def ingest_file_with_folder(
	file_url: str,
	knowledge_base: str,
	folder: str | None = None,
	title: str | None = None,
	*,
	file_record: str | None = None,
	user: str | None = None,
) -> str:
	"""Ingest a file into a knowledge base, preserving its folder provenance.

	This is the canonical entry point for files ingested via AI_FR_HG pipeline
	(§7) — ensures folder location is preserved as provenance.
	"""
	user = user or frappe.session.user
	# Resolve folder before ingestion
	target_folder = _normalize_folder_path(folder) if folder else get_default_folder(user=user)
	_assert_folder_exists(target_folder)
	# Ingestion owns deterministic File resolution and moves that exact stable
	# File identity before creating the AI Document. Do not perform a separate
	# URL lookup here, since duplicate-content File rows may share a URL.
	from ai_fr_hg.ai.ingestion import ingest_file

	return ingest_file(
		file_url=file_url,
		knowledge_base=knowledge_base,
		title=title,
		folder=target_folder,
		file_record=file_record,
	)


def validate_parent_folder_exists(folder: str | None) -> str:
	"""Public helper for input validation at API boundary."""
	return _normalize_folder_path(folder) if folder else _HOME


def create_file_with_content(
	file_name: str,
	content: bytes | str,
	folder: str | None = None,
	*,
	is_private: int = 0,
	attached_to_doctype: str | None = None,
	attached_to_name: str | None = None,
	user: str | None = None,
) -> dict:
	"""Create a new File in a specific folder (canonical path for AI outputs §2, §7).

	All AI-generated artifacts (reports, exports, agent outputs) must be
	persisted through this one function so the folder is always explicit and
	permission-checked. The caller may pass a user-chosen folder or omit it to
	use the sensible default (which the user can override next time).
	"""
	user = user or frappe.session.user
	folder = (
		_normalize_folder_path(folder)
		if folder
		else get_default_folder(user=user, doctype=attached_to_doctype, docname=attached_to_name)
	)
	_assert_folder_exists(folder)
	_check_write_access(folder, user=user)
	cleaned = _clean_name(file_name)
	_assert_unique_in_parent(cleaned, folder, is_folder=False)
	file_doc = frappe.new_doc("File")
	file_doc.update(
		{
			"file_name": cleaned,
			"folder": folder,
			"is_private": cint(is_private),
			"is_folder": 0,
			"content": content,
			"attached_to_doctype": attached_to_doctype,
			"attached_to_name": attached_to_name,
		}
	)
	file_doc.flags.ignore_permissions = False
	file_doc.insert()
	_write_audit(
		"File Created via Folder Service",
		f"File '{cleaned}' created in '{folder}' by '{user}'.",
		details={"file": file_doc.name, "folder": folder, "owner": user},
		reference_name=file_doc.name,
	)
	track_folder_operation("create_file", file_doc.name, folder, user)
	return {"name": file_doc.name, "file_name": cleaned, "folder": folder, "file_url": file_doc.file_url}


def ensure_document_folder(doctype: str, docname: str, *, user: str | None = None) -> str:
	"""Ensure a document-scoped folder exists (e.g., per-project subfolder).

	Returns the folder path ``Home/<doctype>/<docname>`` or ``Home/<doctype>`` if no docname,
	creating intermediates as needed. Permission-aware.
	"""
	user = user or frappe.session.user
	_ensure_home_exists()
	# Top-level per-Doctype folder
	top = f"Home/{doctype}"
	if not frappe.db.exists("File", top):
		try:
			create_folder(doctype, parent_folder="Home", is_private=0, user=user)
		except FolderAlreadyExistsError:
			pass
	if not docname:
		return top
	# Document-specific subfolder (scrub docname to safe folder name)
	safe = frappe.scrub(docname).replace("-", " ").title().replace(" ", "_")[:80] or docname.strip()[:80]
	safe = "".join(c for c in safe if c not in '/\\:*?"<>|').strip() or "Record"
	try:
		_safe = _clean_name(safe)
	except InvalidFolderNameError:
		_safe = "Record"
	child = f"{top}/{_safe}"
	if not frappe.db.exists("File", child):
		try:
			create_folder(_safe, parent_folder=top, is_private=0, user=user)
		except FolderAlreadyExistsError:
			pass
	return child
