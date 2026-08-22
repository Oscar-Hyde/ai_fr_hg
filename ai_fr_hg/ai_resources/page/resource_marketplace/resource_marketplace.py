# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Server side of the AI Resource Marketplace."""

import frappe
from frappe import _


def get_context(context):
	roles = frappe.get_roles()
	if frappe.session.user == "Administrator" or any(
		role in ("AI Manager", "System Manager", "AI User", "AI Auditor") for role in roles
	):
		return
	frappe.throw(_("You do not have access to the AI Resource Marketplace."), frappe.PermissionError)
