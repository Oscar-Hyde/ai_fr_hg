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
through this one service (Master §3.2–§3.5, File & Folder §6).

Folder paths are Frappe-native ``File.name`` values:
  - Root is ``"Home"`` (``is_home_folder = 1``)
  - Child ``Home/Invoices`` has ``folder = "Home"`` and ``file_name = "Invoices"``
  - Deeply nested ``Home/Projects/Acme/Contracts`` chains via ``folder``

The ``File.folder`` link is the parent folder's ``name``.  ``File.file_name``
is the display name within that parent (no slashes).
"""

from __future__ import annotations

import re
from typing import Any

import frappe
from frappe import _
from frappe.utils import cint, now_datetime

from ai_fr_hg.ai.exceptions import (
	CircularFolderError,
	FileNotFoundError,
	FolderAlreadyExistsError,
	FolderError,
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
		_throw(InvalidFolderNameError, "Name cannot contain / \\ : * ? \" < > |")
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
		# Try privileged insert; Home may already exist via fixtures but db check is cheap.
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
			frappe.db.commit()
		except Exception:
			# Home creation races are benign; another worker may have created it.
			pass


def _get_file_doc(name: str):
	"""Return File doc or throw FileNotFoundError."""
	if not frappe.db.exists("File", name):
		_throw(FileNotFoundError, f"File or folder '{name}' does not exist.")
	return frappe.get_doc("File", name)


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
			_throw(FolderPermissionError, f"User {user} lacks {permission_type} permission for {doc_or_doctype}.")
		return
	# document level check
	target = doc if doc is not None else doc_or_doctype
	doctype = target.doctype if hasattr(target, "doctype") else "File"
	if not frappe.has_permission(doctype, permission_type, doc=target, user=user):
		_throw(FolderPermissionError, f"User {user} is not allowed to {permission_type} '{getattr(target, 'name', doctype)}'.")


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
	"""Best-effort audit log for folder operations."""
	try:
		from ai_fr_hg.ai.logging import write_audit_log

		write_audit_log(
			action=action,
			category="File Organization",
			message=message,
			details=details or {},
			reference_doctype=reference_doctype,
			reference_name=reference_name,
			raise_on_error=False,
		)
	except Exception:
		frappe.log_error(title=f"Folder audit failed: {action}", message=frappe.get_traceback())


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

	# Get total count.
	# Use get_list with permission already checked; frappe will also enforce permission_query_conditions if any.
	items = frappe.get_all(
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
	# Filter by read permission per item (client convenience; server will also block writes).
	visible = []
	for item in items:
		doc = frappe.get_doc("File", item.name)
		if frappe.has_permission("File", "read", doc=doc):
			# Hydrate AI ingestion status for folder view (§7 visibility)
			if not item.get("is_folder") and item.get("file_url"):
				ai_doc = frappe.db.get_value("AI Document", {"source_file": item.file_url}, ["name", "status"], as_dict=True)
				if ai_doc:
					item["ai_document"] = ai_doc.name
					item["ingestion_status"] = ai_doc.status
			visible.append(item)

	total = frappe.db.count("File", base_filters)
	return {"folder": folder, "items": visible, "total": total, "breadcrumbs": get_folder_path(folder)}


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

	return _build(root, 0) or {"name": root, "file_name": _folder_name_from_path(root), "is_folder": True, "children": []}


def get_default_folder(
	user: str | None = None,
	doctype: str | None = None,
	docname: str | None = None,
) -> str:
	"""Return a sensible default folder for an upload.

	Resolution order:
	  1. If doctype/docname provided, try per-DocType default from settings or
	     folder of the parent record's attachments.
	  2. Per-user "My Uploads" under Home (auto-created).
	  3. Home/Attachments (Frappe default).
	  4. Home
	"""
	user = user or frappe.session.user
	# Try DocType-specific default from AI Platform Settings? Check if field exists.
	try:
		storage_folder = frappe.db.get_single_value("AI Platform Settings", "storage_folder")
		if storage_folder and frappe.db.exists("File", storage_folder) and cint(frappe.db.get_value("File", storage_folder, "is_folder")):
			return _normalize_folder_path(storage_folder)
	except Exception:
		pass

	if doctype and docname:
		try:
			if frappe.db.exists(doctype, docname):
				doc = frappe.get_doc(doctype, docname)
				# If the record has a File attachment folder via File docs, use it.
				# Look for any File attached to this record, take its folder.
				files = frappe.get_all(
					"File",
					filters={"attached_to_doctype": doctype, "attached_to_name": docname},
					fields=["folder"],
					limit_page_length=1,
				)
				if files and files[0].folder and frappe.db.exists("File", files[0].folder):
					folder_doc = frappe.get_doc("File", files[0].folder)
					if cint(folder_doc.is_folder):
						return files[0].folder
				# Try per-Doctype folder: Home/<DocType>
				candidate = f"Home/{doctype}"
				if frappe.db.exists("File", candidate):
					return candidate
		except Exception:
			pass

	# Per-user My Uploads
	safe_user = frappe.scrub(user).replace(" ", "_") if user else "guest"
	candidate_user_folder = f"Home/My Uploads"
	# We create per-user subfolder under My Uploads if needed.
	# But for default, return Home/My Uploads if visible.
	try:
		_ensure_home_exists()
		if not frappe.db.exists("File", candidate_user_folder):
			# Lazy-create shared My Uploads
			try:
				create_folder("My Uploads", parent_folder=_HOME, is_private=0)
			except FolderError:
				pass
		if frappe.db.exists("File", candidate_user_folder):
			return candidate_user_folder
	except Exception:
		pass

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

	cleaned = _clean_name(folder_name)
	_assert_unique_in_parent(cleaned, parent_folder, is_folder=True)

	if _depth(parent_folder) + 1 > _MAX_FOLDER_DEPTH:
		_throw(InvalidFolderNameError, "Folder nesting exceeds maximum depth.")

	# Determine is_private from parent if not specified
	if is_private is None:
		try:
			parent_doc = frappe.get_doc("File", parent_folder)
			is_private = cint(parent_doc.is_private)
		except Exception:
			is_private = 0
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
	# Insert will trigger File autoname to set name = parent/file_name
	try:
		file_doc.insert()
	except Exception as exc:
		# Translate duplicate name exception to typed error
		if "already exists" in str(exc).lower() or "duplicate" in str(exc).lower():
			_throw(FolderAlreadyExistsError, f"An item named '{cleaned}' already exists in '{parent_folder}'.")
		raise

	# Handle extended metadata if provided
	if description or knowledge_base:
		try:
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
		except Exception:
			# Metadata failure should not block folder creation; log it.
			frappe.log_error(title="AI Folder Settings creation failed", message=frappe.get_traceback())

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
	if folder_name.rsplit("/", 1)[-1] == cleaned:
		# No change
		return {"name": folder_name, "file_name": cleaned, "folder": parent}

	_assert_unique_in_parent(cleaned, parent, is_folder=True)
	new_path = f"{parent}/{cleaned}" if parent and parent != _HOME else f"{_HOME}/{cleaned}" if parent == _HOME else cleaned
	# Ensure new_path doesn't already exist as File
	if frappe.db.exists("File", new_path):
		_throw(FolderAlreadyExistsError, f"An item named '{cleaned}' already exists in '{parent}'.")

	# Gather descendants before rename: all Files where name == folder_name or name startswith folder_name+"/" or folder == folder_name etc.
	# Frappe stores files with folder = parent path. So we need to update both File.name for folders and File.folder for children.
	old_prefix = folder_name + "/"
	descendants = frappe.get_all(
		"File",
		filters={"name": ["like", f"{old_prefix}%"]},
		fields=["name", "folder", "is_folder", "file_name"],
	)
	# Also files directly in this folder (folder == folder_name)
	direct_files = frappe.get_all(
		"File",
		filters={"folder": folder_name},
		fields=["name", "folder", "is_folder", "file_name"],
	)
	# Merge without duplicates
	all_affected = {d.name: d for d in descendants}
	for d in direct_files:
		all_affected[d.name] = d
	# Also the folder itself
	all_affected[doc.name] = doc

	# Permission check: ensure user can write to all affected? At least check parent write and source write.
	for name in all_affected:
		inner = frappe.get_doc("File", name)
		if not frappe.has_permission("File", "write", doc=inner, user=user):
			_throw(FolderPermissionError, f"User {user} cannot modify '{name}' during rename.")

	# Perform rename via SQL to avoid Frappe's autoname side-effects, then clear cache.
	# The canonical way is frappe.rename_doc, but it may not cascade child paths.
	# We'll do it manually:
	try:
		# 1. Update the folder's own File record's file_name and name.
		# File.name for folders is constructed from parent + file_name. Changing file_name updates name.
		# Use frappe.rename_doc for the top folder to preserve framework hooks.
		frappe.rename_doc("File", folder_name, new_path, force=True, show_alert=False)
		# frappe.rename_doc already updates File.name, but child folder's File.folder still points to old path.
		# Need to fix child records.
		new_prefix = new_path + "/"
		for old_name, details in list(all_affected.items()):
			if old_name == folder_name:
				continue
			# old_name is like Home/Old/Child or Home/Old/Child/Sub or file hash? Files have hash names not path-based.
			# For folders, name is path-based, so old_name startswith old_prefix.
			# For files, name is hash, but folder field is old path. So we need to handle both.
			if old_name.startswith(old_prefix):
				# This is a descendant folder (path-based name)
				suffix = old_name[len(old_prefix):]
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
						new_folder = new_prefix + old_folder[len(old_prefix):]
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
			"AI Folder Settings", filters={"folder": ["like", f"{old_prefix}%"]}, fields=["name", "folder"]
		)
		for s in settings:
			if s.folder.startswith(old_prefix):
				new_sf = new_prefix + s.folder[len(old_prefix):]
				frappe.db.set_value("AI Folder Settings", s.name, "folder", new_sf, update_modified=False)

		# Clear caches
		frappe.clear_document_cache("File", new_path)

	except Exception as exc:
		frappe.log_error(title="Folder rename failed", message=frappe.get_traceback())
		# Re-throw as typed if not already
		if isinstance(exc, FolderError):
			raise
		_throw(FolderError, f"Failed to rename folder: {exc}")

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

	if doc.folder == target_folder:
		return {"name": file_name, "folder": target_folder}

	_assert_unique_in_parent(doc.file_name, target_folder, is_folder=False)

	old_folder = doc.folder
	frappe.db.set_value("File", file_name, "folder", target_folder, update_modified=False)
	frappe.db.set_value("File", file_name, "is_private", cint(frappe.db.get_value("File", target_folder, "is_private")), update_modified=False)
	frappe.clear_document_cache("File", file_name)

	_write_audit(
		"File Moved",
		f"File '{file_name}' moved from '{old_folder}' to '{target_folder}'.",
		details={"file": file_name, "from": old_folder, "to": target_folder, "owner": user},
		reference_name=file_name,
	)
	track_folder_operation("move_file", file_name, target_folder, user, details={"from": old_folder})
	# Update folder-specific provenance for AI Document if this File is linked there.
	try:
		_update_document_folder_provenance(file_name, target_folder)
	except Exception:
		frappe.log_error(title="Document folder provenance update failed", message=frappe.get_traceback())
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

	if source_doc.folder == target_folder:
		return {"name": folder_name, "folder": target_folder}

	_assert_unique_in_parent(source_doc.file_name, target_folder, is_folder=True)

	if _depth(target_folder) + _depth(folder_name.rsplit("/", 1)[-1].count("/") if False else 1) + _depth(folder_name) > _MAX_FOLDER_DEPTH:
		# Rough depth check: target depth + 1 + descendant depth
		if _depth(target_folder) + 1 > _MAX_FOLDER_DEPTH:
			_throw(InvalidFolderNameError, "Folder nesting exceeds maximum depth.")

	old_parent = source_doc.folder
	old_path = folder_name
	new_path = f"{target_folder}/{source_doc.file_name}"

	if frappe.db.exists("File", new_path):
		_throw(FolderAlreadyExistsError, f"An item named '{source_doc.file_name}' already exists in '{target_folder}'.")

	# Collect all descendants before move
	old_prefix = old_path + "/"
	descendants = frappe.get_all(
		"File",
		filters={"name": ["like", f"{old_prefix}%"]},
		fields=["name"],
	)
	files_in_tree = frappe.get_all(
		"File",
		filters={"folder": ["like", f"{old_path}%"]},
		fields=["name", "folder"],
	)

	# Do move
	try:
		frappe.rename_doc("File", old_path, new_path, force=True, show_alert=False)
		new_prefix = new_path + "/"
		# Update descendant folder docs' names (path-based)
		for row in descendants:
			old_name = row.name
			if not old_name.startswith(old_prefix):
				continue
			suffix = old_name[len(old_prefix):]
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
				new_folder = new_prefix + old_folder[len(old_prefix):]
			else:
				continue
			if frappe.db.exists("File", frow.name):
				frappe.db.set_value("File", frow.name, "folder", new_folder, update_modified=False)

		# Update AI Folder Settings linked records
		if frappe.db.exists("AI Folder Settings", {"folder": old_path}):
			frappe.db.set_value("AI Folder Settings", {"folder": old_path}, "folder", new_path)
		settings_rows = frappe.get_all(
			"AI Folder Settings", filters={"folder": ["like", f"{old_prefix}%"]}, fields=["name", "folder"]
		)
		for s in settings_rows:
			if s.folder.startswith(old_prefix):
				new_sf = new_prefix + s.folder[len(old_prefix):]
				frappe.db.set_value("AI Folder Settings", s.name, "folder", new_sf, update_modified=False)

		frappe.clear_document_cache("File", new_path)

	except Exception as exc:
		frappe.log_error(title="Folder move failed", message=frappe.get_traceback())
		if isinstance(exc, FolderError):
			raise
		_throw(FolderError, f"Failed to move folder: {exc}")

	_write_audit(
		"Folder Moved",
		f"Folder '{old_path}' moved from '{old_parent}' to '{target_folder}' (new path '{new_path}').",
		details={"old": old_path, "new": new_path, "target": target_folder, "owner": user},
		reference_name=new_path,
	)
	track_folder_operation("move_folder", new_path, target_folder, user, details={"old": old_path})
	return {"name": new_path, "file_name": source_doc.file_name, "folder": target_folder, "old_name": old_path}


def delete_folder(
	folder_name: str,
	*,
	recursive: bool = False,
	user: str | None = None,
) -> dict:
	"""Delete a folder. If non-empty and not recursive, raise FolderNotEmptyError."""
	user = user or frappe.session.user
	folder_name = _normalize_folder_path(folder_name)
	if folder_name == _HOME:
		_throw(InvalidFolderNameError, "The Home folder cannot be deleted.")
	_assert_folder_exists(folder_name)
	doc = _get_folder_doc(folder_name)
	_check_permission("File", "delete", doc=doc, user=user)

	# Check for children
	child_folders = frappe.db.count("File", {"folder": folder_name, "is_folder": 1})
	child_files = frappe.db.count("File", {"folder": folder_name, "is_folder": 0})
	# Also folders/files nested deeper: any File where name like folder_name/% or folder like folder_name%
	nested = frappe.db.count("File", {"name": ["like", f"{folder_name}/%"]})
	total_children = child_folders + child_files + nested

	if total_children and not recursive:
		_throw(
			FolderNotEmptyError,
			f"Folder '{folder_name}' is not empty. Use recursive delete or empty it first.",
		)

	if recursive:
		# Delete all descendants first
		# Get all files/folders under this tree ordered deepest first
		all_descendants = frappe.get_all(
			"File",
			filters={"name": ["like", f"{folder_name}/%"]},
			fields=["name"],
			order_by="name desc",
		)
		for row in all_descendants:
			try:
				fdoc = frappe.get_doc("File", row.name)
				if frappe.has_permission("File", "delete", doc=fdoc, user=user):
					frappe.delete_doc("File", row.name, force=True, ignore_permissions=False)
			except Exception:
				frappe.log_error(title=f"Recursive delete failed for {row.name}", message=frappe.get_traceback())
		# Files directly in folder (hash-named files)
		direct_files = frappe.get_all(
			"File",
			filters={"folder": folder_name, "is_folder": 0},
			fields=["name"],
		)
		for row in direct_files:
			try:
				fdoc = frappe.get_doc("File", row.name)
				if frappe.has_permission("File", "delete", doc=fdoc, user=user):
					frappe.delete_doc("File", row.name, force=True, ignore_permissions=False)
			except Exception:
				frappe.log_error(title=f"Recursive delete file failed {row.name}", message=frappe.get_traceback())
		# Also remove AI Folder Settings
		frappe.db.delete("AI Folder Settings", {"folder": folder_name})
		frappe.db.delete("AI Folder Settings", {"folder": ["like", f"{folder_name}/%"]})

	try:
		frappe.delete_doc("File", folder_name, force=True, ignore_permissions=False)
	except Exception as exc:
		if isinstance(exc, FolderError):
			raise
		_throw(FolderError, f"Failed to delete folder: {exc}")

	_write_audit(
		"Folder Deleted",
		f"Folder '{folder_name}' deleted (recursive={recursive}).",
		details={"folder": folder_name, "recursive": recursive, "owner": user},
		reference_name=folder_name,
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
	frappe.delete_doc("File", file_name, force=True, ignore_permissions=False)
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
) -> dict:
	"""Copy a file into another folder."""
	user = user or frappe.session.user
	doc = _get_file_doc(file_name)
	if cint(doc.is_folder):
		_throw(InvalidFolderNameError, "Cannot copy a folder as a file. Use copy logic for folders if needed.")
	target_folder = _normalize_folder_path(target_folder)
	_assert_folder_exists(target_folder)
	_check_permission("File", "read", doc=doc, user=user)
	_check_write_access(target_folder, user=user)

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
			"attached_to_doctype": doc.attached_to_doctype,
			"attached_to_name": doc.attached_to_name,
			"attached_to_field": doc.attached_to_field,
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
) -> dict:
	"""Move multiple files/folders to target. Enqueues as background job for large batches."""
	if not file_names:
		_throw(InvalidFolderNameError, "No files specified for bulk move.")
	user = user or frappe.session.user
	target_folder = _normalize_folder_path(target_folder)
	_assert_folder_exists(target_folder)
	_check_write_access(target_folder, user=user)

	# Validate all items exist and permissions
	validated = []
	for name in file_names:
		if not frappe.db.exists("File", name):
			_throw(FileNotFoundError, f"File '{name}' does not exist.")
		doc = frappe.get_doc("File", name)
		if not frappe.has_permission("File", "write", doc=doc, user=user):
			_throw(FolderPermissionError, f"User {user} cannot move '{name}'.")
		# If folder, check circular
		if cint(doc.is_folder):
			_assert_no_circular(name, target_folder)
			if name == _HOME:
				_throw(InvalidFolderNameError, "Home folder cannot be moved.")
		validated.append(doc)

	# Decide enqueue threshold
	threshold = 20
	should_enqueue = enqueue if enqueue is not None else len(validated) > threshold

	if should_enqueue:
		job_id = f"ai-folder-bulk-move::{frappe.generate_hash(length=8)}"
		frappe.enqueue(
			"ai_fr_hg.ai.folders._bulk_move_job",
			queue="long",
			timeout=3600,
			job_id=job_id,
			deduplicate=False,
			enqueue_after_commit=True,
			file_names=[d.name for d in validated],
			target_folder=target_folder,
			user=user,
		)
		_write_audit(
			"Bulk Move Queued",
			f"Bulk move of {len(validated)} items to '{target_folder}' queued as {job_id}.",
			details={"count": len(validated), "target": target_folder, "job_id": job_id},
			reference_name=target_folder,
		)
		return {"status": "Queued", "job_id": job_id, "count": len(validated), "target": target_folder}

	# Immediate move
	moved = []
	errors = []
	for doc in validated:
		try:
			if cint(doc.is_folder):
				res = move_folder(doc.name, target_folder, user=user)
			else:
				res = move_file(doc.name, target_folder, user=user)
			moved.append(res)
		except FolderError as exc:
			errors.append({"file": doc.name, "error": str(exc)})
		except Exception as exc:
			errors.append({"file": doc.name, "error": str(exc)})

	_write_audit(
		"Bulk Move Completed",
		f"Bulk move of {len(validated)} items to '{target_folder}' completed: {len(moved)} moved, {len(errors)} errors.",
		details={"moved": len(moved), "errors": errors, "target": target_folder},
		reference_name=target_folder,
	)
	return {"status": "Completed", "moved": moved, "errors": errors, "target": target_folder}


def _bulk_move_job(file_names: list[str], target_folder: str, user: str) -> dict:
	"""Worker for bulk move (enqueued)."""
	# Restore user context
	previous = frappe.session.user
	changed = False
	if user and user != previous:
		frappe.set_user(user)
		changed = True
	try:
		results = []
		errors = []
		for name in file_names:
			try:
				if not frappe.db.exists("File", name):
					errors.append({"file": name, "error": "Not found"})
					continue
				doc = frappe.get_doc("File", name)
				if cint(doc.is_folder):
					res = move_folder(name, target_folder, user=user)
				else:
					res = move_file(name, target_folder, user=user)
				results.append(res)
			except Exception as exc:
				errors.append({"file": name, "error": str(exc)})
				frappe.log_error(title="Bulk move item failed", message=f"{name}: {exc}\n{frappe.get_traceback()}")
		frappe.db.commit()
		return {"moved": len(results), "errors": errors}
	finally:
		if changed:
			frappe.set_user(previous)


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
	"""Search and filter across hierarchy by name, folder path, file type, uploader, date, linked doc, knowledge status."""
	filters: dict = {}
	if folder:
		norm = _normalize_folder_path(folder)
		# Include files directly in folder OR in descendant folders (recursive)
		# For simplicity, direct folder filter; caller can implement recursive via folder like.
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

	# If knowledge_base filter, we need to join via AI Document? For now, handle via File's attached_to.
	# AI Document has source_file = File.file_url, and knowledge_base link.
	# We'll support knowledge_base by filtering Files that are linked to AI Documents in that KB.
	extra_names = None
	if knowledge_base:
		docs = frappe.get_all(
			"AI Document", filters={"knowledge_base": knowledge_base}, fields=["source_file"], limit_page_length=limit
		)
		urls = [d.source_file for d in docs if d.source_file]
		if urls:
			# Find Files with those file_urls
			files = frappe.get_all("File", filters={"file_url": ["in", urls]}, pluck="name")
			extra_names = files
			# If no files match, return empty
			if not extra_names:
				return {"query": query, "count": 0, "results": [], "filters": filters}
			filters["name"] = ["in", extra_names]
		else:
			return {"query": query, "count": 0, "results": [], "filters": filters}

	results = frappe.get_all(
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
	# Permission filter
	visible = []
	for row in results:
		doc = frappe.get_doc("File", row.name)
		if frappe.has_permission("File", "read", doc=doc):
			# Hydrate AI Document linkage if any
			row["ai_document"] = None
			row["ingestion_status"] = None
			if row.file_url:
				ai_doc = frappe.db.get_value(
					"AI Document", {"source_file": row.file_url}, ["name", "status"], as_dict=True
				)
				if ai_doc:
					row["ai_document"] = ai_doc.name
					row["ingestion_status"] = ai_doc.status
			visible.append(row)

	total = frappe.db.count("File", filters)
	return {"query": query, "count": len(visible), "total": total, "results": visible, "filters": filters}


def get_folder_info(folder_name: str, *, user: str | None = None) -> dict:
	"""Return detailed folder metadata including extension."""
	folder_name = _normalize_folder_path(folder_name)
	doc = _get_folder_doc(folder_name)
	_check_permission("File", "read", doc=doc, user=user)

	# Extended metadata
	settings = None
	if frappe.db.exists("AI Folder Settings", {"folder": folder_name}):
		settings = frappe.get_doc("AI Folder Settings", {"folder": folder_name}).as_dict()

	stats = {
		"folder_count": frappe.db.count("File", {"folder": folder_name, "is_folder": 1}),
		"file_count": frappe.db.count("File", {"folder": folder_name, "is_folder": 0}),
		"total_descendants": frappe.db.count("File", {"name": ["like", f"{folder_name}/%"]}) + frappe.db.count("File", {"folder": ["like", f"{folder_name}%"]}),
	}
	# Compute total size
	size = frappe.db.sql("select coalesce(sum(file_size),0) from `tabFile` where folder=%s", (folder_name,))[0][0] or 0

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
	if not data["is_folder"] and doc.file_url:
		ai_doc = frappe.db.get_value("AI Document", {"source_file": doc.file_url}, ["name", "status", "knowledge_base"], as_dict=True)
		if ai_doc:
			data["ai_document"] = ai_doc
			# Find folder provenance
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
		{"id": "recent", "label": _("Recent"), "type": "filter", "query": {"modified_by": user}, "icon": "clock"},
		{"id": "favorites", "label": _("Favorites"), "type": "favorite", "icon": "star"},
		{"id": "shared", "label": _("Shared with me"), "type": "filter", "query": {"is_private": 0}, "icon": "users"},
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
	target = _normalize_folder_path(folder) if folder else get_default_folder(user=user, doctype=attached_to_doctype, docname=attached_to_name)
	_assert_folder_exists(target)
	_check_permission("File", "write", doc=doc, user=user)
	_check_write_access(target, user=user)

	old_folder = doc.folder
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
	# Also ensure is_private follows folder.
	updates["is_private"] = cint(frappe.db.get_value("File", target, "is_private"))

	attachment_changed = any(
		value and getattr(doc, field) != value
		for field, value in (
			("attached_to_doctype", attached_to_doctype),
			("attached_to_name", attached_to_name),
			("attached_to_field", attached_to_field),
		)
	)
	privacy_changed = cint(getattr(doc, "is_private", 0)) != updates["is_private"]
	if old_folder == target and not attachment_changed and not privacy_changed:
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
	try:
		_update_document_folder_provenance(file_name, target)
	except Exception:
		frappe.log_error(title="Folder provenance update failed", message=frappe.get_traceback())
	return {"name": file_name, "folder": target, "old_folder": old_folder, "unchanged": False}


def ensure_file_in_folder(
	file_url: str | None,
	folder: str | None,
	*,
	attached_to_doctype: str | None = None,
	attached_to_name: str | None = None,
	user: str | None = None,
) -> str | None:
	"""Given a file_url, ensure its File record lives in `folder` (or default)."""
	if not file_url:
		return None
	name = frappe.db.get_value("File", {"file_url": file_url}, "name")
	if not name:
		return None
	target = _normalize_folder_path(folder) if folder else get_default_folder(user=user, doctype=attached_to_doctype, docname=attached_to_name)
	try:
		assign_file_to_folder(name, target, attached_to_doctype=attached_to_doctype, attached_to_name=attached_to_name, user=user)
	except FolderError:
		frappe.log_error(title="ensure_file_in_folder failed", message=frappe.get_traceback())
	return name


def _update_document_folder_provenance(file_name: str, folder: str) -> None:
	"""Update AI Document's folder provenance if this File is its source_file."""
	doc = frappe.get_doc("File", file_name)
	if not doc.file_url:
		return
	ai_docs = frappe.get_all("AI Document", filters={"source_file": doc.file_url}, fields=["name"], limit_page_length=10)
	for row in ai_docs:
		try:
			# Store folder as Data or Link? Check if AI Document has folder field; if not, store in metadata JSON or skip.
			if frappe.get_meta("AI Document").has_field("folder"):
				frappe.db.set_value("AI Document", row.name, "folder", folder, update_modified=False)
			elif frappe.get_meta("AI Document").has_field("source_folder"):
				frappe.db.set_value("AI Document", row.name, "source_folder", folder, update_modified=False)
			else:
				# Fallback: store in metadata JSON provenance
				existing = frappe.db.get_value("AI Document", row.name, "metadata")
				import json

				try:
					meta = json.loads(existing) if existing else {}
				except Exception:
					meta = {}
				meta["folder"] = folder
				meta["folder_updated_on"] = str(now_datetime())
				frappe.db.set_value("AI Document", row.name, "metadata", frappe.as_json(meta), update_modified=False)
			# Also update audit
			_write_audit(
				"Document Folder Provenance Updated",
				f"AI Document '{row.name}' folder provenance updated to '{folder}'.",
				details={"document": row.name, "folder": folder, "file": file_name},
				reference_doctype="AI Document",
				reference_name=row.name,
			)
		except Exception:
			frappe.log_error(title="AI Document folder provenance update failed", message=frappe.get_traceback())


