# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Whitelisted facade for the native AI Document Tree View.

All authorization, locking, validation, transaction, and audit behavior lives in
``ai_fr_hg.ai.document_tree``.  This module only normalizes RPC payloads.
"""

from __future__ import annotations

import json

import frappe
from frappe.utils import cint

from ai_fr_hg.ai import document_tree as service


def _list(value) -> list[str]:
	if isinstance(value, str):
		try:
			value = json.loads(value)
		except (TypeError, ValueError):
			frappe.throw("nodes must be a valid JSON array", frappe.ValidationError)
	if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
		frappe.throw("nodes must be an array of tree node identifiers", frappe.ValidationError)
	if len(value) > 500:
		frappe.throw("A bulk request cannot contain more than 500 selected nodes", frappe.ValidationError)
	return value


@frappe.whitelist()
def get_children(
	doctype: str | None = None,
	parent: str | None = None,
	is_root: bool | str = False,
	knowledge_base: str | None = None,
	search: str | None = None,
	limit: int = service.DEFAULT_PAGE_LENGTH,
):
	return service.get_children(
		doctype=doctype,
		parent=parent,
		is_root=is_root,
		knowledge_base=knowledge_base,
		search=search,
		limit=limit,
	)


@frappe.whitelist()
def create_folder(folder_name: str, parent: str | None = None, expected_parent_modified: str | None = None):
	return service.create_folder(
		folder_name,
		parent,
		expected_parent_modified=expected_parent_modified,
	)


@frappe.whitelist()
def rename_node(node: str, new_name: str, expected_modified: str | None = None):
	return service.rename_node(node, new_name, expected_modified=expected_modified)


@frappe.whitelist()
def move_node(node: str, target_folder: str | None = None, expected_modified: str | None = None):
	return service.move_node(node, target_folder, expected_modified=expected_modified)


@frappe.whitelist()
def copy_node(
	node: str,
	target_folder: str | None = None,
	new_name: str | None = None,
	expected_modified: str | None = None,
):
	return service.copy_node(
		node,
		target_folder,
		new_name,
		expected_modified=expected_modified,
	)


@frappe.whitelist()
def delete_node(
	node: str,
	recursive: bool | str = False,
	expected_modified: str | None = None,
):
	return service.delete_node(
		node,
		recursive=bool(cint(recursive)),
		expected_modified=expected_modified,
	)


@frappe.whitelist()
def bulk_move_nodes(nodes, target_folder: str, enqueue: bool | str | None = None):
	return service.bulk_move_nodes(
		_list(nodes),
		target_folder,
		enqueue=None if enqueue is None else bool(cint(enqueue)),
	)


@frappe.whitelist()
def bulk_delete_nodes(nodes, recursive: bool | str = False, enqueue: bool | str | None = None):
	return service.bulk_delete_nodes(
		_list(nodes),
		recursive=bool(cint(recursive)),
		enqueue=None if enqueue is None else bool(cint(enqueue)),
	)
