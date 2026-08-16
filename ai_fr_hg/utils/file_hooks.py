# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""File upload integration.

When a file is attached to an `AI Document` the platform starts processing it
automatically, so a user can drag a PDF onto a record and get a searchable,
indexed document with no further action.
"""

import frappe


def on_file_upload(doc, method: str | None = None) -> None:
	"""Kick off ingestion for files attached to an AI Document."""
	if doc.attached_to_doctype != "AI Document" or not doc.attached_to_name:
		return
	if not frappe.db.get_single_value("AI Platform Settings", "auto_process_documents"):
		return

	document = doc.attached_to_name
	if not frappe.db.exists("AI Document", document):
		return

	current = frappe.db.get_value("AI Document", document, ["source_file", "status"], as_dict=True)
	if current.source_file or current.status not in ("Draft", "Queued"):
		return

	frappe.db.set_value(
		"AI Document",
		document,
		{"source_file": doc.file_url, "source_type": "File", "status": "Queued"},
		update_modified=False,
	)

	from ai_fr_hg.ai.ingestion import enqueue_processing

	enqueue_processing(document)
