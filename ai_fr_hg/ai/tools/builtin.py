# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Built-in tool handlers.

Each handler runs with the calling user's permissions and returns plain,
JSON-serialisable data that a model can reason about.
"""

import frappe
from frappe import _
from frappe.utils import cint, now_datetime


def search_knowledge_base(query: str, knowledge_base: str | None = None, limit: int = 5) -> dict:
	"""Semantic search across the knowledge bases the user may read."""
	from ai_fr_hg.ai.knowledge import retrieve

	results = retrieve(
		query,
		knowledge_bases=[knowledge_base] if knowledge_base else None,
		top_k=min(cint(limit) or 5, 20),
	)
	return {
		"query": query,
		"count": len(results),
		"results": [
			{
				"document": r.document_title,
				"heading": r.heading,
				"page": r.page_number,
				"score": round(r.score, 4),
				"content": r.content[:2000],
			}
			for r in results
		],
	}


def get_document(doctype: str, name: str, fields: list | str | None = None) -> dict:
	"""Fetch a single Frappe document the user is allowed to read."""
	frappe.has_permission(doctype, "read", doc=name, throw=True)
	doc = frappe.get_doc(doctype, name)

	if fields:
		if isinstance(fields, str):
			fields = [f.strip() for f in fields.split(",") if f.strip()]
		return {field: doc.get(field) for field in fields if doc.meta.has_field(field) or field == "name"}

	return doc.as_dict(no_nulls=True, no_default_fields=False)


def list_documents(
	doctype: str,
	filters: dict | str | None = None,
	fields: list | str | None = None,
	limit: int = 20,
	order_by: str | None = None,
) -> list:
	"""List records of a DocType, respecting the user's permissions."""
	import json

	frappe.has_permission(doctype, "read", throw=True)

	if isinstance(filters, str):
		try:
			filters = json.loads(filters)
		except ValueError:
			filters = {}
	if isinstance(fields, str):
		fields = [f.strip() for f in fields.split(",") if f.strip()]

	return frappe.get_list(
		doctype,
		filters=filters or {},
		fields=fields or ["name"],
		limit_page_length=min(cint(limit) or 20, 100),
		order_by=order_by or "modified desc",
	)


def count_documents(doctype: str, filters: dict | str | None = None) -> dict:
	"""Count records of a DocType matching optional filters."""
	import json

	frappe.has_permission(doctype, "read", throw=True)
	if isinstance(filters, str):
		try:
			filters = json.loads(filters)
		except ValueError:
			filters = {}

	return {"doctype": doctype, "count": frappe.db.count(doctype, filters or {})}


def run_report(report: str, filters: dict | str | None = None) -> dict:
	"""Execute a query report and return its columns and rows."""
	import json

	from frappe.desk.query_report import run

	frappe.has_permission("Report", "read", doc=report, throw=True)
	if isinstance(filters, str):
		try:
			filters = json.loads(filters)
		except ValueError:
			filters = {}

	result = run(report, filters=filters or {}, ignore_prepared_report=True)
	return {
		"columns": [c.get("label") if isinstance(c, dict) else c for c in (result.get("columns") or [])],
		"rows": (result.get("result") or [])[:100],
	}


def get_document_text(document: str, max_characters: int = 8000) -> dict:
	"""Return the extracted text of an `AI Document`."""
	frappe.has_permission("AI Document", "read", doc=document, throw=True)
	doc = frappe.get_doc("AI Document", document)
	return {
		"document": doc.name,
		"title": doc.title,
		"status": doc.status,
		"summary": doc.summary,
		"content": (doc.content or "")[: cint(max_characters) or 8000],
		"truncated": len(doc.content or "") > (cint(max_characters) or 8000),
	}


def current_datetime() -> dict:
	"""Return the site's current date and time."""
	from frappe.utils import get_system_timezone

	now = now_datetime()
	return {
		"datetime": str(now),
		"date": str(now.date()),
		"time": str(now.time()),
		"timezone": get_system_timezone(),
	}
