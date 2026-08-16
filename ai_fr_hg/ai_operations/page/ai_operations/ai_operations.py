# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Server side of the AI Operations dashboard."""

import frappe


def get_context(context):
	frappe.only_for(["AI Manager", "System Manager", "AI Auditor"])
