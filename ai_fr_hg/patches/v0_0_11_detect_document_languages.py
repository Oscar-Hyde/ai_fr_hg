# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Fill AI Document.language for already extracted documents.

The field existed but was never written. New extracts detect on ingest; this
patch labels existing text so chat does not wait for a reprocess.
"""

import frappe

from ai_fr_hg.ai.language import detect_language


def execute():
	frappe.reload_doctype("AI Document", force=True)

	rows = frappe.get_all(
		"AI Document",
		filters=[["content", "!=", ""]],
		fields=["name", "content", "language"],
		limit_page_length=2000,
	)
	for row in rows:
		if (row.language or "").strip():
			continue
		code = detect_language(row.content)
		if code:
			frappe.db.set_value("AI Document", row.name, "language", code, update_modified=False)
