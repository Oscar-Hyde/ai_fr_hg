# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

from __future__ import annotations

import frappe


def execute(filters: dict | None = None) -> tuple[list, list]:
	"""Memory Usage report showing how approved memories are performing."""
	filters = filters or {}

	conditions = []
	if filters.get("memory_type"):
		conditions.append(f"m.memory_type = {frappe.db.escape(filters['memory_type'])}")
	if filters.get("status"):
		conditions.append(f"m.status = {frappe.db.escape(filters['status'])}")
	if filters.get("scope"):
		conditions.append(f"m.scope = {frappe.db.escape(filters['scope'])}")
	if filters.get("min_usage") is not None:
		conditions.append(f"coalesce(m.usage_count, 0) >= {frappe.db.escape(str(filters['min_usage']))}")

	where = " AND ".join(conditions) if conditions else "1=1"

	data = frappe.db.sql(
		f"""
		SELECT
			m.name AS "Memory ID",
			m.content AS "Content",
			m.memory_type AS "Type",
			m.scope AS "Scope",
			m.scope_value AS "Scope Value",
			m.status AS "Status",
			m.usage_count AS "Used",
			m.helpful_count AS "Helpful",
			m.not_helpful_count AS "Not Helpful",
			m.confidence AS "Confidence",
			m.embedding_dimensions AS "Embed Dims",
			m.embedding_model AS "Embed Model",
			m.last_used_on AS "Last Used",
			m.creation AS "Created"
		FROM
			`tabAI Memory` m
		WHERE {where}
		ORDER BY m.usage_count DESC, m.last_used_on DESC NULLS LAST
		LIMIT 500
		""",
		as_dict=True,
	)

	columns = [
		{"fieldname": "Memory ID", "fieldtype": "Data", "label": "Memory ID", "width": 140},
		{"fieldname": "Content", "fieldtype": "Long Text", "label": "Content", "width": 300},
		{"fieldname": "Type", "fieldtype": "Data", "label": "Type", "width": 100},
		{"fieldname": "Scope", "fieldtype": "Data", "label": "Scope", "width": 80},
		{"fieldname": "Scope Value", "fieldtype": "Data", "label": "Scope Value", "width": 120},
		{"fieldname": "Status", "fieldtype": "Data", "label": "Status", "width": 70},
		{"fieldname": "Used", "fieldtype": "Int", "label": "Used", "width": 60},
		{"fieldname": "Helpful", "fieldtype": "Int", "label": "Helpful", "width": 60},
		{"fieldname": "Not Helpful", "fieldtype": "Int", "label": "Not Helpful", "width": 70},
		{"fieldname": "Confidence", "fieldtype": "Percent", "label": "Confidence", "width": 70},
		{"fieldname": "Embed Dims", "fieldtype": "Int", "label": "Embed Dims", "width": 70},
		{"fieldname": "Embed Model", "fieldtype": "Data", "label": "Embed Model", "width": 120},
		{"fieldname": "Last Used", "fieldtype": "Datetime", "label": "Last Used", "width": 150},
		{"fieldname": "Created", "fieldtype": "Datetime", "label": "Created", "width": 150},
	]

	return columns, data
