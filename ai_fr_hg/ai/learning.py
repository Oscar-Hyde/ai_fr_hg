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
from ai_fr_hg.ai.exceptions import AIError
from ai_fr_hg.ai.governance import check_capability
from ai_fr_hg.ai.logging import write_audit_log
from ai_fr_hg.ai.vector import encode_vector, normalize


class LearningError(AIError):
	"""Raised for invalid teaching input that the UI can correct inline."""


def _settings():
	return frappe.get_cached_doc("AI Platform Settings")


def learning_enabled() -> bool:
	return cint(_settings().learning_enabled)


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
) -> dict:
	"""Record a piece of teaching as an ``AI Knowledge Candidate``.

	`candidate_type` is inferred from the content when omitted (see
	:func:`learning_utils.classify_candidate`). The record is created in the
	``Draft`` state and is not visible to agents until approved.
	"""
	check_capability("learning")

	content = (content or "").strip()
	if not content:
		frappe.throw(_("Teaching content cannot be empty."), exc=LearningError)

	candidate_type = candidate_type or learning_utils.classify_candidate(content)
	if candidate_type not in learning_utils.VALID_CANDIDATE_TYPES:
		frappe.throw(
			_("Unsupported candidate type {0}.").format(candidate_type), exc=LearningError
		)

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
			"provenance": provenance,
			"confidence": confidence,
			"user": user or frappe.session.user,
			"approval_required": 1 if cint(settings.require_memory_approval) else 0,
			"status": "Draft",
		}
	)
	doc.flags.ignore_permissions = True
	doc.insert(ignore_permissions=True)

	write_audit_log(
		action="Knowledge Candidate Created",
		category="Learning",
		message=f"Candidate {doc.name} ({candidate_type}) created.",
		reference_doctype="AI Knowledge Candidate",
		reference_name=doc.name,
	)
	return doc


def validate_candidate(candidate) -> dict:
	"""Stage 3 - confirm provenance and integrity before anything is learned.

	Returns a report dict and raises :class:`LearningError` for values that
	cannot be learned at all.
	"""
	checks: list[dict] = []

	if not (candidate.content or "").strip():
		raise LearningError(_("Candidate has no content."))
	if candidate.candidate_type not in learning_utils.VALID_CANDIDATE_TYPES:
		raise LearningError(_("Candidate has an invalid type."))

	checks.append({"ok": True, "name": "content", "message": _("Content present.")})
	checks.append({"ok": True, "name": "type", "message": _("Type is valid.")})

	# Provenance is the audit story: we must always know where a teaching came
	# from. Explicit teaching by a named user is always fine; auto-captured
	# sources must carry a reference back to the originating record.
	source_ok = bool(candidate.user) and bool(candidate.source_type)
	checks.append(
		{
			"ok": source_ok,
			"name": "source",
			"message": _("Source user and type are recorded.") if source_ok else _("Missing source."),
		}
	)

	if candidate.source_type not in ("Explicit Teaching", "Chat Correction", "Feedback") and not (
		candidate.source_reference_doctype and candidate.source_reference_name
	):
		checks.append(
			{
				"ok": False,
				"name": "reference",
				"message": _("Document and tool sources must reference an originating record."),
			}
		)

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
		learning_utils.skill_to_dict(row)
		for row in frappe.get_all("AI Skill", filters=filters, fields=["*"])
	]


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
		instruction = skill.get("instructions") or ""
		similarity = learning_utils.score_relevance(content, instruction)
		if learning_utils.is_near_duplicate(content, instruction):
			duplicates.append(
				{"kind": "skill", "name": skill.get("name"), "similarity": round(similarity, 3)}
			)
		elif similarity >= 0.5:
			overlaps.append(
				{"kind": "skill", "name": skill.get("name"), "similarity": round(similarity, 3)}
			)

	return {"duplicates": duplicates, "overlaps": overlaps}


# ---------------------------------------------------------------------------
# Stage 5 + 6: approve → memory / skill update
# ---------------------------------------------------------------------------


