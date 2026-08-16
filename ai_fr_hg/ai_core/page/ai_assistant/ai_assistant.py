# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Server side of the AI Assistant page. The heavy lifting lives in
`ai_fr_hg.api.chat`; this module only guards page access."""

import frappe
from frappe import _


def get_context(context):
	if frappe.session.user == "Guest":
		frappe.throw(_("Please sign in to use the AI Assistant."), frappe.PermissionError)
