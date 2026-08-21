# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Part 2 platform contracts — Frappe-free unit coverage.

Covers the requirements from Part 2 that are verifiable without a bench:

* §13.3 retrieval traceability — a result must identify its extraction origin,
  processing version, and retrieval method, not just its document.
* §18.2 pattern results must carry a processing timestamp.
* §20/§26 background work must be bounded and resumable.
* §21.3 API endpoints must stay thin.
* §25 operational polling must not leak timers.

Bench-dependent persistence and browser behaviour remain Phase 7 scope; these
assert the contracts those paths depend on.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest import TestCase

APP = Path(__file__).resolve().parents[1]


def _doctype(relative: str) -> dict:
	return json.loads((APP / relative).read_text())


def _field(meta: dict, fieldname: str) -> dict:
	return next(f for f in meta["fields"] if f["fieldname"] == fieldname)


# ---------------------------------------------------------------------------
# §13.3 — retrieval traceability
# ---------------------------------------------------------------------------


class TestRetrievalTraceability(TestCase):
	"""Every retrieval result must be traceable back to its extraction."""

	def test_retrieved_chunk_declares_the_full_provenance_contract(self):
		source = (APP / "ai/retrieval.py").read_text()
		for field in (
			"reader_used",
			"extractor_version",
			"extracted_on",
			"retrieval_method",
		):
			self.assertIn(f"{field}:", source, f"RetrievedChunk must declare {field}")

	def test_provenance_is_serialized_to_api_consumers(self):
		"""Fields that never reach as_dict() are invisible to callers."""
		source = (APP / "ai/retrieval.py").read_text()
		as_dict = source[source.index("def as_dict(self) -> dict:") :][:1600]
		for field in ("reader_used", "extractor_version", "extracted_on", "retrieval_method"):
			self.assertIn(f'"{field}"', as_dict)

	def test_retrieval_method_distinguishes_the_three_paths(self):
		import sys
		import types

		if "frappe" not in sys.modules:
			stub = types.ModuleType("frappe")
			stub._ = lambda value: value
			sys.modules["frappe"] = stub

		# Import the pure helper directly from source to avoid a bench import.
		namespace: dict = {}
		source = (APP / "ai/retrieval.py").read_text()
		start = source.index("def _retrieval_method(")
		end = source.index("def _extraction_provenance(")
		exec(compile(source[start:end], "retrieval_method", "exec"), namespace)
		method = namespace["_retrieval_method"]

		self.assertEqual(method("a", {"a": 1}, {"a": 1}), "hybrid")
		self.assertEqual(method("a", {"a": 1}, {}), "semantic")
		self.assertEqual(method("a", {}, {"a": 1}), "keyword")
		self.assertEqual(method("a", {}, {}), "unknown")

	def test_extraction_provenance_survives_malformed_evidence(self):
		"""A bad evidence blob must degrade, never fail a search."""
		namespace: dict = {"json": json}
		source = (APP / "ai/retrieval.py").read_text()
		start = source.index("def _extraction_provenance(")
		end = source.index("def _log_search(")
		exec(compile(source[start:end], "provenance", "exec"), namespace)
		provenance = namespace["_extraction_provenance"]

		class Meta:
			def __init__(self, evidence):
				self.extraction_evidence = evidence

		self.assertEqual(provenance(None), ({}, None))
		self.assertEqual(provenance(Meta(None)), ({}, None))
		self.assertEqual(provenance(Meta("not json")), ({}, None))
		self.assertEqual(provenance(Meta("[]")), ({}, None))
		versions, extracted_on = provenance(
			Meta(json.dumps({"versions": {"app": "0.0.1"}, "extracted_on": "2026-08-21T10:00:00+00:00"}))
		)
		self.assertEqual(versions["app"], "0.0.1")
		self.assertEqual(extracted_on, "2026-08-21T10:00:00+00:00")

	def test_retrieval_loads_the_fields_provenance_needs(self):
		"""Declaring the contract is useless if the query omits the columns."""
		source = (APP / "ai/retrieval.py").read_text()
		hydrate = source[source.index("def _hydrate(") :][:2200]
		self.assertIn('"reader_used"', hydrate)
		self.assertIn('"extraction_evidence"', hydrate)


# ---------------------------------------------------------------------------
# §18.2 — pattern results carry a processing timestamp
# ---------------------------------------------------------------------------


