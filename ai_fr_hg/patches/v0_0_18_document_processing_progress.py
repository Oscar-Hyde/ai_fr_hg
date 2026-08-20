"""Backfill durable ING-06 processing state fields."""

import frappe


def execute():
	# frappe.db.table_exists() takes a DocType name and prepends "tab" itself;
	# passing "tabAI Document" would probe for "tabtabAI Document" and return
	# early on every site, silently skipping the backfill.
	if not frappe.db.table_exists("AI Document"):
		return
	columns = {
		"processing_progress": "decimal(5,2) not null default 0",
		"processing_message": "text null",
		"processing_heartbeat": "datetime(6) null",
		"cancel_requested": "tinyint(1) not null default 0",
	}
	for field, definition in columns.items():
		if not frappe.db.has_column("AI Document", field):
			frappe.db.sql(  # nosemgrep: frappe-sql-format-injection
				f"alter table `tabAI Document` add column `{field}` {definition}"
			)
	frappe.db.sql(
		"update `tabAI Document` set processing_progress = 100, processing_message = 'Indexed' "
		"where status = 'Indexed' and (processing_progress is null or processing_progress = 0)"
	)
