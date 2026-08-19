# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""The Learning Loop.

The platform can be *taught*. A human corrects an answer, writes an explicit
instruction, or the system captures feedback, and that becomes a **knowledge
candidate**. The candidate is validated for provenance, tested for conflicts
against the existing memory/skill store, then - with a controlled approval
gate - becomes a persistent **AI Memory** or **AI Skill**. Those are injected
into future turns so the model behaves better next time, and the outcome of
that behaviour is observed (usage, feedback) and fed back into the loop.

Stages mapped onto code:

    User teaches ──► create_candidate()
    Knowledge candidate
    Validate provenance  ──► validate_candidate()
    Test against existing data  ──► check_conflicts()
    Approved knowledge   ──► approve_candidate()
    Memory / skill update ──► _promote_to_memory() / _promote_to_skill()
    Future AI behaviour  ──► recall() injected into the agent system prompt
    Observe result       ──► observe_feedback() + usage tracking
    ───────────────────────────────► back to the top
"""

import frappe
from frappe import _
from frappe.utils import cint, now_datetime

from ai_fr_hg.ai import learning_utils
from ai_fr_hg.ai.exceptions import AIError, QuotaExceededError
from ai_fr_hg.ai.governance import check_capability
from ai_fr_hg.ai.logging import write_audit_log
from ai_fr_hg.ai.vector import encode_vector, normalize


class LearningError(AIError):
	"""Raised for invalid teaching input that the UI can correct inline."""


def _settings():
	return frappe.get_cached_doc("AI Platform Settings")


def learning_enabled() -> bool:
	return bool(cint(_settings().learning_enabled))


VALID_SOURCE_TYPES = {
	"Explicit Teaching",
	"Chat Correction",
	"Feedback",
	"Document",
	"Tool Result",
	"Automation",
}
VALID_SCOPES = {"Global", "User", "Role", "Agent"}
REFERENCE_REQUIRED_SOURCES = {"Chat Correction", "Document", "Tool Result", "Automation"}
LEARNING_MANAGER_ROLES = {"AI Manager", "System Manager"}
VALID_FEEDBACK_REASONS = {"", "Correction", "Missing Information", "Incorrect Information"}


def _is_learning_manager(user: str | None = None) -> bool:
	user = user or frappe.session.user
	return user == "Administrator" or bool(set(frappe.get_roles(user)).intersection(LEARNING_MANAGER_ROLES))


def _default_scope(candidate_type: str, source_type: str, user: str) -> tuple[str, str | None]:
	"""Choose a least-privilege scope for teaching that omitted one."""
	if not _is_learning_manager():
		return "User", user
	if candidate_type in {"Preference", "Feedback"} or source_type in {"Chat Correction", "Feedback"}:
		return "User", user
	return "Global", None


def _validate_scope(scope: str, value: str | None, teaching_user: str) -> tuple[bool, str]:
	scope = scope or "Global"
	value = (value or "").strip() or None
	if scope not in VALID_SCOPES:
		return False, _("Target scope is invalid.")
	if scope == "Global":
		return True, _("Global scope is valid.")
	if not value:
		return False, _("Target Scope Value is required for a non-global scope.")
	if scope == "User" and not frappe.db.exists("User", value):
		return False, _("Target user does not exist.")
	if scope == "Role" and not frappe.db.exists("Role", value):
		return False, _("Target role does not exist.")
	if scope == "Agent" and not frappe.db.exists("AI Agent", value):
		return False, _("Target agent does not exist.")
	if not _is_learning_manager():
		if scope != "User" or value != teaching_user:
			return False, _("Only AI Managers may teach Global, Role, Agent, or another user's scope.")
	return True, _("Target scope is valid.")


def _validate_reference(
	doctype: str | None,
	name: str | None,
	source_type: str,
	user: str | None = None,
) -> tuple[bool, str]:
	"""Validate a provenance link, including the source user's read authority."""
	doctype = (doctype or "").strip() or None
	name = (name or "").strip() or None
	if bool(doctype) != bool(name):
		return False, _("Source DocType and Source Name must be provided together.")
	if not doctype:
		if source_type in REFERENCE_REQUIRED_SOURCES:
			return False, _("This source type requires an originating record.")
		return True, _("No originating record is required for this source type.")
	if not frappe.db.exists("DocType", doctype) or not frappe.db.exists(doctype, name):
		return False, _("The originating record does not exist.")
	if user and not frappe.has_permission(doctype, "read", doc=name, user=user):
		return False, _("The teaching user is not authorized to read the originating record.")
	return True, _("Originating record exists and is readable by the teaching user.")


