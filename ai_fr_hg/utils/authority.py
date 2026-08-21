# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Canonical worker-identity restoration.

Frappe background jobs run as Administrator. Durable request authority is
the original user, restored for the duration of the work and always put
back afterwards — including on failure. Ingestion, pipelines, folders, and
translation must use this helper rather than ad-hoc ``set_user`` blocks.
"""

from __future__ import annotations

from contextlib import contextmanager

import frappe
from frappe import _


def assert_valid_authority(user: str | None) -> str:
	"""Reject Guest, missing, and disabled users as worker authority."""
	user = user or ""
	if not user or user == "Guest" or not frappe.db.exists("User", user):
		frappe.throw(_("A valid authenticated processing user is required."), frappe.PermissionError)
	if user != "Administrator" and not frappe.db.get_value("User", user, "enabled"):
		frappe.throw(_("Processing user {0} is disabled.").format(user), frappe.PermissionError)
	return user


@contextmanager
def as_user(user: str):
	"""Temporarily run as ``user`` and restore the previous session user."""
	user = assert_valid_authority(user)
	previous = frappe.session.user
	if previous != user:
		# Security-reviewed worker boundary: callers re-check DocType access
		# after the switch; the previous identity is always restored.
		frappe.set_user(user)  # nosemgrep
	try:
		yield
	finally:
		if frappe.session.user != previous:
			frappe.set_user(previous)  # nosemgrep
