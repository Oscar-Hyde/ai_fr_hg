# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Folder organization: ensure DocTypes and fields exist and seed default folders.

This is the one-time migration for the File & Folder Organization feature
(Master §27, File & Folder §11). It is idempotent and safe to re-run.
"""

import frappe


def execute():
	# Ensure new DocTypes' modules are correct (in case they were imported as Custom)
	for doctype, module in (
		("AI Folder Settings", "AI Core"),
		("AI Folder Favorite", "AI Core"),
	):
		if frappe.db.exists("DocType", doctype):
			current = frappe.db.get_value("DocType", doctype, "module")
			if current != module:
				frappe.db.set_value("DocType", doctype, "module", module)

	# Ensure AI Document has folder fields (migrate will add columns, but this fixes old sites where Custom? was off)
	# No explicit column check needed; DocType sync via migrate handles schema.

	# Seed default folder structure if not present (same as after_install)
	try:
		from ai_fr_hg.install import create_default_folders

		create_default_folders()
	except Exception:
		frappe.log_error(
			title="Folder organization patch: default folders failed", message=frappe.get_traceback()
		)

	# Backfill folder provenance for existing AI Documents where source_file points to a File
	try:
		docs = frappe.get_all(
			"AI Document",
			filters={"source_type": "File", "source_file": ["!=", ""], "folder": ["in", ["", None]]},
			fields=["name", "source_file"],
			limit_page_length=500,
		)
		for row in docs:
			if not row.source_file:
				continue
			file_name = frappe.db.get_value("File", {"file_url": row.source_file}, "name")
			if file_name:
				folder = frappe.db.get_value("File", file_name, "folder")
				if folder and frappe.db.exists("File", folder):
					frappe.db.set_value(
						"AI Document",
						row.name,
						{"folder": folder, "source_folder": folder},
						update_modified=False,
					)
	except Exception:
		frappe.log_error(title="Folder organization patch: backfill failed", message=frappe.get_traceback())