def approve_candidate(candidate_name: str, notes: str | None = None, user: str | None = None) -> dict:
	"""Approve a candidate and promote it to ``AI Memory`` or ``AI Skill``.

	Raises for AI User (only AI Manager / System Manager may approve), for an
	already-decided candidate, and when the candidate failed validation.
	"""
	frappe.only_for(["AI Manager", "System Manager"])
	candidate = frappe.get_doc("AI Knowledge Candidate", candidate_name)

	if candidate.status not in ("Validated", "Conflict", "Draft"):
		frappe.throw(_("Candidate {0} has already been decided ({1}).").format(candidate_name, candidate.status))

	report = validate_candidate(candidate)
	if not report["valid"]:
		frappe.throw(_("Candidate {0} failed validation.").format(candidate_name))

	approver = user or frappe.session.user
	promoted = None
	if candidate.candidate_type in learning_utils.SKILL_TYPES:
		promoted = _promote_to_skill(candidate)
	else:
		promoted = _promote_to_memory(candidate)

	candidate.db_set(
		{
			"status": "Approved",
			"approved_by": approver,
			"approved_on": now_datetime(),
			"validation_notes": notes,
		},
		update_modified=False,
	)

	write_audit_log(
		action="Knowledge Candidate Approved",
		category="Learning",
		severity="Warning",
		message=f"{approver} approved candidate {candidate_name}.",
		reference_doctype="AI Knowledge Candidate",
		reference_name=candidate_name,
	)

	return {
		"candidate": candidate_name,
		"status": "Approved",
		"promoted_to": promoted["doctype"],
		"promoted_name": promoted["name"],
	}


def reject_candidate(candidate_name: str, notes: str | None = None, user: str | None = None) -> dict:
	"""Reject a candidate so it is never learned."""
	frappe.only_for(["AI Manager", "System Manager"])
	candidate = frappe.get_doc("AI Knowledge Candidate", candidate_name)
	if candidate.status not in ("Validated", "Conflict", "Draft"):
		frappe.throw(_("Candidate {0} has already been decided ({1}).").format(candidate_name, candidate.status))

	candidate.db_set(
		{
			"status": "Rejected",
			"approved_by": user or frappe.session.user,
			"approved_on": now_datetime(),
			"validation_notes": notes,
		},
		update_modified=False,
	)
	write_audit_log(
		action="Knowledge Candidate Rejected",
		category="Learning",
		severity="Warning",
		reference_doctype="AI Knowledge Candidate",
		reference_name=candidate_name,
	)
	return {"candidate": candidate_name, "status": "Rejected"}


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
				"model": model or "",
			}
	except Exception:
		frappe.log_error(title="AI memory embedding failed", message=frappe.get_traceback())
	return {"embedding": "", "dimensions": 0, "model": ""}


def _promote_to_memory(candidate) -> dict:
	"""Create an ``AI Memory`` from an approved fact/preference/feedback."""
	embed = _embed_memory_text(candidate.content)
	doc = frappe.new_doc("AI Memory")
	doc.update(
		{
			"content": candidate.content,
			"memory_type": candidate.candidate_type,
			"scope": "Global",
			"source_candidate": candidate.name,
			"source_type": candidate.source_type,
			"source_user": candidate.user,
			"confidence": candidate.confidence,
			"status": "Active",
			"embedding": embed["embedding"],
			"embedding_model": embed["model"] or None,
			"embedding_dimensions": embed["dimensions"] or 0,
		}
	)
	doc.flags.ignore_permissions = True
	doc.insert(ignore_permissions=True)
	return {"doctype": "AI Memory", "name": doc.name}