class TestPatternResultContract(TestCase):
	def test_pattern_entity_records_when_it_was_produced(self):
		meta = _doctype("ai_knowledge/doctype/ai_pattern_entity/ai_pattern_entity.json")
		field = _field(meta, "last_scanned_on")
		self.assertEqual(field["fieldtype"], "Datetime")
		self.assertEqual(field.get("read_only"), 1)

	def test_scan_stamps_the_timestamp_on_update_and_insert(self):
		"""Rows are written with update_modified=False, so `modified` cannot carry this."""
		source = (APP / "ai/patterns.py").read_text()
		self.assertIn("scanned_at = now_datetime()", source)
		self.assertIn('"last_scanned_on": scanned_at', source)
		self.assertIn("entity_doc.last_scanned_on = scanned_at", source)

	def test_semantic_scan_also_stamps_the_timestamp(self):
		source = (APP / "ai/semantic.py").read_text()
		self.assertIn("scanned_at = now_datetime()", source)
		self.assertIn('"last_scanned_on": scanned_at', source)

	def test_pattern_result_contract_is_complete(self):
		"""§18.2: detection source, confidence, evidence, and timestamp."""
		meta = _doctype("ai_knowledge/doctype/ai_pattern_entity/ai_pattern_entity.json")
		present = {f["fieldname"] for f in meta["fields"]}
		for required in (
			"extraction_method",  # detection source
			"confidence",
			"context_quote",  # supporting evidence
			"first_offset",
			"source_checksum",
			"last_scanned_on",  # processing timestamp
		):
			self.assertIn(required, present)

	def test_timestamp_is_exposed_through_the_api(self):
		self.assertIn("last_scanned_on", (APP / "api/knowledge.py").read_text())
		self.assertIn("last_scanned_on", (APP / "ai/patterns.py").read_text())


# ---------------------------------------------------------------------------
# §20 / §26 — bounded, resumable background work
# ---------------------------------------------------------------------------


class TestBoundedRetention(TestCase):
	def test_retention_deletes_in_bounded_batches(self):
		source = (APP / "tasks.py").read_text()
		self.assertIn("CLEANUP_BATCH_SIZE", source)
		self.assertIn("CLEANUP_MAX_PER_RUN", source)
		self.assertIn("def delete_expired_rows(", source)

	def test_retention_no_longer_issues_an_unbounded_delete(self):
		"""The original `frappe.db.delete(doctype, {creation: <})` was unbounded."""
		source = (APP / "tasks.py").read_text()
		cleanup = source[source.index("def cleanup_logs()") :]
		cleanup = cleanup[: cleanup.index("def backup_knowledge()")]
		self.assertNotIn('frappe.db.delete(doctype, {"creation"', cleanup)
		self.assertIn("delete_expired_rows(", cleanup)

	def test_a_failing_doctype_does_not_abort_the_whole_run(self):
		source = (APP / "tasks.py").read_text()
		cleanup = source[source.index("def cleanup_logs()") :]
		cleanup = cleanup[: cleanup.index("def backup_knowledge()")]
		self.assertIn("except Exception:", cleanup)
		self.assertIn("frappe.log_error(", cleanup)

	def test_batched_deletion_is_resumable_and_reports_remainder(self):
		source = (APP / "tasks.py").read_text()
		fn = source[source.index("def delete_expired_rows(") :]
		fn = fn[: fn.index("def cleanup_logs()")]
		# Committed per batch so an interruption keeps completed work.
		self.assertIn("frappe.db.commit()", fn)
		# Ordered so progress is monotonic rather than re-scanning randomly.
		self.assertIn('order_by="creation asc"', fn)
		self.assertIn('"remaining"', fn)

	def test_search_query_retention_runs_through_the_same_bounded_path(self):
		source = (APP / "tasks.py").read_text()
		cleanup = source[source.index("def cleanup_logs()") :]
		cleanup = cleanup[: cleanup.index("def backup_knowledge()")]
		self.assertIn('"AI Search Query": 30', cleanup)
		self.assertNotIn('frappe.db.delete("AI Search Query"', cleanup)


# ---------------------------------------------------------------------------
# §25 — operational polling lifecycle
# ---------------------------------------------------------------------------


