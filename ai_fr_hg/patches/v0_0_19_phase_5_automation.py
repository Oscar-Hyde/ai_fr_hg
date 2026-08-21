# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Idempotent defaults for Phase 5 automation, pipeline schedule, and tasks."""

from __future__ import annotations

import frappe
from frappe.utils import now_datetime


def execute():
	if frappe.db.has_column("AI Automation Rule", "coalesce_events"):
		frappe.db.sql(
			"""
			update `tabAI Automation Rule`
			set coalesce_events = 1
			where coalesce_events is null
			"""
		)

	if frappe.db.has_column("AI Pipeline", "misfire_policy"):
		frappe.db.sql(
			"""
			update `tabAI Pipeline`
			set misfire_policy = 'Run Once'
			where trigger_type = 'Scheduled' and (misfire_policy is null or misfire_policy = '')
			"""
		)
	if frappe.db.has_column("AI Pipeline", "next_run_on") and frappe.db.has_column(
		"AI Pipeline", "schedule_cron"
	):
		try:
			from croniter import croniter
		except ImportError:
			croniter = None
		if croniter:
			now = now_datetime()
			rows = frappe.get_all(
				"AI Pipeline",
				filters={"enabled": 1, "trigger_type": "Scheduled", "next_run_on": ["is", "not set"]},
				fields=["name", "schedule_cron"],
			)
			for row in rows:
				if not row.schedule_cron:
					continue
				try:
					nxt = croniter(row.schedule_cron, now).get_next(type(now))
				except Exception:
					continue
				frappe.db.set_value("AI Pipeline", row.name, "next_run_on", nxt, update_modified=False)

	if frappe.db.has_column("AI Task", "requested_by"):
		frappe.db.sql(
			"""
			update `tabAI Task`
			set requested_by = owner
			where requested_by is null or requested_by = ''
			"""
		)
	if frappe.db.has_column("AI Task", "requires_approval"):
		frappe.db.sql(
			"""
			update `tabAI Task`
			set requires_approval = 0
			where requires_approval is null
			"""
		)

	# Existing Approved rows were never a real authorization boundary; leave them.
