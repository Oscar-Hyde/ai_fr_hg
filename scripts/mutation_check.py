#!/usr/bin/env python3
# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Mutation harness: prove the test suite can actually fail.

A passing suite means nothing on its own. This applies deliberate, realistic
defects to application source, reruns the suite, and reports whether each was
detected. A mutation that survives marks a behaviour nothing verifies.

Usage:
    python scripts/mutation_check.py            # run every mutation
    python scripts/mutation_check.py --list     # show them without running
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYTHON = sys.executable


@dataclass
class Mutation:
	"""One deliberate defect, and the behaviour it should violate."""

	name: str
	path: str
	old: str
	new: str
	breaks: str

	def target(self) -> Path:
		return ROOT / self.path


MUTATIONS: list[Mutation] = [
	# -- retention (§20.4, §26, §27) --------------------------------------
	Mutation(
		name="retention-deletes-everything",
		path="ai_fr_hg/tasks.py",
		old='filters={"creation": ["<", cutoff]},',
		new="filters={},",
		breaks="Retention must delete only rows older than the cutoff.",
	),
	Mutation(
		name="retention-never-commits",
		path="ai_fr_hg/tasks.py",
		old="frappe.db.commit()  # nosemgrep: frappe-manual-commit\n\t\tdeleted += len(names)",
		new="deleted += len(names)",
		breaks="Batches must commit so an interrupted run is resumable.",
	),
	Mutation(
		name="retention-ignores-ceiling",
		path="ai_fr_hg/tasks.py",
		old="while deleted < max_rows:",
		new="while True:",
		breaks="A run must stop at the per-run ceiling.",
	),
	Mutation(
		name="retention-aborts-on-first-failure",
		path="ai_fr_hg/tasks.py",
		old='\t\t\tfrappe.log_error(title=f"AI retention cleanup failed: {doctype}", message=frappe.get_traceback())\n\t\t\tcontinue',
		new="\t\t\traise",
		breaks="One failing DocType must not stop retention for the others.",
	),
	# -- retrieval provenance (§13.3) -------------------------------------
	Mutation(
		name="provenance-raises-on-bad-json",
		path="ai_fr_hg/ai/retrieval.py",
		old="""	try:
		evidence = json.loads(raw) if isinstance(raw, str) else raw
	except (ValueError, TypeError):
		return {}, None""",
		new="	evidence = json.loads(raw) if isinstance(raw, str) else raw",
		breaks="Malformed evidence must degrade, never fail a search.",
	),
	Mutation(
		name="retrieval-method-always-hybrid",
		path="ai_fr_hg/ai/retrieval.py",
		old="""	if in_semantic and in_keyword:
		return "hybrid\"""",
		new="""	if in_semantic or in_keyword:
		return "hybrid\"""",
		breaks="retrieval_method must name the path that actually matched.",
	),
	# -- grounding disclosure (§15.3) -------------------------------------
	Mutation(
		name="grounding-always-sourced",
		path="ai_fr_hg/ai/conversation.py",
		old='"basis": "sources" if citation_count else "unsupported",',
		new='"basis": "sources",',
		breaks="An uncited answer must never be reported as source-backed.",
	),
	Mutation(
		name="grounding-dropped-on-reload",
		path="ai_fr_hg/ai/conversation.py",
		old='if (item.get("role") or "") == "Assistant":',
		new="if False:",
		breaks="Reloading a conversation must keep the disclosure.",
	),
	# -- semantic persistence and audit (§24) -----------------------------
	Mutation(
		name="semantic-audit-omitted",
		path="ai_fr_hg/ai/semantic.py",
		old='		action="Semantic Entities Extracted",',
		new='		action="",',
		breaks="Semantic extraction must be identifiable in the audit trail.",
	),
	Mutation(
		name="semantic-audit-hides-rejections",
		path="ai_fr_hg/ai/semantic.py",
		old='			"rejected": rejected,\n			"confidence_floor": confidence_floor(),',
		new='			"confidence_floor": confidence_floor(),',
		breaks="The audit must record what the grounding filter discarded.",
	),
	Mutation(
		name="semantic-leaks-wrong-knowledge-base",
		path="ai_fr_hg/ai/semantic.py",
		old="		doc.knowledge_base = row.knowledge_base",
		new='		doc.knowledge_base = "OTHER-KB"',
		breaks="Rows must inherit the document's knowledge base (isolation).",
	),
	Mutation(
		name="semantic-stale-rows-not-pruned",
		path="ai_fr_hg/ai/semantic.py",
		old="""	for name in existing_entities.values():
		if name not in touched:
			frappe.db.delete("AI Pattern Entity", name)
			removed += 1""",
		new="	pass",
		breaks="Entities absent from a rescan must be removed.",
	),
	Mutation(
		name="semantic-no-scan-timestamp",
		path="ai_fr_hg/ai/semantic.py",
		old='			"last_scanned_on": scanned_at,\n		}\n		name = existing_entities.get(key)',
		new="		}\n		name = existing_entities.get(key)",
		breaks="A result must record when it was produced.",
	),
	# -- relationship invariants (§11, §23) -------------------------------
	Mutation(
		name="relationship-evidence-optional",
		path="ai_fr_hg/ai_knowledge/doctype/ai_entity_relationship/ai_entity_relationship.py",
		old='		if not quote:\n			frappe.throw(_("A relationship requires an evidence quote from the source document."))',
		new="		if False:\n			pass",
		breaks="An inferred relationship without evidence must be refused.",
	),
	Mutation(
		name="relationship-allows-self-reference",
		path="ai_fr_hg/ai_knowledge/doctype/ai_entity_relationship/ai_entity_relationship.py",
		old="		if self.subject.casefold() == self.object.casefold():",
		new="		if False:",
		breaks="An entity must not be linked to itself.",
	),
	Mutation(
		name="relationship-confidence-unclamped",
		path="ai_fr_hg/ai_knowledge/doctype/ai_entity_relationship/ai_entity_relationship.py",
		old="		self.confidence = max(0.0, min(100.0, flt(self.confidence)))",
		new="		self.confidence = flt(self.confidence)",
		breaks="Confidence must be clamped to 0-100.",
	),
	# -- provenance immutability (§10) ------------------------------------
	Mutation(
		name="provenance-mutable",
		path="ai_fr_hg/ai_knowledge/doctype/ai_document/ai_document.py",
		old="			if self.get(fieldname) != old.get(fieldname):",
		new="			if False:",
		breaks="Extraction provenance must be immutable after the fact.",
	),
	# -- grounding filter (§11) -------------------------------------------
	Mutation(
		name="ungrounded-entities-accepted",
		path="ai_fr_hg/ai/semantic.py",
		old="""		offset = find_grounded_offset(source_text, value)
		if offset is None:""",
		new="""		offset = find_grounded_offset(source_text, value)
		if False:""",
		breaks="A value absent from the source must be discarded.",
	),
	Mutation(
		name="confidence-floor-ignored",
		path="ai_fr_hg/ai/semantic.py",
		old='		if confidence < confidence_floor:\n			rejected["low_confidence"] += 1\n			continue\n		offset = find_grounded_offset(source_text, value)',
		new="		offset = find_grounded_offset(source_text, value)",
		breaks="Results below the confidence floor must be discarded.",
	),
	# -- archive safety (§6.2) --------------------------------------------
	Mutation(
		# The redundant `..` scan is defence-in-depth; `normpath` is the real
		# check. Mutating the effective guard is what proves the control works.
		name="archive-traversal-allowed",
		path="ai_fr_hg/ai/readers/archive.py",
		old='	if resolved.startswith(("/", "../")) or resolved == "..":\n		return None',
		new="	pass",
		breaks="Archive members must not escape the archive root.",
	),
	Mutation(
		name="archive-absolute-path-allowed",
		path="ai_fr_hg/ai/readers/archive.py",
		old='	if normalized.startswith("/") or (len(normalized) > 1 and normalized[1] == ":"):\n		return None',
		new="	pass",
		breaks="Absolute and drive-letter member paths must be refused.",
	),
	Mutation(
		name="archive-symlinks-followed",
		path="ai_fr_hg/ai/readers/archive.py",
		old="			if member.issym() or member.islnk():",
		new="			if False:",
		breaks="Archive links must never be followed (path-escape vector).",
	),
	Mutation(
		name="archive-depth-unbounded",
		path="ai_fr_hg/ai/readers/container.py",
		old="				if depth + 1 > budget.max_depth:",
		new="				if False:",
		breaks="Nested archives must respect the depth ceiling.",
	),
	# -- extraction contract (§8) -----------------------------------------
	Mutation(
		name="evidence-loses-version",
		path="ai_fr_hg/ai/extraction.py",
		old="		versions=build_versions(reader),",
		new="		versions={},",
		breaks="Evidence must identify the version that produced it.",
	),
	Mutation(
		name="evidence-loses-timestamp",
		path="ai_fr_hg/ai/extraction.py",
		old="		extracted_on=datetime.now(UTC).isoformat(),",
		new='		extracted_on="",',
		breaks="Evidence must record when extraction ran.",
	),
	# -- formula preservation (§6.2) --------------------------------------
	# -- retry exhaustion and duplicate suppression -----------------------
	Mutation(
		name="retry-ceiling-removed",
		path="ai_fr_hg/ai/ingestion.py",
		old='\t\t\t["retry_count", "<=", max_retries],\n',
		new="",
		breaks="A permanently failing document requeues forever, burning model budget.",
	),
	Mutation(
		name="retry-default-unbounded",
		path="ai_fr_hg/ai/ingestion.py",
		old='\treturn 2 if configured in (None, "") else max(0, cint(configured))',
		new='\treturn 999999 if configured in (None, "") else cint(configured)',
		breaks="An unconfigured site silently gets unlimited retries.",
	),
	Mutation(
		name="global-scope-skips-the-manager-check",
		path="ai_fr_hg/ai/learning.py",
		# Anchored on the guard line alone: deleting it drops through to the
		# success return below, granting Global to everyone. Kept free of a
		# literal translation call so semgrep does not read this string as
		# real translated source.
		old="\t\tif not _is_learning_manager():\n",
		new="\t\tif False:\n",
		breaks="Any user can teach the Global scope, poisoning every user's answers.",
	),
	Mutation(
		name="unknown-memory-scope-fails-open",
		path="ai_fr_hg/ai/learning.py",
		old="\t# An unrecognised scope is not a licence to show the memory to everyone.\n\t# Frappe enforces Select options on document saves, not on direct SQL,\n\t# patches or imports, so a bad value can reach this row. Fail closed:\n\t# an unreadable scope is withheld rather than broadcast.\n\treturn False",
		new="\treturn True",
		breaks="A memory with an unreadable scope is shown to every user.",
	),
	Mutation(
		name="memory-scope-ignores-the-owner",
		path="ai_fr_hg/ai/learning.py",
		old='\tif scope == "User":\n\t\treturn bool(value) and value == user',
		new='\tif scope == "User":\n\t\treturn True',
		breaks="One user's private memories are recalled into another user's context.",
	),
	Mutation(
		name="memory-scope-ignores-the-role",
		path="ai_fr_hg/ai/learning.py",
		old='\tif scope == "Role":\n\t\treturn bool(value) and value in roles',
		new='\tif scope == "Role":\n\t\treturn True',
		breaks="Role-scoped memories leak to users who do not hold the role.",
	),
	Mutation(
		name="scope-value-may-target-another-user",
		path="ai_fr_hg/ai/learning.py",
		old='\t\tif scope != "User" or value != teaching_user:',
		new='\t\tif scope != "User":',
		breaks="An ordinary user can attach a memory to a colleague's scope.",
	),
	Mutation(
		name="reconciliation-enqueues-duplicates",
		path="ai_fr_hg/ai/ingestion.py",
		old="\t\tif row.name in seen:\n\t\t\tcontinue\n\t\tseen.add(row.name)",
		new="\t\tpass",
		breaks="A row matching two reconciliation queries is processed twice per sweep.",
	),
	Mutation(
		name="retry-loses-durable-requester",
		path="ai_fr_hg/ai/ingestion.py",
		old="\t\tauthority = row.processing_requested_by or row.owner",
		new='\t\tauthority = "Administrator"',
		breaks="A retry escalates to scheduler authority instead of the original requester.",
	),
	Mutation(
		name="reconciliation-aborts-on-first-failure",
		path="ai_fr_hg/ai/ingestion.py",
		old="\t\ttry:\n\t\t\tenqueue_processing(row.name, requested_by=authority)\n\t\texcept Exception:",
		new="\t\tif True:\n\t\t\tenqueue_processing(row.name, requested_by=authority)\n\t\tif False:",
		breaks="One bad row strands every remaining document in the sweep.",
	),
	# -- event idempotency: at-least-once delivery, once-only effect ------
	Mutation(
		name="idempotency-check-removed",
		path="ai_fr_hg/ai/automation.py",
		old='\tif existing and existing.status in {"Queued", "Running", "Success"}:',
		new="\tif False:",
		breaks="A redelivered event runs again: duplicate writes, duplicate model spend.",
	),
	Mutation(
		name="revision-key-nondeterministic",
		path="ai_fr_hg/ai/automation_utils.py",
		old='\treturn f"{rule}::{doctype}::{docname}::{modified}"',
		new='\timport uuid\n\n\treturn f"{rule}::{doctype}::{docname}::{modified}::{uuid.uuid4().hex}"',
		breaks="Per-call entropy in the identity defeats deduplication entirely.",
	),
	Mutation(
		name="revision-key-ignores-the-revision",
		path="ai_fr_hg/ai/automation_utils.py",
		old='\treturn f"{rule}::{doctype}::{docname}::{modified}"',
		new='\treturn f"{rule}::{doctype}::{docname}"',
		breaks="Every later change to a document is mistaken for a duplicate and dropped.",
	),
	Mutation(
		name="terminal-failure-blocks-retry",
		path="ai_fr_hg/ai/automation.py",
		old='if existing and existing.status in {"Queued", "Running", "Success"}:',
		new="if existing:",
		breaks="A transient failure becomes permanent: the event can never be retried.",
	),
	Mutation(
		name="coalesced-change-not-recorded",
		path="ai_fr_hg/ai/automation.py",
		old='\t\t\tcoalesced = _insert_event(rule, doc, event, revision, status="Coalesced")\n\t\t\treturn {"event": coalesced, "status": "Coalesced", "skipped": True, "reason": "coalesced"}',
		new='\t\t\treturn {"event": None, "status": "Coalesced", "skipped": True, "reason": "coalesced"}',
		breaks="A superseded change vanishes with no audit record that it occurred.",
	),
	# -- audit trail integrity (Part 2 §24) -------------------------------
	Mutation(
		name="audit-actor-not-the-session-user",
		path="ai_fr_hg/ai/logging.py",
		old='\t\t\t\t"user": frappe.session.user,',
		new='\t\t\t\t"user": "Administrator",',
		breaks="Audit entries are attributed to the wrong actor, so the trail cannot answer who.",
	),
	Mutation(
		name="audit-message-unbounded",
		path="ai_fr_hg/ai/logging.py",
		old='\t\t\t\t"message": (message or "")[:1000],',
		new='\t\t\t\t"message": (message or ""),',
		breaks="Attacker-influenced text is written unbounded into a limited column.",
	),
	Mutation(
		name="audit-fail-closed-ignored",
		path="ai_fr_hg/ai/logging.py",
		old="\t\tif raise_on_error:\n\t\t\traise",
		new="\t\tif False:\n\t\t\traise",
		breaks="A security-sensitive state change proceeds unaudited instead of failing closed.",
	),
	Mutation(
		name="audit-savepoint-leaked",
		path="ai_fr_hg/ai/logging.py",
		old='\t\trelease = getattr(frappe.db, "release_savepoint", None)\n\t\tif release:\n\t\t\trelease(savepoint)',
		new="\t\tpass",
		breaks="Savepoints accumulate across a long-running job until the transaction fails.",
	),
	Mutation(
		name="audit-failure-discards-caller-transaction",
		path="ai_fr_hg/ai/logging.py",
		old="\t\tfrappe.db.rollback(save_point=savepoint)",
		new="\t\tpass",
		breaks="A failed audit leaves the caller's transaction poisoned on PostgreSQL.",
	),
	# -- stale worker recovery (Part 2 §20 heartbeat/lease) ---------------
	Mutation(
		name="stale-reaper-never-fires",
		path="ai_fr_hg/ai/ingestion.py",
		old="STALE_IN_FLIGHT_MINUTES = 30",
		new="STALE_IN_FLIGHT_MINUTES = 100000",
		breaks="A document held by a dead worker stays in-flight until a human notices.",
	),
	Mutation(
		name="stale-reaper-kills-live-workers",
		path="ai_fr_hg/ai/ingestion.py",
		old='\t\t\t["processing_heartbeat", "<", cutoff],\n',
		new="",
		breaks="A live worker's document is failed underneath it, duplicating the work.",
	),
	Mutation(
		name="stale-reaper-unbounded",
		path="ai_fr_hg/ai/ingestion.py",
		old="\t\tlimit=max(1, cint(limit)),\n\t)\n\treaped: list[dict] = []",
		new="\t)\n\treaped: list[dict] = []",
		breaks="A backlog becomes one unbounded reaping transaction.",
	),
	Mutation(
		name="stale-reaper-does-not-refresh-heartbeat",
		path="ai_fr_hg/ai/ingestion.py",
		old='\t\t\t\t"processing_message": "Stale worker",\n\t\t\t\t"processing_heartbeat": now_datetime(),',
		new='\t\t\t\t"processing_message": "Stale worker",',
		breaks="The same row is reaped on every sweep, churning audit and retries.",
	),
	# -- cooperative cancellation and retry (background execution) --------
	Mutation(
		name="ingestion-cancel-checkpoint-dead",
		path="ai_fr_hg/ai/ingestion.py",
		old='\tif frappe.db.get_value("AI Document", document_name, "cancel_requested"):',
		new="\tif False:",
		breaks="A cancelled document job runs to completion; the stop flag is ignored.",
	),
	Mutation(
		name="translation-cancel-checkpoint-dead",
		path="ai_fr_hg/ai/translation.py",
		old='\tif frappe.db.get_value("AI Translation", translation, "cancel_requested"):',
		new="\tif False:",
		breaks="A cancelled translation keeps consuming model quota to the end.",
	),
	Mutation(
		name="pipeline-backoff-ignores-cancellation",
		path="ai_fr_hg/ai/pipeline.py",
		old="\tfor _retry_tick in range(max(seconds, 0) * 10):\n\t\tif _is_cancelled(run):\n\t\t\treturn True\n\t\ttime.sleep(0.1)",
		new="\ttime.sleep(max(seconds, 0))",
		breaks="A cancelled run holds a worker slot for the whole retry backoff.",
	),
	Mutation(
		name="pipeline-cancel-authority-dropped",
		path="ai_fr_hg/ai_automation/doctype/ai_pipeline_run/ai_pipeline_run.py",
		old="\t\tif self.triggered_by != user:",
		new="\t\tif False:",
		breaks="Any signed-in user can cancel or retry another user's pipeline run.",
	),
	Mutation(
		name="pipeline-cancel-allows-terminal",
		path="ai_fr_hg/ai_automation/doctype/ai_pipeline_run/ai_pipeline_run.py",
		old='\t\tif status not in {"Queued", "Running", "Waiting Approval"}:',
		new="\t\tif False:",
		breaks="A completed run is rewritten as Cancelled, destroying its outcome.",
	),
	Mutation(
		name="pipeline-retry-accepts-corrupt-input",
		path="ai_fr_hg/ai_automation/doctype/ai_pipeline_run/ai_pipeline_run.py",
		old="\t\tif not isinstance(input_data, dict):",
		new="\t\tif False:",
		breaks="A retry starts from a non-object payload instead of refusing.",
	),
	# -- automation event claim and counters (AUTO-03 / AUTO-04) ----------
	Mutation(
		name="automation-event-claimed-twice",
		path="ai_fr_hg/ai/automation.py",
		old='\tif status != "Queued":\n\t\treturn False',
		new="\tif False:\n\t\treturn False",
		breaks="One document change runs the rule twice: double writes and double audit.",
	),
	Mutation(
		name="automation-counter-not-relative",
		path="ai_fr_hg/ai/automation.py",
		old="set run_count = coalesce(run_count, 0) + 1,",
		new="set run_count = 1,",
		breaks="AUTO-03: concurrent runs lose increments instead of accumulating.",
	),
	Mutation(
		name="automation-error-unbounded",
		path="ai_fr_hg/ai/automation.py",
		old='(now_datetime(), (error or "")[:1000], rule_name),',
		new='(now_datetime(), (error or ""), rule_name),',
		breaks="An unbounded traceback is written into a length-limited column.",
	),
	# -- permission authorities (SEC-01, agent/tool role gates) -----------
	Mutation(
		name="kb-role-grant-ignored",
		path="ai_fr_hg/ai/knowledge.py",
		old="\t\tif roles.intersection(allowed):",
		new="\t\tif True:",
		breaks="Every private knowledge base becomes searchable by every user.",
	),
	Mutation(
		name="kb-disabled-corpora-searchable",
		path="ai_fr_hg/ai/knowledge.py",
		old='filters={"enabled": 1}, fields=["name", "is_public"]',
		new='fields=["name", "is_public"]',
		breaks="Retired corpora are searched again after being disabled.",
	),
	Mutation(
		name="agent-role-gate-removed",
		path="ai_fr_hg/ai/agent.py",
		old="\tif not set(frappe.get_roles()).intersection(allowed):",
		new="\tif False:",
		breaks="A restricted agent becomes usable by any signed-in user.",
	),
	Mutation(
		name="tool-role-gate-removed",
		path="ai_fr_hg/ai/tools/__init__.py",
		old="\tif not set(frappe.get_roles()).intersection(allowed):",
		new="\tif False:",
		breaks="A restricted tool becomes invokable by any signed-in user.",
	),
	Mutation(
		name="translation-memory-scope-unauthorized",
		path="ai_fr_hg/ai/translation.py",
		old="\tif not _knowledge_base_access(scope, frappe.session.user, write=False):",
		new="\tif False:",
		breaks="SEC-01: one tenant's translation memory leaks into another's output.",
	),
	# -- task lifecycle governance (TASK-02) ------------------------------
	Mutation(
		name="task-actor-authority-dropped",
		path="ai_fr_hg/ai/tasks.py",
		old='raise frappe.PermissionError(_("You cannot perform this action on AI Task {0}.").format(doc.name))',
		new="return",
		breaks="Any signed-in user can cancel or retry another user's task.",
	),
	Mutation(
		name="task-cancelled-is-not-terminal",
		path="ai_fr_hg/ai/tasks.py",
		old='"Cancelled": set(),',
		new='"Cancelled": {"Open", "Cancelled"},',
		breaks="A terminal state becomes re-openable, so the lifecycle is not a lifecycle.",
	),
	Mutation(
		name="task-claim-trusts-stale-list",
		path="ai_fr_hg/ai/tasks.py",
		old='status = frappe.db.get_value("AI Task", row.name, "status", for_update=True)',
		new='status = row.get("status") or "Open"',
		breaks="A task already taken by another worker is claimed and run twice.",
	),
	# -- quota reservation ledger (GOV-03) --------------------------------
	Mutation(
		name="quota-request-ceiling-not-enforced",
		path="ai_fr_hg/ai/limits.py",
		old="\tif status == -1:",
		new="\tif False:",
		breaks="The GOV-03 bug: concurrent callers all pass the same pre-call check.",
	),
	Mutation(
		name="quota-reserves-nothing-for-tokens",
		path="ai_fr_hg/ai/limits.py",
		old="\testimate = max(0, cint(estimated_tokens))",
		new="\testimate = 0",
		breaks="Reserving zero makes the daily token cap an average, not a limit.",
	),
	# -- URL ingestion gate (SEC-04 application layer) --------------------
	Mutation(
		name="fetch-url-allows-embedded-credentials",
		path="ai_fr_hg/ai/ingestion.py",
		old='raise DocumentFetchError(_("Source URLs must not contain embedded credentials."))',
		new="pass",
		breaks="A URL carrying credentials is fetched, leaking them to the peer.",
	),
	Mutation(
		name="fetch-url-skips-ssrf-check",
		path="ai_fr_hg/ai/ingestion.py",
		old='enforce_local_only(url, _("Document source URL"))',
		new="pass",
		breaks="The SSRF gate is skipped; only the transport layer would refuse.",
	),
	Mutation(
		name="fetch-url-ignores-host-allowlist",
		path="ai_fr_hg/ai/ingestion.py",
		old="if not _is_manager(user) and parsed.hostname.lower() not in get_allowed_hosts():",
		new="if False and parsed.hostname.lower() not in get_allowed_hosts():",
		breaks="A non-manager may fetch any host, not just the allowlisted ones.",
	),
	# -- pipeline step configuration contract (PIPE-04) -------------------
	Mutation(
		name="step-config-types-unenforced",
		path="ai_fr_hg/ai/pipeline.py",
		old="if expected is list and not isinstance(config[key], list):",
		new="if False:",
		breaks="A Classify step accepts a string where a list of categories is required.",
	),
	Mutation(
		name="step-config-required-key-optional",
		path="ai_fr_hg/ai/pipeline.py",
		old="if key not in config:",
		new="if False:",
		breaks="A pipeline step saves without the configuration its type requires.",
	),
	# -- search telemetry redaction (SEC-07) ------------------------------
	Mutation(
		name="search-query-stored-unredacted",
		path="ai_fr_hg/ai/retrieval.py",
		old='"query": redact(str(query or ""))[:1000],',
		new='"query": str(query or "")[:1000],',
		breaks="The raw user query is persisted, bypassing operator redaction patterns.",
	),
	Mutation(
		name="search-telemetry-control-ignored",
		path="ai_fr_hg/ai/retrieval.py",
		old="if enabled is not None and not cint(enabled):\n\t\t\treturn",
		new="if False:\n\t\t\treturn",
		breaks="Telemetry is written even when the operator disabled search logging.",
	),
	Mutation(
		name="search-snippet-unbounded",
		path="ai_fr_hg/ai/retrieval.py",
		old='"snippet": redact(str(item.get("content") or ""))[:200],',
		new='"snippet": redact(str(item.get("content") or "")),',
		breaks="Full document content is copied into the telemetry row.",
	),
	# -- generic tool permissions (SEC-02 / SEC-03) -----------------------
	Mutation(
		name="count-bypasses-row-permissions",
		path="ai_fr_hg/ai/tools/query.py",
		old="names = frappe.get_list(\n\t\tdoctype,\n\t\tfilters=cleaned_filters or None,",
		new="names = frappe.get_all(\n\t\tdoctype,\n\t\tfilters=cleaned_filters or None,",
		breaks="The original SEC-02 bug: the aggregate counts rows the caller cannot list.",
	),
	Mutation(
		name="password-fieldtype-not-denied",
		path="ai_fr_hg/ai/tools/query.py",
		old='SENSITIVE_FIELD_TYPES = {"Password"}',
		new="SENSITIVE_FIELD_TYPES = set()",
		breaks="A Password field with an innocuous name becomes readable by generic tools.",
	),
	# -- folder subtree isolation (RET-07) --------------------------------
	Mutation(
		name="folder-prefix-matches-siblings",
		path="ai_fr_hg/ai/folders.py",
		old='filters.append([fieldname, "like", f"{escaped}/%"])',
		new='filters.append([fieldname, "like", f"{escaped}%"])',
		breaks="The original RET-07 bug: Home/A also matches the sibling Home/AB.",
	),
	Mutation(
		name="folder-like-metacharacters-unescaped",
		path="ai_fr_hg/ai/folders.py",
		old="escaped = escape_like(norm)",
		new="escaped = norm",
		breaks="An underscore in a folder name becomes a wildcard, widening the scope.",
	),
	# -- conversation history (CHAT-01) -----------------------------------
	Mutation(
		name="history-selects-oldest",
		path="ai_fr_hg/ai/conversation.py",
		old='order_by="sequence desc, creation desc"',
		new='order_by="sequence asc, creation asc"',
		breaks="The original CHAT-01 bug: the model receives the oldest turns.",
	),
	Mutation(
		name="history-replays-in-flight-turns",
		path="ai_fr_hg/ai/conversation.py",
		old='filters={"conversation": conversation, "status": ["in", list(HISTORY_STATUSES)]},\n\t\tfields=',
		new='filters={"conversation": conversation},\n\t\tfields=',
		breaks="Cancelled and running turns leak into context as if they had completed.",
	),
	# -- facade integrity (§21: one governed route per operation) ---------
	Mutation(
		name="domain-feedback-rewhitelisted",
		path="ai_fr_hg/ai/learning.py",
		old="def record_feedback(",
		new="@frappe.whitelist()\ndef record_feedback(",
		breaks="Re-publishing the domain writer bypasses the facade's bounded_text limit.",
	),
	Mutation(
		name="conversation-send-bypasses-facade",
		path="ai_fr_hg/ai_conversation/doctype/ai_conversation/ai_conversation.py",
		old="from ai_fr_hg.api.chat import send_message\n\n\t\treturn send_message(message, conversation=self.name, agent=self.agent)",
		new="from ai_fr_hg.ai.agent import run_agent_turn\n\n\t\treturn run_agent_turn(message, agent=self.agent, conversation=self.name)",
		breaks="Calling the agent directly skips message bounding and the write check.",
	),
	Mutation(
		name="folder-endpoint-impersonates-user",
		path="ai_fr_hg/api/folders.py",
		# Re-anchored after FILE-08 removed `get_tabs`. `delete_file` is a
		# destructive endpoint, so acting as a fixed identity here is the
		# worst form of the defect.
		old="return service_delete_file(file_name=file_name, user=frappe.session.user)",
		new='return service_delete_file(file_name=file_name, user="Administrator")',
		breaks="A destructive endpoint acts as Administrator instead of the caller.",
	),
	# -- schema and wiring integrity (real-bench failure classes) ---------
	Mutation(
		name="hook-target-typo",
		path="ai_fr_hg/hooks.py",
		old="ai_fr_hg.ai.semantic.handle_document_trashed",
		new="ai_fr_hg.ai.semantic.handle_document_trashed_typo",
		breaks="A misspelled doc_event fails silently in a worker, not at install.",
	),
	Mutation(
		name="permission-query-without-has-permission",
		path="ai_fr_hg/hooks.py",
		old='has_permission = {\n\tdoctype: "ai_fr_hg.utils.permissions.has_document_permission" for doctype in permission_query_conditions\n}',
		new="has_permission = {}",
		breaks="A row filter without a document check leaves direct reads unguarded.",
	),
	Mutation(
		name="doctype-link-to-missing-target",
		path="ai_fr_hg/ai_knowledge/doctype/ai_entity_relationship/ai_entity_relationship.json",
		old='"options": "AI Knowledge Base"',
		new='"options": "AI Knowledge Base Typo"',
		breaks="A Link to a non-existent DocType fails at bench migrate.",
	),
	Mutation(
		name="field-order-drops-a-field",
		path="ai_fr_hg/ai_knowledge/doctype/ai_entity_relationship/ai_entity_relationship.json",
		old='  "evidence_quote",\n  "first_offset",',
		new='  "first_offset",',
		breaks="A field missing from field_order is invisible in the Desk form.",
	),
	Mutation(
		name="xlsx-formulas-dropped",
		path="ai_fr_hg/ai/readers/office.py",
		old="		formulas, formula_warnings, _ = self._read_formulas(openpyxl, content)",
		new="		formulas, formula_warnings, _ = [], [], 0",
		breaks="Spreadsheet formulas must not be silently discarded.",
	),
]