def track_folder_operation(
	action: str,
	target: str,
	source: str | None,
	user: str,
	details: dict | None = None,
) -> None:
	"""Write a reconstructable audit record for folder ops (Master §23)."""
	try:
		from ai_fr_hg.ai.logging import write_audit_log

		write_audit_log(
			action=f"Folder {action.title()}",
			category="File Organization",
			message=f"Folder operation '{action}' on '{target}' by '{user}'.",
			details={"action": action, "target": target, "source": source, "user": user, **(details or {})},
			reference_doctype="File",
			reference_name=target,
			raise_on_error=False,
		)
		# Also bump AI Folder Settings last operation if exists
		if frappe.db.exists("AI Folder Settings", {"folder": target}):
			try:
				frappe.db.set_value(
					"AI Folder Settings",
					{"folder": target},
					{"last_operation": action, "last_operation_by": user, "last_operation_on": now_datetime()},
					update_modified=False,
				)
			except Exception:
				pass
	except Exception:
		pass


# ---------------------------------------------------------------------------
# Convenience helpers for UI and ingestion pipeline
# ---------------------------------------------------------------------------


def ingest_file_with_folder(
	file_url: str,
	knowledge_base: str,
	folder: str | None = None,
	title: str | None = None,
	*,
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
	# Ensure File doc is in target folder
	ensure_file_in_folder(file_url, target_folder, user=user)
	# Create AI Document with folder metadata
	from ai_fr_hg.ai.ingestion import ingest_file

	document_name = ingest_file(
		file_url=file_url,
		knowledge_base=knowledge_base,
		title=title,
	)
	# Store folder provenance on AI Document
	try:
		meta = frappe.get_meta("AI Document")
		if meta.has_field("folder"):
			frappe.db.set_value("AI Document", document_name, "folder", target_folder, update_modified=False)
		elif meta.has_field("source_folder"):
			frappe.db.set_value("AI Document", document_name, "source_folder", target_folder, update_modified=False)
	except Exception:
		frappe.log_error(title="AI Document folder assignment failed", message=frappe.get_traceback())
	return document_name


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
	folder = _normalize_folder_path(folder) if folder else get_default_folder(user=user, doctype=attached_to_doctype, docname=attached_to_name)
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
	safe = "".join(c for c in safe if c not in "/\\:*?\"<>|").strip() or "Record"
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

