# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Map the retired folder audit category to the native audit taxonomy."""

import frappe


def execute():
	if frappe.db.table_exists("AI Audit Log") and frappe.db.has_column("AI Audit Log", "category"):
		frappe.db.set_value("AI Audit Log", {"category": "File Organization"}, "category", "Data")
