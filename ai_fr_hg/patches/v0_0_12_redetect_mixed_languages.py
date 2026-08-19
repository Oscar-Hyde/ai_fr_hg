# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Re-detect AI Document.language with mixed English / Arabic / Hebrew support.

v0_0_11 wrote a single winner. Mixed files were stored as only `ar` or only
`en`. Re-run detection so a document that combines scripts is labelled
`en,ar` or `en,ar,he`.
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
		code = detect_language(row.content)
		if code and code != (row.language or "").strip():
			frappe.db.set_value("AI Document", row.name, "language", code, update_modified=False)
