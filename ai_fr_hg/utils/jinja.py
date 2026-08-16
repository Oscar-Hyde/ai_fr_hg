# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Jinja helpers exposed to print formats, web pages and notifications."""

import frappe


def get_ai_summary(doctype: str, name: str, max_words: int = 200) -> str:
	"""Summarise any Frappe document from a template.

	Usage in a print format::

	    {{get_ai_summary("Sales Order", doc.name)}}
	"""
	from ai_fr_hg.ai.automation import _get_source_text
	from ai_fr_hg.ai.intelligence import summarize

	frappe.has_permission(doctype, "read", doc=name, throw=True)
	doc = frappe.get_doc(doctype, name)

	return summarize(
		_get_source_text(frappe._dict(source_field=None), doc),
		max_words=max_words,
		reference_doctype=doctype,
		reference_name=name,
	)


def ai_search(query: str, knowledge_base: str | None = None, limit: int = 5) -> list:
	"""Search the knowledge base from a template.

	Usage::

	    {% for hit in ai_search("refund policy") %}{{ hit.content }}{% endfor %}
	"""
	from ai_fr_hg.ai.knowledge import retrieve

	results = retrieve(
		query,
		knowledge_bases=[knowledge_base] if knowledge_base else None,
		top_k=limit,
	)
	return [r.as_dict() for r in results]