# ---------------------------------------------------------------------------
# Stage 1 + 2: user teaches → knowledge candidate
# ---------------------------------------------------------------------------


def create_candidate(
	content: str,
	title: str | None = None,
	candidate_type: str | None = None,
	source_type: str = "Explicit Teaching",
	source_reference_doctype: str | None = None,
	source_reference_name: str | None = None,
	provenance: str | None = None,
	confidence: float = 0.0,
	user: str | None = None,
	target_scope: str | None = None,
	target_scope_value: str | None = None,
) -> dict:
	"""Record a piece of teaching as an ``AI Knowledge Candidate``.

	The record remains inert until it completes validation, conflict testing,
	and the configured approval policy. ``user`` attributes an internal source;
	non-managers cannot use it to impersonate another teaching user.
	"""
	check_capability("learning")
	if not learning_enabled():
		frappe.throw(_("The Learning Loop is disabled in AI Platform Settings."), exc=LearningError)

	content = (content or "").strip()
	if not content:
		frappe.throw(_("Teaching content cannot be empty."), exc=LearningError)

	candidate_type = candidate_type or learning_utils.classify_candidate(content)
	if candidate_type not in learning_utils.VALID_CANDIDATE_TYPES:
		frappe.throw(_("Unsupported candidate type {0}.").format(candidate_type), exc=LearningError)
	if source_type not in VALID_SOURCE_TYPES:
		frappe.throw(_("Unsupported source type {0}.").format(source_type), exc=LearningError)

	actor = frappe.session.user
	teaching_user = user or actor
	if teaching_user != actor and not _is_learning_manager(actor):
		frappe.throw(_("You cannot attribute teaching to another user."), exc=LearningError)
	if not frappe.db.exists("User", teaching_user):
		frappe.throw(_("Teaching user {0} does not exist.").format(teaching_user), exc=LearningError)

	if not target_scope:
		target_scope, target_scope_value = _default_scope(candidate_type, source_type, teaching_user)
	scope_ok, scope_message = _validate_scope(target_scope, target_scope_value, teaching_user)
	if not scope_ok:
		frappe.throw(scope_message, exc=LearningError)

	reference_ok, reference_message = _validate_reference(
		source_reference_doctype,
		source_reference_name,
		source_type,
		user=teaching_user,
	)
	if not reference_ok:
		frappe.throw(reference_message, exc=LearningError)

	settings = _settings()
	doc = frappe.new_doc("AI Knowledge Candidate")
	doc.update(
		{
			"title": title or content[:140],
			"content": content,
			"candidate_type": candidate_type,
			"source_type": source_type,
			"source_reference_doctype": source_reference_doctype,
			"source_reference_name": source_reference_name,
			# Free-form caller text is contextual evidence, never authoritative
			# attribution. The DocType controller builds ``provenance`` from the
			# actor, teaching user, source type, and source record.
			"provenance_context": provenance,
			"confidence": confidence,
			"user": teaching_user,
			"target_scope": target_scope,
			"target_scope_value": target_scope_value if target_scope != "Global" else None,
			"approval_required": 1 if cint(settings.require_memory_approval) else 0,
			"testing_status": "Not Tested",
			"status": "Draft",
		}
	)
	doc.flags.ignore_permissions = True
	doc.insert(ignore_permissions=True)
	return doc


