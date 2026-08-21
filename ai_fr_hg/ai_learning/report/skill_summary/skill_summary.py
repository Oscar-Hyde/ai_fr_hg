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
	"""Skill Summary report showing all learned procedures.

	LEARN-01: registered as a Script Report so these filters actually run.
	"""
	_authorise("AI Skill")
	filters = normalise_report_filters("Skill Summary", filters)
	skill = frappe.qb.DocType("AI Skill")
	query = (
		frappe.qb.from_(skill)
		.select(
			skill.name.as_("Skill ID"),
			skill.skill_name.as_("Skill Name"),
			skill.skill_type.as_("Type"),
			skill.scope.as_("Scope"),
			skill.scope_value.as_("Scope Value"),
			skill.enabled.as_("Enabled"),
			skill.version.as_("Version"),
			skill.usage_count.as_("Used"),
			skill.source_user.as_("Source User"),
			skill.description.as_("Description"),
			skill.creation.as_("Created"),
		)
		.orderby(skill.usage_count, order=frappe.qb.desc)
		.orderby(skill.creation, order=frappe.qb.desc)
		.limit(REPORT_ROW_LIMIT)
	)
	if filters.get("skill_type"):
		query = query.where(skill.skill_type == filters["skill_type"])
	if filters.get("enabled") is not None:
		query = query.where(skill.enabled == filters["enabled"])
	if filters.get("scope"):
		query = query.where(skill.scope == filters["scope"])

	columns = [
		{"fieldname": "Skill ID", "fieldtype": "Data", "label": _("Skill ID"), "width": 140},
		{"fieldname": "Skill Name", "fieldtype": "Data", "label": _("Skill Name"), "width": 200},
		{"fieldname": "Type", "fieldtype": "Data", "label": _("Type"), "width": 100},
		{"fieldname": "Scope", "fieldtype": "Data", "label": _("Scope"), "width": 80},
		{"fieldname": "Scope Value", "fieldtype": "Data", "label": _("Scope Value"), "width": 120},
		{"fieldname": "Enabled", "fieldtype": "Check", "label": _("Enabled"), "width": 60},
		{"fieldname": "Version", "fieldtype": "Int", "label": _("Version"), "width": 60},
		{"fieldname": "Used", "fieldtype": "Int", "label": _("Used"), "width": 60},
		{
			"fieldname": "Source User",
			"fieldtype": "Link",
			"options": "User",
			"label": _("Source User"),
			"width": 120,
		},
		{
			"fieldname": "Description",
			"fieldtype": "Small Text",
			"label": _("Description"),
			"width": 250,
		},
		{"fieldname": "Created", "fieldtype": "Datetime", "label": _("Created"), "width": 150},
	]

	return columns, query.run(as_dict=True)
