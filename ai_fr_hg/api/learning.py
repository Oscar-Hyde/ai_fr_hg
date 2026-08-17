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
) -> dict:
	"""Teach the platform a fact, preference, or instruction."""
	doc = learning.create_candidate(
		content=content,
		title=title,
		candidate_type=candidate_type,
		source_type=source_type,
		source_reference_doctype=source_reference_doctype,
		source_reference_name=source_reference_name,
		provenance=provenance,
		confidence=confidence,
	)
	report = learning.validate_candidate(doc)
	conflicts = learning.check_conflicts(doc)
	if conflicts["duplicates"]:
		doc.db_set("status", "Conflict")
		doc.db_set("conflict_count", len(conflicts["duplicates"]) + len(conflicts["overlaps"]))
		return {
			"candidate": doc.name,
			"status": "Conflict",
			"conflicts": conflicts,
			"valid": report["valid"],
		}
	doc.db_set("status", "Validated")
	doc.db_set("conflict_count", len(conflicts["overlaps"]))
	return {
		"candidate": doc.name,
		"status": "Validated",
		"conflicts": conflicts,
		"valid": report["valid"],
	}


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
		limit_page_length=limit,
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
		limit_page_length=limit,
	)


@frappe.whitelist()
def overview() -> dict:
	"""Summary counters for a Learning dashboard."""
	check_capability("learning")
	return {
		"candidates": frappe.db.count("AI Knowledge Candidate"),
		"pending": frappe.db.count("AI Knowledge Candidate", {"status": ["in", ["Draft", "Validated", "Conflict"]]}),
		"memories": frappe.db.count("AI Memory", {"status": "Active"}),
		"skills": frappe.db.count("AI Skill", {"enabled": 1}),
		"learning_enabled": frappe.db.get_single_value("AI Platform Settings", "learning_enabled"),
	}