class TestOperationsPollingLifecycle(TestCase):
	"""Frappe keeps Desk pages in the DOM; an uncleared timer polls forever."""

	SOURCE = "ai_operations/page/ai_operations/ai_operations.js"

	def test_polling_is_stopped_when_the_page_is_hidden(self):
		source = (APP / self.SOURCE).read_text()
		self.assertIn("on_page_hide", source)
		self.assertIn("clearInterval", source)

	def test_polling_restarts_when_a_cached_page_is_shown(self):
		source = (APP / self.SOURCE).read_text()
		show = source[source.index("on_page_show") :][:400]
		self.assertIn("start_polling", show)

	def test_start_polling_is_idempotent(self):
		"""Revisiting the page must not stack a second interval."""
		source = (APP / self.SOURCE).read_text()
		start = source[source.index("start_polling() {") :][:260]
		self.assertIn("if (this.timer) return;", start)

	def test_every_interval_in_the_app_is_cleared(self):
		"""Any setInterval without a matching clearInterval is a leak."""
		for path in APP.rglob("*.js"):
			if "node_modules" in str(path):
				continue
			source = path.read_text()
			if "setInterval" in source:
				self.assertIn("clearInterval", source, f"{path.name} starts a timer it never clears")


# ---------------------------------------------------------------------------
# §21.3 — API endpoints stay thin
# ---------------------------------------------------------------------------


class TestApiFacadeThinness(TestCase):
	def test_new_endpoints_delegate_to_a_service(self):
		"""The Part 1/2 endpoints must authorize and delegate, not orchestrate."""
		source = (APP / "api/knowledge.py").read_text()
		for endpoint in ("def scan_semantic_entities(", "def get_entity_relationships("):
			body = source[source.index(endpoint) :]
			body = body[: body.index("@frappe.whitelist()", 1)] if "@frappe.whitelist()" in body[1:] else body
			self.assertTrue(
				"check_permission" in body or "has_permission" in body,
				f"{endpoint} must enforce access",
			)

	def test_semantic_endpoint_refuses_when_the_feature_is_disabled(self):
		source = (APP / "api/knowledge.py").read_text()
		body = source[source.index("def scan_semantic_entities(") :][:1400]
		self.assertIn("semantic_enabled()", body)
		self.assertIn("frappe.throw", body)


# ---------------------------------------------------------------------------
# §23 — isolation for machine-written analysis rows
# ---------------------------------------------------------------------------


class TestAnalysisRowIsolation(TestCase):
	def test_every_knowledge_scoped_doctype_has_a_permission_query(self):
		hooks = (APP / "hooks.py").read_text()
		for doctype in ("AI Pattern Entity", "AI Entity Relationship", "AI Document Chunk"):
			self.assertIn(f'"{doctype}":', hooks, f"{doctype} needs a permission query")

	def test_analysis_rows_are_read_only_for_non_managers(self):
		source = (APP / "utils/permissions.py").read_text()
		self.assertIn('{"AI Pattern Entity", "AI Entity Relationship"}', source)
		self.assertIn("_is_read(permission_type)", source)


# ---------------------------------------------------------------------------
# §24 — audit coverage for AI interactions
# ---------------------------------------------------------------------------


class TestAiInteractionAudit(TestCase):
	"""§24 requires AI interactions and processing history to be recorded."""

	def test_semantic_extraction_is_audited(self):
		source = (APP / "ai/semantic.py").read_text()
		self.assertIn("write_audit_log(", source)
		self.assertIn('action="Semantic Entities Extracted"', source)

	def test_audit_records_what_was_discarded_not_only_what_was_kept(self):
		"""The grounding filter is only trustworthy if its rejections are visible."""
		source = (APP / "ai/semantic.py").read_text()
		audit = source[source.index('action="Semantic Entities Extracted"') :][:900]
		self.assertIn('"rejected"', audit)
		self.assertIn('"confidence_floor"', audit)
		self.assertIn('"model"', audit)

	def test_audit_uses_a_valid_category(self):
		categories = set(
			_field(_doctype("ai_operations/doctype/ai_audit_log/ai_audit_log.json"), "category")[
				"options"
			].split("\n")
		)
		source = (APP / "ai/semantic.py").read_text()
		audit = source[source.index('action="Semantic Entities Extracted"') :][:400]
		used = audit.split('category="')[1].split('"')[0]
		self.assertIn(used, categories)

	def test_audit_links_back_to_the_source_document(self):
		source = (APP / "ai/semantic.py").read_text()
		audit = source[source.index('action="Semantic Entities Extracted"') :][:900]
		self.assertIn('reference_doctype="AI Document"', audit)
		self.assertIn("reference_name=document", audit)


