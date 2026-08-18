# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Map legacy learning audit values to the canonical Data taxonomy."""

import frappe


def execute():
	if frappe.db.table_exists("AI Audit Log") and frappe.db.has_column("AI Audit Log", "category"):
		frappe.db.set_value("AI Audit Log", {"category": "Learning"}, "category", "Data")