def validate_candidate(candidate) -> dict:
	"""Stage 3 - validate content, provenance, authority, source and scope."""
	checks: list[dict] = []

	content_ok = bool((candidate.content or "").strip())
	type_ok = candidate.candidate_type in learning_utils.VALID_CANDIDATE_TYPES
	source_ok = bool(candidate.user) and candidate.source_type in VALID_SOURCE_TYPES
	provenance_ok = bool((candidate.provenance or "").strip())

	checks.extend(
		[
			{
				"ok": content_ok,
				"name": "content",
				"message": _("Content present.") if content_ok else _("Candidate has no content."),
			},
			{
				"ok": type_ok,
				"name": "type",
				"message": _("Type is valid.") if type_ok else _("Candidate type is invalid."),
			},
			{
				"ok": source_ok,
				"name": "source",
				"message": _("Source user and type are recorded.")
				if source_ok
				else _("Source user or type is invalid."),
			},
			{
				"ok": provenance_ok,
				"name": "provenance",
				"message": _("Provenance is recorded.") if provenance_ok else _("Provenance is missing."),
			},
		]
	)

	reference_ok, reference_message = _validate_reference(
		candidate.source_reference_doctype,
		candidate.source_reference_name,
		candidate.source_type,
		user=candidate.user,
	)
	checks.append({"ok": reference_ok, "name": "reference", "message": reference_message})

	scope_ok, scope_message = _validate_scope(
		candidate.target_scope or "Global",
		candidate.target_scope_value,
		candidate.user,
	)
	checks.append({"ok": scope_ok, "name": "scope", "message": scope_message})

	return {"candidate": candidate.name, "checks": checks, "valid": all(c["ok"] for c in checks)}


# ---------------------------------------------------------------------------
# Stage 4: test against existing data
# ---------------------------------------------------------------------------


def _existing_memories(active_only: bool = True) -> list[dict]:
	filters = {"status": "Active"} if active_only else {}
	return [
		learning_utils.memory_to_dict(row)
		for row in frappe.get_all("AI Memory", filters=filters, fields=["*"])
	]


def _existing_skills(active_only: bool = True) -> list[dict]:
	filters = {"enabled": 1} if active_only else {}
	return [
		learning_utils.skill_to_dict(row) for row in frappe.get_all("AI Skill", filters=filters, fields=["*"])
	]


def _scopes_can_conflict(candidate, existing: dict) -> bool:
	candidate_scope = candidate.target_scope or "Global"
	existing_scope = existing.get("scope") or "Global"
	if "Global" in (candidate_scope, existing_scope):
		return True
	if candidate_scope != existing_scope:
		return True
	return candidate.target_scope_value == existing.get("scope_value")


def check_conflicts(candidate) -> dict:
	"""Stage 4 - test a candidate against the existing memory/skill store.

	Two kinds of conflict are reported:
	- **duplicate**: the store already contains essentially the same knowledge.
	- **overlap**: meaningfully similar but not identical, worth a human look.

	Conflicts do not hard-fail a candidate (the administrator may still approve
	an intentional override), but they are surfaced to the approval gate.
	"""
	content = (candidate.content or "").strip()
	duplicates: list[dict] = []
	overlaps: list[dict] = []

	for memory in _existing_memories():
		if not _scopes_can_conflict(candidate, memory):
			continue
		similarity = learning_utils.score_relevance(content, memory.get("content"))
		if learning_utils.is_near_duplicate(content, memory.get("content")):
			duplicates.append(
				{"kind": "memory", "name": memory.get("name"), "similarity": round(similarity, 3)}
			)
		elif similarity >= 0.5:
			overlaps.append(
				{"kind": "memory", "name": memory.get("name"), "similarity": round(similarity, 3)}
			)

	for skill in _existing_skills():
		if not _scopes_can_conflict(candidate, skill):
			continue
		instruction = skill.get("instructions") or ""
		similarity = learning_utils.score_relevance(content, instruction)
		if learning_utils.is_near_duplicate(content, instruction):
			duplicates.append(
				{"kind": "skill", "name": skill.get("name"), "similarity": round(similarity, 3)}
			)
		elif similarity >= 0.5:
			overlaps.append({"kind": "skill", "name": skill.get("name"), "similarity": round(similarity, 3)})

	return {"duplicates": duplicates, "overlaps": overlaps}


# ---------------------------------------------------------------------------
# Stage 5 + 6: approve → memory / skill update
# ---------------------------------------------------------------------------


def approve_candidate(candidate_name: str, notes: str | None = None) -> dict:
	"""Approve a candidate as an AI Manager or System Manager."""
	frappe.only_for(["AI Manager", "System Manager"])
	if not learning_enabled():
		frappe.throw(_("The Learning Loop is disabled in AI Platform Settings."), exc=LearningError)
	return _approve_candidate(candidate_name, notes=notes, policy_approved=False)


