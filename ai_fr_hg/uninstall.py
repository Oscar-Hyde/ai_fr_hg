# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Uninstall cleanup."""

import frappe


def before_uninstall() -> None:
	"""Remove queued jobs and cached state belonging to this app."""
	frappe.cache.delete_value("ai_fr_hg:automation_rules")

	# Drop scheduled job entries so the scheduler stops calling into the app.
	for job in frappe.get_all("Scheduled Job Type", filters={"method": ["like", "ai_fr_hg.%"]}, pluck="name"):
		frappe.delete_doc("Scheduled Job Type", job, force=True, ignore_permissions=True)

	frappe.db.commit()  # nosemgrep: frappe-manual-commit
