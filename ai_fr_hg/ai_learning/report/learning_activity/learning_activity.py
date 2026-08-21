# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

from __future__ import annotations

import frappe
from frappe import _

from ai_fr_hg.ai.learning_utils import REPORT_ROW_LIMIT, normalise_report_filters


def _authorise(ref_doctype: str) -> None:
	"""Service-layer authorization, not only the Report wrapper's role check.

	Frappe checks the Report's roles before calling ``execute``. This repeats
	the check against the referenced DocType so the function is safe if it is
	ever invoked directly (background job, another report, a test).
	"""
	if not frappe.has_permission(ref_doctype, "report"):
		frappe.throw(
			_("You are not permitted to run reports on {0}.").format(_(ref_doctype)),
			frappe.PermissionError,
		)


def execute(filters: dict | None = None) -> tuple[list, list]:
	"""Learning Activity report showing candidate lifecycle information.

	LEARN-01: this is a **Script Report**. It used to be registered as a Query
	Report with static SQL, so Frappe ran the SQL and never called this
	function - every filter in the sidebar was inert. Filters are validated
	against a declared contract before they reach the query builder.
	"""
	_authorise("AI Knowledge Candidate")
	filters = normalise_report_filters("Learning Activity", filters)
	candidate = frappe.qb.DocType("AI Knowledge Candidate")
	query = (
		frappe.qb.from_(candidate)
		.select(
			candidate.name.as_("Candidate ID"),
			candidate.title.as_("Title"),
			candidate.candidate_type.as_("Type"),
			candidate.source_type.as_("Source"),
			candidate.user.as_("Teaching User"),
			candidate.status.as_("Status"),
			candidate.testing_status.as_("Testing Status"),
			candidate.confidence.as_("Confidence"),
			candidate.conflict_count.as_("Conflicts"),
			candidate.target_scope.as_("Scope"),
			candidate.target_scope_value.as_("Scope Value"),
			candidate.creation.as_("Created"),
			candidate.approved_on.as_("Approved On"),
			candidate.approved_by.as_("Approved By"),
		)
		.orderby(candidate.creation, order=frappe.qb.desc)
		.limit(REPORT_ROW_LIMIT)
	)
	if filters.get("status"):
		query = query.where(candidate.status == filters["status"])
	if filters.get("candidate_type"):
		query = query.where(candidate.candidate_type == filters["candidate_type"])
	if filters.get("from_date"):
		query = query.where(candidate.creation >= filters["from_date"])
	if filters.get("to_date"):
		query = query.where(candidate.creation <= filters["to_date"])

	columns = [
		{"fieldname": "Candidate ID", "fieldtype": "Data", "label": _("Candidate ID"), "width": 140},
		{"fieldname": "Title", "fieldtype": "Data", "label": _("Title"), "width": 200},
		{"fieldname": "Type", "fieldtype": "Data", "label": _("Type"), "width": 100},
		{"fieldname": "Source", "fieldtype": "Data", "label": _("Source"), "width": 120},
		{
			"fieldname": "Teaching User",
			"fieldtype": "Link",
			"options": "User",
			"label": _("Teaching User"),
			"width": 120,
		},
		{"fieldname": "Status", "fieldtype": "Data", "label": _("Status"), "width": 90},
		{
			"fieldname": "Testing Status",
			"fieldtype": "Data",
			"label": _("Testing Status"),
			"width": 100,
		},
		{"fieldname": "Confidence", "fieldtype": "Percent", "label": _("Confidence"), "width": 70},
		{"fieldname": "Conflicts", "fieldtype": "Int", "label": _("Conflicts"), "width": 70},
		{"fieldname": "Scope", "fieldtype": "Data", "label": _("Scope"), "width": 80},
		{"fieldname": "Scope Value", "fieldtype": "Data", "label": _("Scope Value"), "width": 120},
		{"fieldname": "Created", "fieldtype": "Datetime", "label": _("Created"), "width": 150},
		{"fieldname": "Approved On", "fieldtype": "Datetime", "label": _("Approved On"), "width": 150},
		{
			"fieldname": "Approved By",
			"fieldtype": "Link",
			"options": "User",
			"label": _("Approved By"),
			"width": 120,
		},
	]

	return columns, query.run(as_dict=True)