def _approve_candidate(
	candidate_name: str,
	notes: str | None = None,
	policy_approved: bool = False,
) -> dict:
	"""Canonical promotion path used by human and configured policy approval."""
	# Serialize decisions for one candidate. This prevents simultaneous approve
	# clicks from producing two memories before either request updates status.
	frappe.db.get_value("AI Knowledge Candidate", candidate_name, "status", for_update=True)
	candidate = frappe.get_doc("AI Knowledge Candidate", candidate_name)

	if candidate.status == "Approved":
		promoted = _find_promoted(candidate.name)
		if promoted:
			return {
				"candidate": candidate_name,
				"status": "Approved",
				"promoted_to": promoted["doctype"],
				"promoted_name": promoted["name"],
			}
	if candidate.status not in ("Validated", "Conflict", "Draft"):
		frappe.throw(
			_("Candidate {0} has already been decided ({1}).").format(candidate_name, candidate.status)
		)

	report = validate_candidate(candidate)
	if not report["valid"]:
		frappe.throw(_("Candidate {0} failed validation.").format(candidate_name), exc=LearningError)

	conflicts = check_conflicts(candidate)
	if policy_approved and (conflicts["duplicates"] or conflicts["overlaps"]):
		frappe.throw(_("A conflicting candidate cannot be approved automatically."), exc=LearningError)
	if (
		(conflicts["duplicates"] or conflicts["overlaps"])
		and not policy_approved
		and not (notes or "").strip()
	):
		frappe.throw(_("Approval notes are required when overriding a conflict."), exc=LearningError)

	promoted = (
		_promote_to_skill(candidate)
		if candidate.candidate_type in learning_utils.SKILL_TYPES
		else _promote_to_memory(candidate)
	)

	approver = frappe.session.user
	decision_notes = notes or (
		"Automatically approved by the configured learning policy." if policy_approved else None
	)
	candidate.db_set(
		{
			"status": "Approved",
			"approved_by": approver,
			"approved_on": now_datetime(),
			"testing_status": "Passed",
			"validation_notes": decision_notes,
		},
		update_modified=False,
	)

	write_audit_log(
		action="Knowledge Candidate Auto-Approved" if policy_approved else "Knowledge Candidate Approved",
		category="Data",
		severity="Warning",
		message=f"{approver} approved candidate {candidate_name}.",
		details={
			"teaching_user": candidate.user,
			"policy_approved": policy_approved,
			"promoted_to": promoted["doctype"],
			"promoted_name": promoted["name"],
			"conflict_override": bool(conflicts["duplicates"] or conflicts["overlaps"]),
		},
		reference_doctype="AI Knowledge Candidate",
		reference_name=candidate_name,
		raise_on_error=True,
	)

	return {
		"candidate": candidate_name,
		"status": "Approved",
		"promoted_to": promoted["doctype"],
		"promoted_name": promoted["name"],
	}


def reject_candidate(candidate_name: str, notes: str | None = None) -> dict:
	"""Reject a candidate so it is never learned."""
	frappe.only_for(["AI Manager", "System Manager"])
	frappe.db.get_value("AI Knowledge Candidate", candidate_name, "status", for_update=True)
	candidate = frappe.get_doc("AI Knowledge Candidate", candidate_name)
	if candidate.status not in ("Validated", "Conflict", "Draft"):
		frappe.throw(
			_("Candidate {0} has already been decided ({1}).").format(candidate_name, candidate.status)
		)

	candidate.db_set(
		{
			"status": "Rejected",
			"approved_by": frappe.session.user,
			"approved_on": now_datetime(),
			"validation_notes": notes,
		},
		update_modified=False,
	)
	write_audit_log(
		action="Knowledge Candidate Rejected",
		category="Data",
		severity="Warning",
		message=_("{0} rejected candidate {1}.").format(frappe.session.user, candidate_name),
		details={"teaching_user": candidate.user, "decision_notes": notes},
		reference_doctype="AI Knowledge Candidate",
		reference_name=candidate_name,
		raise_on_error=True,
	)
	return {"candidate": candidate_name, "status": "Rejected"}


def _find_promoted(candidate_name: str) -> dict | None:
	memory = frappe.db.get_value("AI Memory", {"source_candidate": candidate_name}, "name")
	if memory:
		return {"doctype": "AI Memory", "name": memory}
	skill = frappe.db.get_value("AI Skill", {"source_candidate": candidate_name}, "name")
	if skill:
		return {"doctype": "AI Skill", "name": skill}
	return None


