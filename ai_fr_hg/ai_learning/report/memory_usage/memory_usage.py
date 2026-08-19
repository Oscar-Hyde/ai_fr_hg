# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import cint


def execute(filters: dict | None = None) -> tuple[list, list]:
	"""Memory Usage report showing how approved memories are performing."""
	filters = filters or {}
	memory = frappe.qb.DocType("AI Memory")
	query = (
		frappe.qb.from_(memory)
		.select(
			memory.name.as_("Memory ID"),
			memory.content.as_("Content"),
			memory.memory_type.as_("Type"),
			memory.scope.as_("Scope"),
			memory.scope_value.as_("Scope Value"),
			memory.status.as_("Status"),
			memory.usage_count.as_("Used"),
			memory.helpful_count.as_("Helpful"),
			memory.not_helpful_count.as_("Not Helpful"),
			memory.confidence.as_("Confidence"),
			memory.embedding_dimensions.as_("Embed Dims"),
			memory.embedding_model.as_("Embed Model"),
			memory.last_used_on.as_("Last Used"),
			memory.creation.as_("Created"),
		)
		.orderby(memory.usage_count, order=frappe.qb.desc)
		.orderby(memory.last_used_on, order=frappe.qb.desc)
		.limit(500)
	)
	if filters.get("memory_type"):
		query = query.where(memory.memory_type == filters["memory_type"])
	if filters.get("status"):
		query = query.where(memory.status == filters["status"])
	if filters.get("scope"):
		query = query.where(memory.scope == filters["scope"])
	if filters.get("min_usage") is not None:
		query = query.where(memory.usage_count >= max(0, cint(filters["min_usage"])))

	columns = [
		{"fieldname": "Memory ID", "fieldtype": "Data", "label": _("Memory ID"), "width": 140},
		{"fieldname": "Content", "fieldtype": "Long Text", "label": _("Content"), "width": 300},
		{"fieldname": "Type", "fieldtype": "Data", "label": _("Type"), "width": 100},
		{"fieldname": "Scope", "fieldtype": "Data", "label": _("Scope"), "width": 80},
		{"fieldname": "Scope Value", "fieldtype": "Data", "label": _("Scope Value"), "width": 120},
		{"fieldname": "Status", "fieldtype": "Data", "label": _("Status"), "width": 70},
		{"fieldname": "Used", "fieldtype": "Int", "label": _("Used"), "width": 60},
		{"fieldname": "Helpful", "fieldtype": "Int", "label": _("Helpful"), "width": 60},
		{"fieldname": "Not Helpful", "fieldtype": "Int", "label": _("Not Helpful"), "width": 70},
		{"fieldname": "Confidence", "fieldtype": "Percent", "label": _("Confidence"), "width": 70},
		{"fieldname": "Embed Dims", "fieldtype": "Int", "label": _("Embed Dims"), "width": 70},
		{"fieldname": "Embed Model", "fieldtype": "Data", "label": _("Embed Model"), "width": 120},
		{"fieldname": "Last Used", "fieldtype": "Datetime", "label": _("Last Used"), "width": 150},
		{"fieldname": "Created", "fieldtype": "Datetime", "label": _("Created"), "width": 150},
	]

	return columns, query.run(as_dict=True)
