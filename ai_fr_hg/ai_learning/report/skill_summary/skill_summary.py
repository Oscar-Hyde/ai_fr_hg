# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

from __future__ import annotations

import frappe


def execute(filters: dict | None = None) -> tuple[list, list]:
	"""Skill Summary report showing all learned procedures."""
	filters = filters or {}

	conditions = []
	if filters.get("skill_type"):
		conditions.append(f"s.skill_type = {frappe.db.escape(filters['skill_type'])}")
	if filters.get("enabled") is not None:
		conditions.append(f"s.enabled = {int(filters['enabled'])}")
	if filters.get("scope"):
		conditions.append(f"s.scope = {frappe.db.escape(filters['scope'])}")

	where = " AND ".join(conditions) if conditions else "1=1"

	data = frappe.db.sql(
		f"""
		SELECT
			s.name AS "Skill ID",
			s.skill_name AS "Skill Name",
			s.skill_type AS "Type",
			s.scope AS "Scope",
			s.scope_value AS "Scope Value",
			s.enabled AS "Enabled",
			s.version AS "Version",
			s.usage_count AS "Used",
			s.source_user AS "Source User",
			s.description AS "Description",
			s.creation AS "Created"
		FROM
			`tabAI Skill` s
		WHERE {where}
		ORDER BY s.usage_count DESC, s.creation DESC
		LIMIT 500
		""",
		as_dict=True,
	)

	columns = [
		{"fieldname": "Skill ID", "fieldtype": "Data", "label": "Skill ID", "width": 140},
		{"fieldname": "Skill Name", "fieldtype": "Data", "label": "Skill Name", "width": 200},
		{"fieldname": "Type", "fieldtype": "Data", "label": "Type", "width": 100},
		{"fieldname": "Scope", "fieldtype": "Data", "label": "Scope", "width": 80},
		{"fieldname": "Scope Value", "fieldtype": "Data", "label": "Scope Value", "width": 120},
		{"fieldname": "Enabled", "fieldtype": "Check", "label": "Enabled", "width": 60},
		{"fieldname": "Version", "fieldtype": "Int", "label": "Version", "width": 60},
		{"fieldname": "Used", "fieldtype": "Int", "label": "Used", "width": 60},
		{
			"fieldname": "Source User",
			"fieldtype": "Link",
			"options": "User",
			"label": "Source User",
			"width": 120,
		},
		{"fieldname": "Description", "fieldtype": "Small Text", "label": "Description", "width": 250},
		{"fieldname": "Created", "fieldtype": "Datetime", "label": "Created", "width": 150},
	]

	return columns, data