def _embed_memory_text(text: str, model: str | None = None) -> dict:
	"""Return ``{embedding, dimensions, model}`` for a memory, or empty on failure.

	Memory embeddings are optional: keyword recall works without them. If the
	embedding round-trip fails we degrade to keyword-only rather than failing
	the whole learning operation.
	"""
	from ai_fr_hg.ai.engine import run_embedding

	try:
		vectors = run_embedding([text], model=model, operation="Embedding")
		if vectors and vectors[0]:
			unit = normalize(vectors[0])
			return {
				"embedding": encode_vector(unit),
				"dimensions": len(unit),
				"model": model
				or frappe.db.get_single_value("AI Platform Settings", "default_embedding_model")
				or "",
			}
	except Exception:
		frappe.log_error(title="AI memory embedding failed", message=frappe.get_traceback())
	return {"embedding": "", "dimensions": 0, "model": ""}


def _promote_to_memory(candidate) -> dict:
	"""Create an idempotent ``AI Memory`` from an approved candidate."""
	if promoted := _find_promoted(candidate.name):
		return promoted

	embed = _embed_memory_text(candidate.content)
	doc = frappe.new_doc("AI Memory")
	doc.update(
		{
			"content": candidate.content,
			# A Document candidate is a source category; the persistent memory
			# taxonomy intentionally remains Fact/Preference/Instruction/Feedback.
			"memory_type": candidate.candidate_type
			if candidate.candidate_type in learning_utils.MEMORY_TYPES
			else "Fact",
			"scope": candidate.target_scope or "Global",
			"scope_value": candidate.target_scope_value,
			"source_candidate": candidate.name,
			"source_type": candidate.source_type,
			"source_user": candidate.user,
			"provenance": candidate.provenance,
			"confidence": candidate.confidence,
			"status": "Active",
			"embedding": embed["embedding"],
			"embedding_model": embed["model"] or None,
			"embedding_dimensions": embed["dimensions"] or 0,
			"embedding_format": "Base64 Float32" if embed["embedding"] else None,
		}
	)
	doc.flags.ignore_permissions = True
	doc.flags.from_learning = True
	doc.insert(ignore_permissions=True)
	return {"doctype": "AI Memory", "name": doc.name}


def _promote_to_skill(candidate) -> dict:
	"""Create an idempotent, safely named ``AI Skill`` from an instruction."""
	if promoted := _find_promoted(candidate.name):
		return promoted

	skill_name = (candidate.title or candidate.content[:80]).strip()[:140]
	if frappe.db.exists("AI Skill", skill_name):
		suffix = f" ({candidate.name})"
		skill_name = f"{skill_name[: 140 - len(suffix)]}{suffix}"

	doc = frappe.new_doc("AI Skill")
	doc.update(
		{
			"skill_name": skill_name,
			"description": (candidate.provenance or "")[:300],
			"instructions": candidate.content,
			"skill_type": "Procedural",
			"scope": candidate.target_scope or "Global",
			"scope_value": candidate.target_scope_value,
			"source_candidate": candidate.name,
			"source_user": candidate.user,
			"enabled": 1,
			"version": 1,
		}
	)
	doc.flags.ignore_permissions = True
	doc.flags.from_learning = True
	doc.insert(ignore_permissions=True)
	return {"doctype": "AI Skill", "name": doc.name}


def _audit_candidate_processing(candidate, state: str, *, details: dict | None = None) -> None:
	"""Record a candidate validation/governance transition in the same transaction."""
	severity = "Warning" if state in {"Validation Failed", "Conflict"} else "Info"
	write_audit_log(
		action=f"Knowledge Candidate {state}",
		category="Data",
		severity=severity,
		message=_("Candidate {0} entered learning state {1}.").format(candidate.name, state),
		details={
			"teaching_user": candidate.user,
			"source_type": candidate.source_type,
			"target_scope": candidate.target_scope,
			"target_scope_value": candidate.target_scope_value,
			**(details or {}),
		},
		reference_doctype="AI Knowledge Candidate",
		reference_name=candidate.name,
		raise_on_error=True,
	)


