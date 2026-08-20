# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Align folder naming and the storage-folder setting with real behavior.

- "My Uploads" was always shared, never per-user: rename the legacy folder to
  "Shared Uploads" through the canonical folder service (descendant paths are
  updated by the service). Rows are preserved; only the name changes.
- `storage_folder` was a free-text Data value that could diverge from the
  folder tree. Normalize legacy short values to the canonical File identity
  "Home/AI Platform" and create the folder when it is missing, so the Link
  field always references a real folder.
"""

import frappe


def _ensure_folder(name: str, parent: str) -> None:
	from ai_fr_hg.ai.folders import create_folder

	if not frappe.db.exists("File", parent):
		# Frappe core seeds Home on every site; never recurse into it.
		frappe.log_error(title="Folder patch", message=f"Parent folder '{parent}' does not exist.")
		return
	if frappe.db.exists("File", f"{parent}/{name}"):
		return
	create_folder(name, parent_folder=parent, is_private=0, user="Administrator")


def execute() -> None:
	from ai_fr_hg.ai.folders import rename_folder

	if frappe.db.exists("File", "Home/My Uploads") and not frappe.db.exists("File", "Home/Shared Uploads"):
		try:
			rename_folder("Home/My Uploads", "Shared Uploads", user="Administrator")
		except Exception:
			frappe.log_error(title="Shared Uploads rename failed", message=frappe.get_traceback())

	storage = frappe.db.get_single_value("AI Platform Settings", "storage_folder")
	if storage and not frappe.db.exists("File", storage):
		# Legacy short value like "AI Platform" (or a deleted folder): fall back
		# to the canonical seeded path and make sure the folder exists.
		if storage != "Home/AI Platform":
			frappe.db.set_single_value("AI Platform Settings", "storage_folder", "Home/AI Platform")
		_ensure_folder("AI Platform", "Home")
