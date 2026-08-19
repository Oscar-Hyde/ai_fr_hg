# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Stop cutting local chat turns off at the previous 90s default.

The 90-second budget was introduced so reverse proxies would not return a bare
504. On a local bench there is usually no such proxy, and a cold local model
often needs longer than 90s for its first token. Sites still on the shipped
default are switched to 0 (unlimited). An administrator who chose another
value is left alone.
"""

import frappe
from frappe.utils import cint

PREVIOUS_DEFAULT = 90


def execute():
	frappe.reload_doctype("AI Platform Settings", force=True)

	current = frappe.db.get_single_value("AI Platform Settings", "max_turn_seconds")
	if current is None or cint(current) != PREVIOUS_DEFAULT:
		return

	frappe.db.set_single_value("AI Platform Settings", "max_turn_seconds", 0)