def process_candidate(candidate_name: str, approve: bool = False) -> dict:
	"""Validate, conflict-test and apply the configured gate to a draft candidate."""
	check_capability("learning")
	if not learning_enabled():
		frappe.throw(_("The Learning Loop is disabled in AI Platform Settings."), exc=LearningError)
	candidate = frappe.get_doc("AI Knowledge Candidate", candidate_name)
	candidate.check_permission("read")
	if not _is_learning_manager() and candidate.user != frappe.session.user:
		frappe.throw(_("You may only validate your own knowledge candidates."), frappe.PermissionError)
	approval_required = 1 if cint(_settings().require_memory_approval) else 0
	if cint(candidate.approval_required) != approval_required:
		candidate.db_set("approval_required", approval_required, update_modified=False)
	if candidate.status in {"Approved", "Rejected"}:
		frappe.throw(_("A decided candidate cannot be processed again."), exc=LearningError)

	report = validate_candidate(candidate)
	if not report["valid"]:
		notes = "; ".join(check["message"] for check in report["checks"] if not check["ok"])
		candidate.db_set(
			{"status": "Draft", "testing_status": "Failed", "validation_notes": notes},
			update_modified=False,
		)
		_audit_candidate_processing(candidate, "Validation Failed", details={"validation_notes": notes})
		return {
			"candidate": candidate.name,
			"status": "Draft",
			"conflicts": {"duplicates": [], "overlaps": []},
			"valid": False,
			"validation": report,
		}

	conflicts = check_conflicts(candidate)
	all_conflicts = conflicts["duplicates"] + conflicts["overlaps"]
	candidate.db_set("conflict_count", len(all_conflicts), update_modified=False)
	if all_conflicts:
		summary = ", ".join(f"{item['kind']} {item['name']}" for item in all_conflicts)
		candidate.db_set(
			{
				"status": "Conflict",
				"testing_status": "Conflict",
				"conflicts_summary": summary[:1000],
			},
			update_modified=False,
		)
		_audit_candidate_processing(
			candidate,
			"Conflict",
			details={
				"duplicate_count": len(conflicts["duplicates"]),
				"overlap_count": len(conflicts["overlaps"]),
				"summary": summary[:1000],
			},
		)
		return {
			"candidate": candidate.name,
			"status": "Conflict",
			"conflicts": conflicts,
			"valid": True,
			"validation": report,
		}

	candidate.db_set(
		{"status": "Validated", "testing_status": "Passed", "conflicts_summary": None},
		update_modified=False,
	)
	_audit_candidate_processing(candidate, "Validated", details={"approval_required": approval_required})
	if approve:
		decision = approve_candidate(candidate.name, notes="Approved during teaching; no conflicts detected.")
	elif not cint(candidate.approval_required):
		decision = _approve_candidate(candidate.name, policy_approved=True)
	else:
		decision = None

	return {
		"candidate": candidate.name,
		"status": decision["status"] if decision else "Validated",
		"conflicts": conflicts,
		"valid": True,
		"validation": report,
		**({"promotion": decision} if decision else {}),
	}


def teach(
	content: str,
	title: str | None = None,
	candidate_type: str | None = None,
	source_type: str = "Explicit Teaching",
	source_reference_doctype: str | None = None,
	source_reference_name: str | None = None,
	provenance: str | None = None,
	confidence: float = 0.0,
	approve: bool = False,
	user: str | None = None,
	target_scope: str | None = None,
	target_scope_value: str | None = None,
) -> dict:
	"""Run the canonical create → validate → conflict-test → approval path."""
	candidate = create_candidate(
		content=content,
		title=title,
		candidate_type=candidate_type,
		source_type=source_type,
		source_reference_doctype=source_reference_doctype,
		source_reference_name=source_reference_name,
		provenance=provenance,
		confidence=confidence,
		user=user,
		target_scope=target_scope,
		target_scope_value=target_scope_value,
	)

	return process_candidate(candidate.name, approve=approve)


# ---------------------------------------------------------------------------
# Stage 7: future AI behaviour - recall and inject
# ---------------------------------------------------------------------------


def _memory_applies(memory: dict, user: str, roles: set[str], agent: str | None) -> bool:
	scope = memory.get("scope") or "Global"
	value = memory.get("scope_value")
	if scope == "Global":
		return True
	if scope == "User":
		return bool(value) and value == user
	if scope == "Role":
		return bool(value) and value in roles
	if scope == "Agent":
		return bool(value) and value == agent
	return True


