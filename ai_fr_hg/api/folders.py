# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Whitelisted thin API boundary for folder/file organization.

Each method authenticates → authorizes → validates → delegates to the
canonical ``ai_fr_hg.ai.folders`` service → returns canonical result.
No business logic lives here (Master §8, File & Folder §6).
"""

from __future__ import annotations

import json

import frappe
from frappe import _
from frappe.utils import cint


def _coerce_list(value) -> list[str]:
	if isinstance(value, str):
		try:
			parsed = json.loads(value)
			if isinstance(parsed, list):
				return [str(v) for v in parsed if v]
		except ValueError:
			return [v.strip() for v in value.split(",") if v.strip()]
		return [value]
	if isinstance(value, (list, tuple)):
		return [str(v) for v in value if v]
	return []


@frappe.whitelist()
def create_folder(
	folder_name: str,
	parent_folder: str | None = None,
	is_private: int | None = None,
	description: str | None = None,
	knowledge_base: str | None = None,
) -> dict:
	"""Create a folder under parent_folder."""
	from ai_fr_hg.ai.folders import create_folder as service_create

	frappe.has_permission("File", "create", throw=True)
	if parent_folder:
		# Validate parent exists via service (it will throw typed error)
		pass
	return service_create(
		folder_name=folder_name,
		parent_folder=parent_folder,
		is_private=cint(is_private) if is_private is not None else None,
		description=description,
		knowledge_base=knowledge_base,
		user=frappe.session.user,
	)


@frappe.whitelist()
def rename_folder(folder_name: str, new_name: str) -> dict:
	from ai_fr_hg.ai.document_tree import rename_folder as tree_rename_folder

	return tree_rename_folder(folder=folder_name, new_name=new_name)


@frappe.whitelist()
def rename_file(file_name: str, new_name: str) -> dict:
	from ai_fr_hg.ai.folders import rename_file as service_rename_file

	return service_rename_file(file_name=file_name, new_name=new_name, user=frappe.session.user)


@frappe.whitelist()
def move_file(file_name: str, target_folder: str) -> dict:
	from ai_fr_hg.ai.folders import move_file as service_move

	return service_move(file_name=file_name, target_folder=target_folder, user=frappe.session.user)


@frappe.whitelist()
def move_folder(folder_name: str, target_folder: str) -> dict:
	from ai_fr_hg.ai.document_tree import move_folder as tree_move_folder

	return tree_move_folder(folder=folder_name, target_folder=target_folder)


@frappe.whitelist()
def delete_folder(folder_name: str, recursive: int = 0) -> dict:
	from ai_fr_hg.ai.folders import delete_folder as service_delete

	return service_delete(folder_name=folder_name, recursive=bool(cint(recursive)), user=frappe.session.user)


@frappe.whitelist()
def delete_file(file_name: str) -> dict:
	from ai_fr_hg.ai.folders import delete_file as service_delete_file

	return service_delete_file(file_name=file_name, user=frappe.session.user)


@frappe.whitelist()
def copy_file(file_name: str, target_folder: str, new_name: str | None = None) -> dict:
	from ai_fr_hg.ai.folders import copy_file as service_copy

	return service_copy(
		file_name=file_name, target_folder=target_folder, new_name=new_name, user=frappe.session.user
	)


@frappe.whitelist()
def set_file_folder(
	file_name: str,
	folder: str,
	attached_to_doctype: str | None = None,
	attached_to_name: str | None = None,
	attached_to_field: str | None = None,
) -> dict:
	"""Re-file an existing file (attachment placement §4)."""
	from ai_fr_hg.ai.folders import assign_file_to_folder

	return assign_file_to_folder(
		file_name=file_name,
		folder=folder,
		attached_to_doctype=attached_to_doctype,
		attached_to_name=attached_to_name,
		attached_to_field=attached_to_field,
		user=frappe.session.user,
	)


@frappe.whitelist()
def bulk_move(file_names: str | list, target_folder: str, enqueue: int | None = None) -> dict:
	"""Move many files/folders; enqueues as background job when large (§7, §10)."""
	from ai_fr_hg.ai.folders import bulk_move as service_bulk

	names = _coerce_list(file_names)
	return service_bulk(
		file_names=names,
		target_folder=target_folder,
		user=frappe.session.user,
		enqueue=bool(cint(enqueue)) if enqueue is not None else None,
	)


@frappe.whitelist()
def list_folder_contents(
	folder: str | None = None,
	include_files: int = 1,
	include_folders: int = 1,
	limit: int = 50,
	offset: int = 0,
	search_text: str | None = None,
	order_by: str | None = None,
) -> dict:
	from ai_fr_hg.ai.folders import list_folder_contents as service_list

	return service_list(
		folder=folder,
		include_files=bool(cint(include_files)),
		include_folders=bool(cint(include_folders)),
		limit=cint(limit) or 50,
		offset=cint(offset) or 0,
		search_text=search_text,
		order_by=order_by or "file_name asc",
	)


@frappe.whitelist()
def get_tree(root: str | None = None, max_depth: int = 4, include_files: int = 0) -> dict:
	from ai_fr_hg.ai.folders import get_tree as service_tree

	return service_tree(
		root=root,
		max_depth=cint(max_depth) or 4,
		include_files=bool(cint(include_files)),
		user=frappe.session.user,
	)


@frappe.whitelist()
def get_breadcrumbs(file_or_folder: str) -> list:
	from ai_fr_hg.ai.folders import get_breadcrumbs as service_crumb

	return service_crumb(file_or_folder=file_or_folder)


@frappe.whitelist()
def get_folder_info(folder_name: str) -> dict:
	from ai_fr_hg.ai.folders import get_folder_info as service_info

	return service_info(folder_name=folder_name, user=frappe.session.user)


@frappe.whitelist()
def get_file_info(file_name: str) -> dict:
	from ai_fr_hg.ai.folders import get_file_info as service_file_info

	return service_file_info(file_name=file_name, user=frappe.session.user)


@frappe.whitelist()
def search(
	query: str | None = None,
	folder: str | None = None,
	file_type: str | None = None,
	limit: int = 50,
) -> dict:
	from ai_fr_hg.ai.folders import search as service_search

	return service_search(
		query=query,
		folder=folder,
		file_type=file_type,
		limit=cint(limit) or 50,
	)


@frappe.whitelist()
def list_favorites() -> list:
	from ai_fr_hg.ai.folders import list_favorites as service_fav

	return service_fav(user=frappe.session.user)


@frappe.whitelist()
def add_favorite(folder: str) -> dict:
	from ai_fr_hg.ai.folders import add_favorite as service_add

	return service_add(folder=folder, user=frappe.session.user)


@frappe.whitelist()
def remove_favorite(folder: str) -> dict:
	from ai_fr_hg.ai.folders import remove_favorite as service_remove

	return service_remove(folder=folder, user=frappe.session.user)


@frappe.whitelist()
def get_recents(limit: int = 20) -> list:
	from ai_fr_hg.ai.folders import get_recents as service_recent

	return service_recent(user=frappe.session.user, limit=cint(limit) or 20)


@frappe.whitelist()
def get_tabs() -> list:
	from ai_fr_hg.ai.folders import get_tabs as service_tabs

	return service_tabs(user=frappe.session.user)


@frappe.whitelist()
def get_default_folder(doctype: str | None = None, docname: str | None = None) -> dict:
	"""Return the default folder for the native uploader's current context."""
	from ai_fr_hg.ai.folders import get_breadcrumbs, get_default_folder as service_default

	folder = service_default(user=frappe.session.user, doctype=doctype, docname=docname)
	return {"folder": folder, "breadcrumbs": get_breadcrumbs(folder) if folder else []}


@frappe.whitelist()
def upload_file_with_folder(
	file_url: str | None = None,
	folder: str | None = None,
	attached_to_doctype: str | None = None,
	attached_to_name: str | None = None,
	attached_to_field: str | None = None,
	is_private: int | None = None,
) -> dict:
	"""Thin wrapper used by the augmented FileUploader (§4).

	The actual File record is already created by Frappe's upload endpoint;
	this call re-files it into the user-selected folder server-side.
	"""
	if not file_url:
		frappe.throw(_("File URL is required."))
	name = frappe.db.get_value("File", {"file_url": file_url}, "name")
	if not name:
		frappe.throw(_("File with URL {0} not found.").format(file_url))
	folder = folder or __import__("ai_fr_hg.ai.folders", fromlist=["get_default_folder"]).get_default_folder(
		user=frappe.session.user, doctype=attached_to_doctype, docname=attached_to_name
	)
	from ai_fr_hg.ai.folders import assign_file_to_folder

	return assign_file_to_folder(
		file_name=name,
		folder=folder,
		attached_to_doctype=attached_to_doctype,
		attached_to_name=attached_to_name,
		attached_to_field=attached_to_field,
		user=frappe.session.user,
	)
