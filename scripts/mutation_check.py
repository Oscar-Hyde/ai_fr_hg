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
		old="return service_tabs(user=frappe.session.user)",
		new='return service_tabs(user="Administrator")',
		breaks="A published endpoint must act as the caller, never a fixed identity.",
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
			"--ignore=ai_fr_hg/tests/test_units.py",
			"--ignore=ai_fr_hg/tests/test_pattern_units.py",
			"--ignore=ai_fr_hg/tests/test_netguard_units.py",
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