def recall(
	query: str | None,
	agent: str | None = None,
	user: str | None = None,
	limit: int | None = None,
	track_usage: bool = True,
) -> tuple[list[dict], list[dict]]:
	"""Return ``(memories, skills)`` that should shape this turn's behaviour.

	Memories are filtered to the caller's scope, ranked by relevance to
	`query`, and returned as dicts for prompt injection. Skills are filtered
	to scope and returned in full (they are few and procedural). Usage is
	recorded when `track_usage` is true so the loop can observe what actually
	gets used.
	"""
	user = user or frappe.session.user
	roles = set(frappe.get_roles(user))
	settings = _settings()
	limit = cint(limit) or cint(settings.memory_top_k) or 5

	memories = [m for m in _existing_memories() if _memory_applies(m, user, roles, agent)]
	ranked = learning_utils.rank_memories(query, memories)[:limit]

	skills = [s for s in _existing_skills() if _memory_applies(s, user, roles, agent)]

	if track_usage and (ranked or skills):
		_track_usage(ranked, skills)

	return ranked, skills


def _track_usage(memories: list[dict], skills: list[dict]) -> None:
	"""Increment usage counters and touch last_used_on (single atomic updates)."""
	now = now_datetime()
	for memory in memories:
		if name := memory.get("name"):
			frappe.db.sql(
				"update `tabAI Memory` set usage_count = coalesce(usage_count,0) + 1, "
				"last_used_on = %s where name = %s",
				(now, name),
			)
	for skill in skills:
		if name := skill.get("name"):
			frappe.db.sql(
				"update `tabAI Skill` set usage_count = coalesce(usage_count,0) + 1 where name = %s",
				(name,),
			)


def prepare_memory_context(
	query: str,
	agent: str | None = None,
	user: str | None = None,
	max_characters: int | None = None,
) -> dict:
	"""Build prompt blocks and return the exact learned records behind them."""
	settings = _settings()
	if not cint(settings.learning_enabled):
		return {"memory_block": "", "skill_block": "", "memories": [], "skills": []}

	# Existence checks are a single indexed probe each. An empty store must
	# not pay for loading every memory/skill row on every chat turn.
	if not frappe.db.exists("AI Memory", {"status": "Active"}) and not frappe.db.exists(
		"AI Skill", {"enabled": 1}
	):
		return {"memory_block": "", "skill_block": "", "memories": [], "skills": []}

	memories, skills = recall(query, agent=agent, user=user)
	max_chars = cint(max_characters) or cint(settings.max_memory_characters) or 8000
	memory_block = learning_utils.build_memory_block(memories, max_characters=max_chars)
	skill_block = learning_utils.build_skill_block(skills, max_characters=max_chars)

	return {
		"memory_block": f"LEARNED KNOWLEDGE (follow when relevant):\n{memory_block}" if memory_block else "",
		"skill_block": f"KNOWN PROCEDURES (apply when the task matches):\n{skill_block}"
		if skill_block
		else "",
		"memories": [memory.get("name") for memory in memories if memory.get("name")],
		"skills": [skill.get("name") for skill in skills if skill.get("name")],
	}


def build_memory_context(
	query: str,
	agent: str | None = None,
	user: str | None = None,
	max_characters: int | None = None,
) -> tuple[str, str]:
	"""Return backwards-compatible ``(memory_block, skills_block)`` output."""
	context = prepare_memory_context(query, agent=agent, user=user, max_characters=max_characters)
	return context["memory_block"], context["skill_block"]


# ---------------------------------------------------------------------------
# Stage 8: observe result - feedback and usage
# ---------------------------------------------------------------------------


def _learned_memories(message) -> list[str]:
	try:
		context = frappe.parse_json(message.learned_context or "{}") or {}
	except (TypeError, ValueError):
		return []
	return [name for name in context.get("memories", []) if name]


def _adjust_memory_feedback(message, previous_feedback: str, feedback: str) -> None:
	"""Move feedback counters exactly once when a user changes a rating."""
	memories = _learned_memories(message)
	if not memories or previous_feedback == feedback:
		return

	for memory in memories:
		if previous_feedback == "Positive":
			frappe.db.sql(
				"update `tabAI Memory` set helpful_count = greatest(coalesce(helpful_count, 0) - 1, 0) where name = %s",
				(memory,),
			)
		elif previous_feedback == "Negative":
			frappe.db.sql(
				"update `tabAI Memory` set not_helpful_count = greatest(coalesce(not_helpful_count, 0) - 1, 0) where name = %s",
				(memory,),
			)

		if feedback == "Positive":
			frappe.db.sql(
				"update `tabAI Memory` set helpful_count = coalesce(helpful_count, 0) + 1 where name = %s",
				(memory,),
			)
		elif feedback == "Negative":
			frappe.db.sql(
				"update `tabAI Memory` set not_helpful_count = coalesce(not_helpful_count, 0) + 1 where name = %s",
				(memory,),
			)


