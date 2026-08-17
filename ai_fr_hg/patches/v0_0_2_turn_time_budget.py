# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Give existing sites a chat turn time budget.

Without a budget a slow turn can exceed the reverse proxy's timeout and return
a bare ``504 Gateway Time-out``, losing the answer and the error alike. A
DocType `default` only applies to newly created records, so a Single that
already exists needs its value set explicitly here.
"""

import frappe
from frappe.utils import cint

DEFAULT_TURN_SECONDS = 90


def execute():
	frappe.reload_doctype("AI Platform Settings", force=True)

	if cint(frappe.db.get_single_value("AI Platform Settings", "max_turn_seconds")):
		return  # already configured; respect the administrator's choice

	# Stay under the per-request timeout so the budget binds before the socket
	# does, while leaving room for a genuinely slow first token.
	request_timeout = cint(frappe.db.get_single_value("AI Platform Settings", "request_timeout"))
	budget = min(DEFAULT_TURN_SECONDS, request_timeout) if request_timeout else DEFAULT_TURN_SECONDS

	frappe.db.set_single_value("AI Platform Settings", "max_turn_seconds", budget)
