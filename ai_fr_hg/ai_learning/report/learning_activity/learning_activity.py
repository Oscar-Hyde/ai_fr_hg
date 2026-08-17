# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

from __future__ import annotations

import frappe


def execute(filters: dict | None = None) -> tuple[list, list]:
	"""Learning Activity report showing candidate lifecycle information."""
	filters = filters or {}

	conditions = []
	if filters.get("status"):
		conditions.append(f"c.status = {frappe.db.escape(filters['status'])}")
	if filters.get("candidate_type"):
		conditions.append(
			f"c.candidate_type = {frappe.db.escape(filters['candidate_type'])}"
		)
	if filters.get("from_date"):
		conditions.append(f"c.creation >= {frappe.db.escape(filters['from_date'])}")
	if filters.get("to_date"):
		conditions.append(f"c.creation <= {frappe.db.escape(filters['to_date'])}")

	where = " AND ".join(conditions) if conditions else "1=1"

	data = frappe.db.sql(
		f"""
		SELECT
			c.name AS "Candidate ID:Data:140",
			c.title AS "Title:Data:200",
			c.candidate_type AS "Type:Data:100",
			c.source_type AS "Source:Data:120",
			c.user AS "Teaching User:Link/User:120",
			c.status AS "Status:Data:90",
			c.testing_status AS "Testing Status:Data:100",
			c.confidence AS "Confidence:%:70",
			c.conflict_count AS "Conflicts:Int:70",
			c.target_scope AS "Scope:Data:80",
			c.target_scope_value AS "Scope Value:Data:120",
			c.creation AS "Created:Datetime:150",
			c.approved_on AS "Approved On:Datetime:150",
			c.approved_by AS "Approved By:Link/User:120"
		FROM
			`tabAI Knowledge Candidate` c
		WHERE {where}
		ORDER BY c.creation DESC
		LIMIT 500
		""",
		as_dict=True,
	)

	columns = [
		{"fieldname": "Candidate ID", "fieldtype": "Data", "label": "Candidate ID", "width": 140},
		{"fieldname": "Title", "fieldtype": "Data", "label": "Title", "width": 200},
		{"fieldname": "Type", "fieldtype": "Data", "label": "Type", "width": 100},
		{"fieldname": "Source", "fieldtype": "Data", "label": "Source", "width": 120},
		{"fieldname": "Teaching User", "fieldtype": "Link", "options": "User", "label": "Teaching User", "width": 120},
		{"fieldname": "Status", "fieldtype": "Data", "label": "Status", "width": 90},
		{"fieldname": "Testing Status", "fieldtype": "Data", "label": "Testing Status", "width": 100},
		{"fieldname": "Confidence", "fieldtype": "Percent", "label": "Confidence", "width": 70},
		{"fieldname": "Conflicts", "fieldtype": "Int", "label": "Conflicts", "width": 70},
		{"fieldname": "Scope", "fieldtype": "Data", "label": "Scope", "width": 80},
		{"fieldname": "Scope Value", "fieldtype": "Data", "label": "Scope Value", "width": 120},
		{"fieldname": "Created", "fieldtype": "Datetime", "label": "Created", "width": 150},
		{"fieldname": "Approved On", "fieldtype": "Datetime", "label": "Approved On", "width": 150},
		{"fieldname": "Approved By", "fieldtype": "Link", "options": "User", "label": "Approved By", "width": 120},
	]

	return columns, data