def observe_feedback(
	message_name: str,
	feedback: str,
	correction: str | None = None,
	reason: str | None = None,
	previous_feedback: str = "",
) -> dict:
	"""Observe an answer outcome without ever treating a wrong answer as truth."""
	if feedback not in ("Positive", "Negative", ""):
		frappe.throw(_("Feedback must be Positive or Negative."), exc=LearningError)

	message = frappe.get_doc("AI Message", message_name)
	message.check_permission("write")
	if message.role != "Assistant":
		frappe.throw(_("Feedback can only be recorded for assistant messages."), exc=LearningError)

	_adjust_memory_feedback(message, previous_feedback, feedback)
	if not feedback:
		return {"message": message_name, "feedback": feedback, "candidate": None}

	write_audit_log(
		action=f"Feedback {feedback}",
		category="Data",
		message=reason,
		details={"previous_feedback": previous_feedback, "has_correction": bool((correction or "").strip())},
		reference_doctype="AI Message",
		reference_name=message_name,
		raise_on_error=True,
	)
	if feedback == "Positive":
		return {"message": message_name, "feedback": feedback, "candidate": None}
	if not learning_enabled():
		return {
			"message": message_name,
			"feedback": feedback,
			"candidate": None,
			"learning_status": "Disabled",
		}
	try:
		check_capability("learning")
	except QuotaExceededError:
		# Rating an answer remains available even when policy denies teaching.
		frappe.log_error(title="AI feedback learning denied", message=frappe.get_traceback())
		return {
			"message": message_name,
			"feedback": feedback,
			"candidate": None,
			"learning_status": "Not Permitted",
		}

	# Repeated clicks must not create repeated candidates for one failed answer.
	existing = frappe.db.get_value(
		"AI Knowledge Candidate",
		{
			"source_type": "Chat Correction",
			"source_reference_doctype": "AI Message",
			"source_reference_name": message_name,
		},
		"name",
	)
	if existing:
		return {"message": message_name, "feedback": feedback, "candidate": existing}

	correction = (correction or "").strip()
	if correction:
		content = correction[:4000]
		candidate_type = learning_utils.classify_candidate(content)
		title = "Correction from feedback"
	else:
		failed_answer = (message.content or "").strip()[:2000]
		if not failed_answer:
			return {"message": message_name, "feedback": feedback, "candidate": None}
		content = (
			"This is a failure example, not authoritative knowledge. The following assistant answer "
			f"was marked not helpful{f' ({reason})' if reason else ''}:\n\n{failed_answer}"
		)
		candidate_type = "Feedback"
		title = "Not-helpful answer for review"

	result = teach(
		content=content,
		title=title,
		candidate_type=candidate_type,
		source_type="Chat Correction",
		source_reference_doctype="AI Message",
		source_reference_name=message_name,
		provenance=f"User marked the answer in {message_name} as not helpful.",
	)
	return {"message": message_name, "feedback": feedback, "candidate": result["candidate"]}


# ---------------------------------------------------------------------------
# Whitelisted actions (used by Desk buttons)
# ---------------------------------------------------------------------------


@frappe.whitelist()
def record_feedback(
	message: str,
	feedback: str,
	correction: str | None = None,
	reason: str | None = None,
) -> dict:
	"""Persist and observe feedback through one permission-checked path."""
	if feedback not in ("Positive", "Negative", ""):
		frappe.throw(_("Feedback must be Positive or Negative."), exc=LearningError)
	if (reason or "") not in VALID_FEEDBACK_REASONS:
		frappe.throw(_("Feedback reason is invalid."), exc=LearningError)

	doc = frappe.get_doc("AI Message", message)
	doc.check_permission("write")
	previous = doc.feedback or ""
	doc.db_set(
		{"feedback": feedback, "feedback_reason": reason, "feedback_comment": correction},
		update_modified=False,
	)
	return observe_feedback(
		message,
		feedback,
		correction=correction,
		reason=reason,
		previous_feedback=previous,
	)