def _promote_to_skill(candidate) -> dict:
	"""Create an ``AI Skill`` from an approved instruction."""
	doc = frappe.new_doc("AI Skill")
	doc.update(
		{
			"skill_name": candidate.title or candidate.content[:80],
			"description": (candidate.provenance or "")[:300],
			"instructions": candidate.content,
			"skill_type": "Procedural",
			"scope": "Global",
			"source_candidate": candidate.name,
			"source_user": candidate.user,
			"enabled": 1,
			"version": 1,
		}
	)
	doc.flags.ignore_permissions = True
	doc.insert(ignore_permissions=True)
	return {"doctype": "AI Skill", "name": doc.name}


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
) -> dict:
	"""Run the full teaching path: create → validate → conflict test → gate.

	Returns the candidate with its status. When ``approve`` is true and the
	caller is an approver, a conflict-free candidate is approved immediately
	(used by scripts and automation); otherwise it waits at ``Validated``.
	"""
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
	)

	report = validate_candidate(candidate)
	conflicts = check_conflicts(candidate)
	candidate.db_set("conflict_count", len(conflicts["duplicates"]) + len(conflicts["overlaps"]))
	if conflicts["duplicates"]:
		candidate.db_set("status", "Conflict")
		candidate.db_set(
			"conflicts_summary",
			"Duplicate of existing " + ", ".join(c["name"] for c in conflicts["duplicates"]),
		)
		return {
			"candidate": candidate.name,
			"status": "Conflict",
			"conflicts": conflicts,
			"valid": report["valid"],
		}

	candidate.db_set("status", "Validated")

	if approve and frappe.session.user != "Guest":
		approve_candidate(candidate.name, notes="Auto-approved; no conflicts detected.", user=user)
		return {
			"candidate": candidate.name,
			"status": "Approved",
			"conflicts": conflicts,
			"valid": True,
		}

	return {
		"candidate": candidate.name,
		"status": candidate.status,
		"conflicts": conflicts,
		"valid": report["valid"],
	}


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


def build_memory_context(
	query: str,
	agent: str | None = None,
	user: str | None = None,
	max_characters: int | None = None,
) -> tuple[str, str]:
	"""Return ``(memory_block, skills_block)`` for this turn's learned knowledge.

	Each block is either empty (nothing taught/relevant) or a fully-framed,
	numbered prompt block ready to insert into the agent's system prompt.
	Disabling the learning loop yields two empty blocks.
	"""
	settings = _settings()
	if not cint(settings.learning_enabled):
		return "", ""

	memories, skills = recall(query, agent=agent, user=user)
	max_chars = cint(max_characters) or cint(settings.max_memory_characters) or 8000

	memory_block = learning_utils.build_memory_block(memories, max_characters=max_chars)
	skill_block = learning_utils.build_skill_block(skills, max_characters=max_chars)

	memory_block = f"LEARNED KNOWLEDGE (follow when relevant):\n{memory_block}" if memory_block else ""
	skill_block = f"KNOWN PROCEDURES (apply when the task matches):\n{skill_block}" if skill_block else ""

	return memory_block, skill_block


# ---------------------------------------------------------------------------
# Stage 8: observe result - feedback and usage
# ---------------------------------------------------------------------------


def observe_feedback(message_name: str, feedback: str) -> dict:
	"""Stage 8 - feed an answer's outcome back into the learning loop.

	A ``Negative`` rating on an assistant message captures the answer as a
	candidate for review, so a human can turn a recurring mistake into a
	learned correction. ``Positive`` feedback just closes the loop quietly.
	"""
	message = frappe.get_doc("AI Message", message_name)
	message.check_permission("read")

	if feedback == "Positive":
		write_audit_log(
			action="Feedback Positive",
			category="Learning",
			reference_doctype="AI Message",
			reference_name=message_name,
		)
		return {"message": message_name, "feedback": feedback}

	# Negative feedback → teaching candidate for a human to review and approve.
	content = (message.content or "").strip()[:2000]
	if not content:
		return {"message": message_name, "feedback": feedback, "candidate": None}

	candidate = create_candidate(
		content=content,
		title="Correction from feedback",
		candidate_type=learning_utils.classify_candidate(content),
		source_type="Chat Correction",
		source_reference_doctype="AI Message",
		source_reference_name=message_name,
		provenance=f"User marked the answer in {message_name} as not helpful.",
	)
	candidate.db_set("status", "Validated")
	return {"message": message_name, "feedback": feedback, "candidate": candidate.name}


# ---------------------------------------------------------------------------
# Whitelisted actions (used by Desk buttons)
# ---------------------------------------------------------------------------


@frappe.whitelist()
def record_feedback(message: str, feedback: str) -> dict:
	"""Whitelisted wrapper around :func:`observe_feedback`."""
	if feedback not in ("Positive", "Negative", ""):
		frappe.throw(_("Feedback must be Positive or Negative."))
	if not feedback:
		return {"message": message, "feedback": feedback}
	return observe_feedback(message, feedback)
