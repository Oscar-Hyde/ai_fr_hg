"""Idempotent migration for ING-05: ensure extraction_warnings column exists and backfill empty."""

import frappe


def execute():
	# Ensure column exists via DB alter if needed (Frappe migrate will add from JSON, this handles direct SQL upgrades)
	try:
		# frappe.db.add_column is not public; use describe check
		cols = frappe.db.sql("SHOW COLUMNS FROM `tabAI Document`", as_dict=True)
		names = {c["Field"] for c in cols}
		if "extraction_warnings" not in names:
			frappe.db.sql("ALTER TABLE `tabAI Document` ADD COLUMN `extraction_warnings` LONGTEXT")
			frappe.db.commit()
	except Exception:
		frappe.log_error(title="ING-05 migration", message=frappe.get_traceback())
	# Backfill None -> '[]'
	try:
		frappe.db.sql(
			"UPDATE `tabAI Document` SET `extraction_warnings`='[]' WHERE `extraction_warnings` IS NULL OR `extraction_warnings`=''"
		)
		frappe.db.commit()
	except Exception:
		pass
