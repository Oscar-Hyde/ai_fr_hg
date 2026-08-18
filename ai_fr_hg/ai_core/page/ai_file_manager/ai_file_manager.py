# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Server side of the File Manager page."""

import frappe
from frappe import _


def get_context(context):
	if frappe.session.user == "Guest":
		frappe.throw(_("Please sign in to use the File Manager."), frappe.PermissionError)