# ---------------------------------------------------------------------------
# §22.3 — data integrity on inferred rows
# ---------------------------------------------------------------------------


class TestRelationshipIntegrity(TestCase):
	META = "ai_knowledge/doctype/ai_entity_relationship/ai_entity_relationship.json"

	def test_relationship_records_when_it_was_produced(self):
		field = _field(_doctype(self.META), "last_scanned_on")
		self.assertEqual(field["fieldtype"], "Datetime")
		self.assertEqual(field.get("read_only"), 1)

	def test_relationship_scan_stamps_the_timestamp(self):
		source = (APP / "ai/semantic.py").read_text()
		sync = source[source.index("def _sync_relationships(") :]
		self.assertIn('"last_scanned_on": scanned_at', sync)

	def test_field_order_matches_declared_fields(self):
		"""A field_order/fields mismatch breaks the Desk form silently."""
		meta = _doctype(self.META)
		self.assertEqual(sorted(meta["field_order"]), sorted(f["fieldname"] for f in meta["fields"]))

	def test_provenance_reaches_api_consumers(self):
		source = (APP / "api/knowledge.py").read_text()
		body = source[source.index("def get_entity_relationships(") :][:1400]
		for field in ("evidence_quote", "confidence", "model_used", "last_scanned_on"):
			self.assertIn(f'"{field}"', body)


# ---------------------------------------------------------------------------
# §15.3 — sourced information vs generated interpretation
# ---------------------------------------------------------------------------


class TestResponseGroundingDisclosure(TestCase):
	"""§15.3: a response must not present generated content as verified fact."""

	def test_agent_reply_declares_its_grounding(self):
		source = (APP / "ai/agent.py").read_text()
		self.assertIn('"grounding": {', source)
		for key in ('"has_context"', '"citation_count"', '"strict"', '"basis"'):
			self.assertIn(key, source)

	def test_every_return_path_declares_grounding(self):
		"""A path that omits it would silently look unverified-but-unlabelled."""
		source = (APP / "ai/agent.py").read_text()
		# Both the normal completion and the strict-grounding fallback.
		self.assertEqual(source.count('"grounding": {'), 2)

	def test_basis_distinguishes_sourced_unsupported_and_fallback(self):
		source = (APP / "ai/agent.py").read_text()
		self.assertIn('"basis": "sources" if citations else "unsupported"', source)
		self.assertIn('"basis": "fallback"', source)

	def test_grounding_reaches_the_api_consumer(self):
		"""The chat endpoint returns the agent result, so the field must survive."""
		source = (APP / "api/chat.py").read_text()
		body = source[source.index("result = run_agent_turn(") :][:600]
		self.assertIn("return result", body)
		self.assertNotIn("del result[", body)

	def test_reloaded_conversations_keep_the_disclosure(self):
		"""A refresh must not turn an unsupported answer into a sourced-looking one."""
		import json as _json

		source = (APP / "ai/conversation.py").read_text()
		namespace: dict = {"json": _json}
		start = source.index("def _parse_json_field")
		end = source.index("def get_conversation_payload")
		exec(compile(source[start:end], "conversation", "exec"), namespace)
		decorate = namespace["_decorate_messages"]

		rows = [
			{"role": "User", "content": "hi", "citations": None, "learned_context": None},
			{
				"role": "Assistant",
				"content": "grounded",
				"citations": _json.dumps([{"document": "D1"}]),
				"learned_context": None,
			},
			{"role": "Assistant", "content": "recall", "citations": None, "learned_context": None},
		]
		user, sourced, unsupported = decorate(rows)
		self.assertNotIn("grounding", user)
		self.assertEqual(sourced["grounding"]["basis"], "sources")
		self.assertEqual(unsupported["grounding"]["basis"], "unsupported")

	def test_assistant_ui_renders_the_disclosure(self):
		source = (APP / "ai_core/page/ai_assistant/ai_assistant.js").read_text()
		self.assertIn("ai-grounding-note", source)
		self.assertIn('message.grounding.basis !== "sources"', source)
		# The live turn must forward the field the badge depends on.
		self.assertIn("grounding: response.grounding", source)

	def test_disclosure_has_a_style(self):
		"""An unstyled warning is easy to miss, which defeats the purpose."""
		self.assertIn(".ai-grounding-note", (APP / "public/scss/ai_assistant.scss").read_text())