def run_suite() -> bool:
	"""True when the suite passes."""
	completed = subprocess.run(
		[
			PYTHON,
			"-m",
			"pytest",
			"ai_fr_hg/tests/",
			"-x",
			"-q",
			"--no-header",
			"-p",
			"no:cacheprovider",
			# These seven genuinely require a live bench (they import frappe at
			# module scope and hit a real site). test_netguard_units.py used to
			# be listed here too, but it is bench-free -- it stands up a real
			# loopback HTTP/TLS server -- and excluding it meant SEC-04's only
			# evidence never ran outside the CI bench job.
			"--ignore=ai_fr_hg/tests/test_units.py",
			"--ignore=ai_fr_hg/tests/test_pattern_units.py",
			"--ignore=ai_fr_hg/tests/test_api_validation_units.py",
			"--ignore=ai_fr_hg/tests/test_int02_validation.py",
			"--ignore=ai_fr_hg/tests/test_int03_hierarchical.py",
			"--ignore=ai_fr_hg/tests/test_int04_whole_doc.py",
			"--ignore=ai_fr_hg/tests/test_phase_6_governance.py",
		],
		cwd=ROOT,
		capture_output=True,
		text=True,
	)
	return completed.returncode == 0


def main() -> int:
	parser = argparse.ArgumentParser()
	parser.add_argument("--list", action="store_true", help="list mutations without running")
	args = parser.parse_args()

	if args.list:
		for mutation in MUTATIONS:
			print(f"{mutation.name:42s} {mutation.path}")
		return 0

	print("Baseline: ", end="", flush=True)
	if not run_suite():
		print("FAIL — the suite must be green before mutating.")
		return 2
	print("green\n")

	caught, survived, skipped = [], [], []

	for mutation in MUTATIONS:
		target = mutation.target()
		original = target.read_text()
		if mutation.old not in original:
			skipped.append(mutation)
			print(f"  SKIP    {mutation.name} (anchor not found — mutation is stale)")
			continue
		target.write_text(original.replace(mutation.old, mutation.new, 1))
		try:
			detected = not run_suite()
		finally:
			target.write_text(original)
		if detected:
			caught.append(mutation)
			print(f"  caught  {mutation.name}")
		else:
			survived.append(mutation)
			print(f"  SURVIVED {mutation.name}")
			print(f"           unverified: {mutation.breaks}")

	total = len(MUTATIONS)
	print(f"\n{len(caught)}/{total} caught, {len(survived)} survived, {len(skipped)} stale")
	if survived:
		print("\nSurviving mutations mark behaviour no test verifies:")
		for mutation in survived:
			print(f"  - {mutation.name}: {mutation.breaks}")
	return 1 if (survived or skipped) else 0


if __name__ == "__main__":
	raise SystemExit(main())
