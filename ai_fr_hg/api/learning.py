# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Whitelisted endpoints for the Learning Loop.

Teaching, approval and observation are all reachable from the Desk or a custom
script. Approval and rejection are restricted to AI Manager / System Manager
(they delegate to :mod:`ai_fr_hg.ai.learning`, which enforces that).
"""

import frappe
from frappe.utils import cint

from ai_fr_hg.ai import learning
from ai_fr_hg.ai.governance import check_capability


@frappe.whitelist()
def teach(
	content: str,
	title: str | None = None,
	candidate_type: str | None = None,
	source_type: str = "Explicit Teaching",
	source_reference_doctype: str | None = None,
	source_reference_name: str | None = None,
	provenance: str | None = None,
	confidence: float = 0.0,
	target_scope: str | None = None,
	target_scope_value: str | None = None,
) -> dict:
	"""Teach through the canonical governed learning service."""
	return learning.teach(
		content=content,
		title=title,
		candidate_type=candidate_type,
		source_type=source_type,
		source_reference_doctype=source_reference_doctype,
		source_reference_name=source_reference_name,
		provenance=provenance,
		confidence=confidence,
		target_scope=target_scope,
		target_scope_value=target_scope_value,
	)


@frappe.whitelist()
def approve_candidate(candidate: str, notes: str | None = None) -> dict:
	"""Approve a candidate and promote it to a memory or skill."""
	return learning.approve_candidate(candidate, notes=notes)


@frappe.whitelist()
def reject_candidate(candidate: str, notes: str | None = None) -> dict:
	"""Reject a candidate so it is never learned."""
	return learning.reject_candidate(candidate, notes=notes)


@frappe.whitelist()
def list_candidates(status: str | None = None) -> list:
	"""Candidates the caller may review, optionally filtered by status."""
	filters = {}
	if status:
		filters["status"] = status
	return frappe.get_list(
		"AI Knowledge Candidate",
		filters=filters,
		fields=[
			"name",
			"title",
			"content",
			"candidate_type",
			"source_type",
			"user",
			"status",
			"confidence",
			"target_scope",
			"target_scope_value",
			"testing_status",
			"conflict_count",
			"creation",
		],
		order_by="creation desc",
		limit_page_length=100,
	)


@frappe.whitelist()
def list_memories(status: str = "Active", limit: int = 200) -> list:
	"""Active (or archived) memories, most used first."""
	return frappe.get_list(
		"AI Memory",
		filters={"status": status},
		fields=[
			"name",
			"content",
			"memory_type",
			"scope",
			"scope_value",
			"source_candidate",
			"source_user",
			"confidence",
			"usage_count",
			"helpful_count",
			"not_helpful_count",
			"last_used_on",
			"creation",
		],
		order_by="usage_count desc, creation desc",
		limit_page_length=min(max(cint(limit) or 200, 1), 500),
	)


@frappe.whitelist()
def list_skills(enabled: int = 1, limit: int = 100) -> list:
	"""Known skills, optionally only the enabled ones."""
	return frappe.get_list(
		"AI Skill",
		filters={"enabled": enabled} if cint(enabled) else {},
		fields=[
			"name",
			"skill_name",
			"description",
			"instructions",
			"skill_type",
			"scope",
			"source_candidate",
			"source_user",
			"enabled",
			"version",
			"usage_count",
		],
		order_by="usage_count desc, creation desc",
		limit_page_length=min(max(cint(limit) or 100, 1), 500),
	)


def _visible_count(doctype: str, filters: dict | None = None) -> int:
	"""Count through ``get_list`` so row-level learning scopes are enforced."""
	rows = frappe.get_list(
		doctype,
		filters=filters or {},
		# Dict form: SQL function strings fail the engine's field validation.
		fields=[{"COUNT": "*", "as": "total"}],
		limit_page_length=1,
	)
	return cint(rows[0].total) if rows else 0


@frappe.whitelist()
def overview() -> dict:
	"""Summary counters limited to records visible to the caller."""
	check_capability("learning")
	return {
		"candidates": _visible_count("AI Knowledge Candidate"),
		"pending": _visible_count(
			"AI Knowledge Candidate", {"status": ["in", ["Draft", "Validated", "Conflict"]]}
		),
		"memories": _visible_count("AI Memory", {"status": "Active"}),
		"skills": _visible_count("AI Skill", {"enabled": 1}),
		"learning_enabled": frappe.db.get_single_value("AI Platform Settings", "learning_enabled"),
	}
