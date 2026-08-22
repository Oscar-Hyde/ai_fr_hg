# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Executing behaviour tests for the Part 2 platform requirements.

These replace the source-text assertions in `test_part2_platform_contracts.py`
with tests that **run the application's own functions** against the in-memory
bench in `fakebench.py`, then assert on observed state and return values.

Why this matters: a test of the form ``assertIn("last_scanned_on", source)``
passes when the field is written to the wrong row, when the value is `None`,
and when the function raises before reaching it. It fails when someone renames
a local variable. It measures text, not behaviour.

Scope honesty: the harness models Frappe's *observable semantics*, not a
database. These tests prove control flow, state transitions, filtering,
idempotency, error handling and side effects. They do **not** prove SQL,
isolation levels, index usage, or migrations — that remains bench work.
"""

from __future__ import annotations

import json
import types
from pathlib import Path
from unittest import TestCase

from ai_fr_hg.tests.fakebench import (
	FakeBench,
	PermissionError_,
	ValidationError,
	import_app,
	install,
)

APP = Path(__file__).resolve().parents[1]

LOG_DOCTYPES = (
	"AI Execution Log",
	"AI Service Health Log",
	"AI Audit Log",
	"AI Search Query",
)


def _bench_with_logs(**retention) -> FakeBench:
	bench = install(FakeBench())
	for doctype in LOG_DOCTYPES:
		bench.register_doctype(doctype, [{"fieldname": "name"}, {"fieldname": "creation"}])
	bench.register_doctype("AI Platform Settings", [], is_single=True)
	bench.db.singles["AI Platform Settings"] = {
		"execution_log_retention_days": 0,
		"health_log_retention_days": 0,
		"audit_log_retention_days": 0,
		**retention,
	}
	return bench


def _seed(bench, doctype, count, creation, prefix="R"):
	for index in range(count):
		bench.db.insert_row(doctype, {"name": f"{prefix}-{index:05d}", "creation": creation})


# ---------------------------------------------------------------------------
# §20.4 / §26 / §27 — retention actually deletes the right rows
# ---------------------------------------------------------------------------


class TestRetentionBehaviour(TestCase):
	OLD = "2020-01-01 00:00:00"
	RECENT = "2026-08-21 00:00:00"

	def test_only_expired_rows_are_deleted(self):
		bench = _bench_with_logs(execution_log_retention_days=30)
		_seed(bench, "AI Execution Log", 1200, self.OLD, prefix="OLD")
		_seed(bench, "AI Execution Log", 5, self.RECENT, prefix="NEW")

		tasks = import_app("ai_fr_hg.tasks")

		tasks.cleanup_logs()

		survivors = sorted(bench.get_all("AI Execution Log", pluck="name"))
		self.assertEqual(len(survivors), 5)
		self.assertTrue(all(name.startswith("NEW-") for name in survivors))

	def test_deletion_is_committed_per_batch_not_once_at_the_end(self):
		"""Per-batch commits are what make an interrupted run resumable."""
		bench = _bench_with_logs(execution_log_retention_days=30)
		_seed(bench, "AI Execution Log", 1200, self.OLD)

		tasks = import_app("ai_fr_hg.tasks")

		tasks.cleanup_logs()

		# 1200 rows / 500 per batch = 3 batches, so 3 commits.
		self.assertEqual(bench.db.committed, 3)

	def test_a_run_stops_at_the_ceiling_and_reports_the_remainder(self):
		bench = _bench_with_logs()
		_seed(bench, "AI Execution Log", 25_000, self.OLD)

		tasks = import_app("ai_fr_hg.tasks")

		result = tasks.delete_expired_rows("AI Execution Log", "2026-01-01")

		self.assertEqual(result["deleted"], tasks.CLEANUP_MAX_PER_RUN)
		self.assertTrue(result["remaining"])
		# The backlog survives for the next run rather than being lost.
		self.assertEqual(bench.db.count("AI Execution Log"), 25_000 - tasks.CLEANUP_MAX_PER_RUN)

	def test_the_next_run_resumes_and_finishes_the_backlog(self):
		bench = _bench_with_logs()
		_seed(bench, "AI Execution Log", 25_000, self.OLD)

		tasks = import_app("ai_fr_hg.tasks")

		first = tasks.delete_expired_rows("AI Execution Log", "2026-01-01")
		second = tasks.delete_expired_rows("AI Execution Log", "2026-01-01")

		self.assertTrue(first["remaining"])
		self.assertFalse(second["remaining"])
		self.assertEqual(bench.db.count("AI Execution Log"), 0)

	def test_zero_retention_disables_deletion_for_that_doctype(self):
		bench = _bench_with_logs(execution_log_retention_days=0)
		_seed(bench, "AI Execution Log", 10, self.OLD)

		tasks = import_app("ai_fr_hg.tasks")

		tasks.cleanup_logs()
		self.assertEqual(bench.db.count("AI Execution Log"), 10)

	def test_a_failing_doctype_does_not_stop_the_others(self):
		bench = _bench_with_logs(execution_log_retention_days=30, audit_log_retention_days=30)
		_seed(bench, "AI Execution Log", 3, self.OLD, prefix="E")
		_seed(bench, "AI Audit Log", 3, self.OLD, prefix="A")

		tasks = import_app("ai_fr_hg.tasks")

		original = tasks.delete_expired_rows
		calls: list[str] = []

		def flaky(doctype, cutoff, **kwargs):
			calls.append(doctype)
			if doctype == "AI Execution Log":
				raise RuntimeError("table is locked")
			return original(doctype, cutoff, **kwargs)

		tasks.delete_expired_rows = flaky
		try:
			tasks.cleanup_logs()
		finally:
			tasks.delete_expired_rows = original

		# The failure was recorded, and the later DocType still ran.
		self.assertEqual(bench.db.count("AI Execution Log"), 3)
		self.assertEqual(bench.db.count("AI Audit Log"), 0)
		self.assertTrue(any("retention cleanup failed" in (e["title"] or "") for e in bench.errors))

	def test_search_queries_use_the_fixed_thirty_day_window(self):
		bench = _bench_with_logs()
		_seed(bench, "AI Search Query", 4, self.OLD, prefix="Q")
		_seed(bench, "AI Search Query", 2, self.RECENT, prefix="QNEW")

		tasks = import_app("ai_fr_hg.tasks")

		tasks.cleanup_logs()
		survivors = bench.get_all("AI Search Query", pluck="name")
		self.assertEqual(sorted(survivors), ["QNEW-00000", "QNEW-00001"])

	def test_nothing_to_delete_is_a_no_op(self):
		bench = _bench_with_logs(execution_log_retention_days=30)

		tasks = import_app("ai_fr_hg.tasks")

		result = tasks.delete_expired_rows("AI Execution Log", "2026-01-01")
		self.assertEqual(result["deleted"], 0)
		self.assertEqual(result["batches"], 0)
		self.assertEqual(bench.db.committed, 0)


# ---------------------------------------------------------------------------
# §13.3 — retrieval provenance is computed, not just declared
# ---------------------------------------------------------------------------


class TestRetrievalProvenanceBehaviour(TestCase):
	def _provenance(self):
		install(FakeBench())
		from ai_fr_hg.ai.retrieval import _extraction_provenance

		return _extraction_provenance

	def _method(self):
		install(FakeBench())
		from ai_fr_hg.ai.retrieval import _retrieval_method

		return _retrieval_method

	def test_versions_and_timestamp_are_read_from_stored_evidence(self):
		provenance = self._provenance()

		class Meta:
			extraction_evidence = json.dumps(
				{
					"versions": {"app": "0.0.1", "reader": "1.1", "library_version": "6.16.1"},
					"extracted_on": "2026-08-21T10:00:00+00:00",
				}
			)

		versions, extracted_on = provenance(Meta())
		self.assertEqual(versions["reader"], "1.1")
		self.assertEqual(versions["library_version"], "6.16.1")
		self.assertEqual(extracted_on, "2026-08-21T10:00:00+00:00")

	def test_malformed_evidence_degrades_instead_of_raising(self):
		provenance = self._provenance()

		class Meta:
			def __init__(self, value):
				self.extraction_evidence = value

		for bad in (None, "", "not json", "[]", '"a string"', "{{{"):
			versions, extracted_on = provenance(Meta(bad))
			self.assertEqual(versions, {})
			self.assertIsNone(extracted_on)

	def test_legacy_evidence_without_versions_yields_empty_provenance(self):
		provenance = self._provenance()

		class Meta:
			extraction_evidence = json.dumps({"reader": "PDF", "detector": {"family": "pdf"}})

		versions, extracted_on = provenance(Meta())
		self.assertEqual(versions, {})
		self.assertIsNone(extracted_on)

	def test_retrieval_method_reflects_which_path_found_the_chunk(self):
		method = self._method()
		self.assertEqual(method("c1", {"c1": 0.8}, {"c1": 0.4}), "hybrid")
		self.assertEqual(method("c1", {"c1": 0.8}, {}), "semantic")
		self.assertEqual(method("c1", {}, {"c1": 0.4}), "keyword")
		self.assertEqual(method("c1", {}, {}), "unknown")

	def test_serialized_result_carries_the_whole_evidence_chain(self):
		install(FakeBench())
		from ai_fr_hg.ai.retrieval import RetrievedChunk

		payload = RetrievedChunk(
			chunk="CH1",
			document="DOC1",
			document_title="Q3",
			knowledge_base="KB",
			content="text",
			score=0.91,
			reader_used="PDF",
			extractor_version={"app": "0.0.1", "reader": "1.1"},
			extracted_on="2026-08-21T10:00:00+00:00",
			retrieval_method="hybrid",
		).as_dict()

		# Where did it come from, who produced it, which version, how was it found.
		self.assertEqual(payload["document"], "DOC1")
		self.assertEqual(payload["reader_used"], "PDF")
		self.assertEqual(payload["extractor_version"]["reader"], "1.1")
		self.assertEqual(payload["extracted_on"], "2026-08-21T10:00:00+00:00")
		self.assertEqual(payload["retrieval_method"], "hybrid")

	def test_default_result_has_no_invented_provenance(self):
		"""Absent provenance must read as absent, never as a plausible default."""
		install(FakeBench())
		from ai_fr_hg.ai.retrieval import RetrievedChunk

		payload = RetrievedChunk(
			chunk="CH1", document="D", document_title="D", knowledge_base="KB", content="c"
		).as_dict()
		self.assertIsNone(payload["reader_used"])
		self.assertEqual(payload["extractor_version"], {})
		self.assertIsNone(payload["extracted_on"])
		self.assertEqual(payload["retrieval_method"], "unknown")


# ---------------------------------------------------------------------------
# §15.3 — the disclosure survives a reload
# ---------------------------------------------------------------------------


class TestGroundingDisclosureBehaviour(TestCase):
	def _decorate(self):
		install(FakeBench())
		from ai_fr_hg.ai.conversation import _decorate_messages

		return _decorate_messages

	def test_a_cited_answer_is_reported_as_sourced(self):
		decorate = self._decorate()
		[message] = decorate(
			[
				{
					"role": "Assistant",
					"content": "answer",
					"citations": json.dumps([{"document": "D1"}, {"document": "D2"}]),
					"learned_context": None,
				}
			]
		)
		self.assertEqual(message["grounding"]["basis"], "sources")
		self.assertEqual(message["grounding"]["citation_count"], 2)
		self.assertTrue(message["grounding"]["has_context"])

	def test_an_uncited_answer_is_reported_as_unsupported(self):
		decorate = self._decorate()
		[message] = decorate(
			[{"role": "Assistant", "content": "recall", "citations": None, "learned_context": None}]
		)
		self.assertEqual(message["grounding"]["basis"], "unsupported")
		self.assertEqual(message["grounding"]["citation_count"], 0)

	def test_user_messages_carry_no_grounding_claim(self):
		decorate = self._decorate()
		[message] = decorate(
			[{"role": "User", "content": "question", "citations": None, "learned_context": None}]
		)
		self.assertNotIn("grounding", message)

	def test_corrupt_citation_json_does_not_fabricate_grounding(self):
		"""A parse failure must fall back to 'unsupported', never to 'sources'."""
		decorate = self._decorate()
		[message] = decorate(
			[
				{
					"role": "Assistant",
					"content": "answer",
					"citations": "{not valid json",
					"learned_context": None,
				}
			]
		)
		self.assertEqual(message["grounding"]["basis"], "unsupported")

	def test_disclosure_matches_the_citations_actually_returned(self):
		decorate = self._decorate()
		[message] = decorate(
			[
				{
					"role": "Assistant",
					"content": "a",
					"citations": json.dumps([{"document": "D1"}]),
					"learned_context": None,
				}
			]
		)
		self.assertEqual(message["grounding"]["citation_count"], len(message["citations"]))


# ---------------------------------------------------------------------------
# §24 — the audit entry is really written, with the rejection detail
# ---------------------------------------------------------------------------


def _semantic_bench() -> FakeBench:
	bench = install(FakeBench())
	bench.register_doctype(
		"AI Document",
		[{"fieldname": f} for f in ("name", "content", "knowledge_base", "checksum")],
	)
	bench.register_doctype(
		"AI Pattern Entity",
		[
			{"fieldname": f}
			for f in (
				"name",
				"document",
				"knowledge_base",
				"entity_type",
				"extraction_method",
				"value",
				"normalized_value",
				"occurrences",
				"first_offset",
				"context_quote",
				"confidence",
				"model_used",
				"source_checksum",
				"last_scanned_on",
			)
		],
	)
	bench.register_doctype(
		"AI Entity Relationship",
		[
			{"fieldname": f}
			for f in (
				"name",
				"document",
				"knowledge_base",
				"subject",
				"object",
				"relationship_type",
				"evidence_quote",
				"first_offset",
				"confidence",
				"model_used",
				"source_checksum",
				"last_scanned_on",
			)
		],
	)
	bench.register_doctype("AI Audit Log", [{"fieldname": "name"}])
	bench.register_doctype("AI Platform Settings", [], is_single=True)
	bench.db.singles["AI Platform Settings"] = {
		"semantic_entities_enabled": 1,
		"semantic_confidence_floor": 50,
	}
	bench.db.insert_row(
		"AI Document",
		{
			"name": "DOC1",
			"content": "Alice Novak works for Cyberdyne Systems in Sofia.",
			"knowledge_base": "KB1",
			"checksum": "abc123",
		},
	)
	return bench


def _patch_extraction(monkeypatched_module, payload):
	"""Replace the model call with a fixed payload, keeping all other logic real."""
	monkeypatched_module.extract_semantic = lambda text, model=None, document=None: payload


class TestSemanticAuditBehaviour(TestCase):
	def test_scan_writes_an_audit_row_recording_rejections(self):
		bench = _semantic_bench()
		semantic = import_app("ai_fr_hg.ai.semantic")

		_patch_extraction(
			semantic,
			{
				"entities": [
					{
						"entity_type": "person",
						"value": "Alice Novak",
						"normalized_value": "alice novak",
						"confidence": 90.0,
						"first_offset": 0,
						"context_quote": "Alice Novak works",
						"occurrences": 1,
					}
				],
				"relationships": [],
				"rejected": {"ungrounded": 3, "low_confidence": 1, "invalid": 0},
				"model": "llama3.1:8b",
			},
		)

		semantic.scan_document_semantic("DOC1")

		audits = bench.get_all("AI Audit Log")
		self.assertEqual(len(audits), 1)
		entry = audits[0]
		self.assertEqual(entry["action"], "Semantic Entities Extracted")
		self.assertEqual(entry["reference_name"], "DOC1")
		details = json.loads(entry["details"]) if isinstance(entry["details"], str) else entry["details"]
		# The grounding filter is only trustworthy if its rejections are visible.
		self.assertEqual(details["rejected"]["ungrounded"], 3)
		self.assertEqual(details["model"], "llama3.1:8b")
		self.assertEqual(details["confidence_floor"], 50)

	def test_entities_are_persisted_with_provenance(self):
		bench = _semantic_bench()
		semantic = import_app("ai_fr_hg.ai.semantic")

		_patch_extraction(
			semantic,
			{
				"entities": [
					{
						"entity_type": "organization",
						"value": "Cyberdyne Systems",
						"normalized_value": "cyberdyne systems",
						"confidence": 88.0,
						"first_offset": 25,
						"context_quote": "works for Cyberdyne Systems",
						"occurrences": 1,
					}
				],
				"relationships": [],
				"rejected": {},
				"model": "llama3.1:8b",
			},
		)

		result = semantic.scan_document_semantic("DOC1")

		self.assertEqual(result["entities"], 1)
		self.assertEqual(result["created"], 1)
		[row] = bench.get_all("AI Pattern Entity")
		self.assertEqual(row["extraction_method"], "semantic")
		self.assertEqual(row["confidence"], 88.0)
		self.assertEqual(row["model_used"], "llama3.1:8b")
		self.assertEqual(row["source_checksum"], "abc123")
		self.assertIsNotNone(row["last_scanned_on"])

	def test_rescanning_updates_in_place_instead_of_duplicating(self):
		bench = _semantic_bench()
		semantic = import_app("ai_fr_hg.ai.semantic")

		payload = {
			"entities": [
				{
					"entity_type": "person",
					"value": "Alice Novak",
					"normalized_value": "alice novak",
					"confidence": 90.0,
					"first_offset": 0,
					"context_quote": "Alice Novak",
					"occurrences": 1,
				}
			],
			"relationships": [],
			"rejected": {},
			"model": "m1",
		}
		_patch_extraction(semantic, payload)

		first = semantic.scan_document_semantic("DOC1")
		second = semantic.scan_document_semantic("DOC1")

		self.assertEqual(first["created"], 1)
		self.assertEqual(second["created"], 0)
		self.assertEqual(second["updated"], 1)
		self.assertEqual(bench.db.count("AI Pattern Entity"), 1)

	def test_entities_that_disappear_on_rescan_are_removed(self):
		bench = _semantic_bench()
		semantic = import_app("ai_fr_hg.ai.semantic")

		_patch_extraction(
			semantic,
			{
				"entities": [
					{
						"entity_type": "person",
						"value": "Alice Novak",
						"normalized_value": "alice novak",
						"confidence": 90.0,
						"first_offset": 0,
						"context_quote": "q",
						"occurrences": 1,
					}
				],
				"relationships": [],
				"rejected": {},
				"model": "m1",
			},
		)
		semantic.scan_document_semantic("DOC1")
		self.assertEqual(bench.db.count("AI Pattern Entity"), 1)

		_patch_extraction(semantic, {"entities": [], "relationships": [], "rejected": {}, "model": "m1"})
		result = semantic.scan_document_semantic("DOC1")

		self.assertEqual(result["removed"], 1)
		self.assertEqual(bench.db.count("AI Pattern Entity"), 0)

	def test_relationships_are_persisted_with_evidence_and_timestamp(self):
		bench = _semantic_bench()
		semantic = import_app("ai_fr_hg.ai.semantic")

		_patch_extraction(
			semantic,
			{
				"entities": [],
				"relationships": [
					{
						"subject": "Alice Novak",
						"object": "Cyberdyne Systems",
						"relationship_type": "works_for",
						"evidence_quote": "Alice Novak works for Cyberdyne Systems in Sofia.",
						"first_offset": 0,
						"confidence": 92.0,
					}
				],
				"rejected": {},
				"model": "m1",
			},
		)

		result = semantic.scan_document_semantic("DOC1")

		self.assertEqual(result["relationships"], 1)
		[row] = bench.get_all("AI Entity Relationship")
		self.assertEqual(row["relationship_type"], "works_for")
		self.assertTrue(row["evidence_quote"])
		self.assertEqual(row["confidence"], 92.0)
		self.assertIsNotNone(row["last_scanned_on"])

	def test_scanning_a_missing_document_raises(self):
		_semantic_bench()
		semantic = import_app("ai_fr_hg.ai.semantic")

		with self.assertRaises(Exception):
			semantic.scan_document_semantic("NOPE")

	def test_semantic_rows_are_scoped_to_the_documents_knowledge_base(self):
		"""A leaked knowledge_base is a cross-tenant disclosure, not a cosmetic bug."""
		bench = _semantic_bench()
		semantic = import_app("ai_fr_hg.ai.semantic")

		_patch_extraction(
			semantic,
			{
				"entities": [
					{
						"entity_type": "person",
						"value": "Alice Novak",
						"normalized_value": "alice novak",
						"confidence": 90.0,
						"first_offset": 0,
						"context_quote": "q",
						"occurrences": 1,
					}
				],
				"relationships": [
					{
						"subject": "Alice Novak",
						"object": "Cyberdyne Systems",
						"relationship_type": "works_for",
						"evidence_quote": "Alice Novak works for Cyberdyne Systems in Sofia.",
						"first_offset": 0,
						"confidence": 92.0,
					}
				],
				"rejected": {},
				"model": "m1",
			},
		)
		semantic.scan_document_semantic("DOC1")

		self.assertEqual(bench.get_all("AI Pattern Entity")[0]["knowledge_base"], "KB1")
		self.assertEqual(bench.get_all("AI Entity Relationship")[0]["knowledge_base"], "KB1")

	def test_document_deletion_cascades_to_relationships(self):
		bench = _semantic_bench()
		semantic = import_app("ai_fr_hg.ai.semantic")

		bench.db.insert_row(
			"AI Entity Relationship",
			{"name": "REL1", "document": "DOC1", "knowledge_base": "KB1", "subject": "a", "object": "b"},
		)
		self.assertEqual(bench.db.count("AI Entity Relationship"), 1)

		semantic.handle_document_trashed(bench.get_doc("AI Document", "DOC1"))

		self.assertEqual(bench.db.count("AI Entity Relationship"), 0)


# ---------------------------------------------------------------------------
# §23 — the relationship controller enforces its own invariants
# ---------------------------------------------------------------------------


class TestRelationshipValidation(TestCase):
	def _controller_bench(self):
		bench = install(FakeBench())
		bench.register_doctype("AI Document", [{"fieldname": "name"}, {"fieldname": "knowledge_base"}])
		bench.db.insert_row("AI Document", {"name": "DOC1", "knowledge_base": "KB1"})
		from ai_fr_hg.ai_knowledge.doctype.ai_entity_relationship.ai_entity_relationship import (
			AIEntityRelationship,
		)

		bench.register_doctype(
			"AI Entity Relationship",
			[
				{"fieldname": f}
				for f in (
					"name",
					"document",
					"knowledge_base",
					"subject",
					"object",
					"relationship_type",
					"evidence_quote",
					"confidence",
					"first_offset",
				)
			],
			controller=AIEntityRelationship,
		)
		return bench

	def _new(self, bench, **values):
		doc = bench.new_doc("AI Entity Relationship", **values)
		return doc

	def test_a_relationship_without_evidence_is_rejected(self):
		"""An inferred claim with no supporting span cannot be audited."""
		bench = self._controller_bench()
		doc = self._new(
			bench,
			document="DOC1",
			subject="Alice",
			object="Cyberdyne",
			relationship_type="works_for",
			evidence_quote="",
		)
		with self.assertRaises(ValidationError):
			doc.insert()

	def test_self_referential_relationships_are_rejected(self):
		bench = self._controller_bench()
		doc = self._new(
			bench,
			document="DOC1",
			subject="Acme",
			object="acme",
			relationship_type="part_of",
			evidence_quote="Acme is part of acme.",
		)
		with self.assertRaises(ValidationError):
			doc.insert()

	def test_confidence_is_clamped_into_range(self):
		bench = self._controller_bench()
		doc = self._new(
			bench,
			document="DOC1",
			subject="Alice",
			object="Cyberdyne",
			relationship_type="works_for",
			evidence_quote="Alice works for Cyberdyne.",
			confidence=5000,
		)
		doc.insert()
		self.assertEqual(bench.get_all("AI Entity Relationship")[0]["confidence"], 100.0)

	def test_unknown_predicate_degrades_to_related_to(self):
		bench = self._controller_bench()
		doc = self._new(
			bench,
			document="DOC1",
			subject="Alice",
			object="Cyberdyne",
			relationship_type="invented_predicate",
			evidence_quote="Alice and Cyberdyne.",
		)
		doc.insert()
		self.assertEqual(bench.get_all("AI Entity Relationship")[0]["relationship_type"], "related_to")

	def test_knowledge_base_is_inherited_from_the_document(self):
		bench = self._controller_bench()
		doc = self._new(
			bench,
			document="DOC1",
			subject="Alice",
			object="Cyberdyne",
			relationship_type="works_for",
			evidence_quote="Alice works for Cyberdyne.",
		)
		doc.insert()
		self.assertEqual(bench.get_all("AI Entity Relationship")[0]["knowledge_base"], "KB1")

	def test_a_relationship_on_a_missing_document_is_rejected(self):
		bench = self._controller_bench()
		doc = self._new(
			bench,
			document="GONE",
			subject="Alice",
			object="Cyberdyne",
			relationship_type="works_for",
			evidence_quote="Alice works for Cyberdyne.",
		)
		with self.assertRaises(ValidationError):
			doc.insert()


# ---------------------------------------------------------------------------
# §10 — provenance immutability is enforced, not merely styled read-only
# ---------------------------------------------------------------------------


class TestProvenanceImmutability(TestCase):
	def _bench(self):
		bench = install(FakeBench())
		from ai_fr_hg.ai_knowledge.doctype.ai_document import ai_document as module

		bench.register_doctype(
			"AI Document",
			[
				{"fieldname": f}
				for f in ("name", "checksum", "reader_used", "file_size", "mime_type", "extraction_evidence")
			],
		)
		return bench, module

	def test_editing_a_provenance_field_is_refused(self):
		bench, module = self._bench()
		doc = module.AIDocument.__new__(module.AIDocument)
		from ai_fr_hg.tests.fakebench import FakeDocument

		FakeDocument.__init__(doc, bench, "AI Document", {"name": "D1", "checksum": "aaa"})
		object.__setattr__(doc, "_before_save", {"name": "D1", "checksum": "aaa"})

		doc.checksum = "tampered"
		with self.assertRaises(ValidationError):
			doc.validate_extraction_provenance()

	def test_unchanged_provenance_passes(self):
		bench, module = self._bench()
		doc = module.AIDocument.__new__(module.AIDocument)
		from ai_fr_hg.tests.fakebench import FakeDocument

		FakeDocument.__init__(doc, bench, "AI Document", {"name": "D1", "checksum": "aaa"})
		object.__setattr__(doc, "_before_save", {"name": "D1", "checksum": "aaa"})

		doc.validate_extraction_provenance()  # must not raise

	def test_the_canonical_pipeline_may_rewrite_provenance(self):
		bench, module = self._bench()
		doc = module.AIDocument.__new__(module.AIDocument)
		from ai_fr_hg.tests.fakebench import FakeDocument

		FakeDocument.__init__(doc, bench, "AI Document", {"name": "D1", "checksum": "aaa"})
		object.__setattr__(doc, "_before_save", {"name": "D1", "checksum": "aaa"})
		doc.checksum = "recomputed"

		with module.allow_extraction_provenance():
			doc.validate_extraction_provenance()  # authorized path

	def test_a_new_document_is_not_blocked(self):
		bench, module = self._bench()
		doc = module.AIDocument.__new__(module.AIDocument)
		from ai_fr_hg.tests.fakebench import FakeDocument

		FakeDocument.__init__(doc, bench, "AI Document", {"name": "D1", "checksum": "aaa"})
		doc.validate_extraction_provenance()  # no prior version, nothing to protect


# ---------------------------------------------------------------------------
# §6.2 — archive containment is a security control, tested as one
# ---------------------------------------------------------------------------


class TestArchiveContainment(TestCase):
	"""These assert on the resolver's decisions, not on the presence of a guard.

	The earlier suite tested traversal only through a whole-archive read, which
	could not distinguish "blocked by the resolver" from "the member happened
	not to be extracted". A mutation campaign showed one guard was redundant;
	these pin the effective behaviour instead of the implementation shape.
	"""

	def _safe(self):
		install(FakeBench())
		from ai_fr_hg.ai.readers.archive import _safe_member_path

		return _safe_member_path

	def test_paths_that_escape_the_root_are_refused(self):
		safe = self._safe()
		for hostile in (
			"../../etc/passwd",
			"a/../../etc/passwd",
			"..",
			"../x",
			"a/b/../../../outside",
		):
			self.assertIsNone(safe(hostile), f"{hostile!r} must be refused")

	def test_absolute_and_drive_paths_are_refused(self):
		safe = self._safe()
		for hostile in ("/etc/shadow", "/", "C:/windows/system32", "C:\\windows", "\\\\server\\share"):
			self.assertIsNone(safe(hostile), f"{hostile!r} must be refused")

	def test_interior_traversal_that_stays_inside_is_normalized(self):
		"""`a/b/../c.txt` is legitimate; over-blocking would break real archives."""
		safe = self._safe()
		self.assertEqual(safe("a/b/../c.txt"), "a/c.txt")
		self.assertEqual(safe("dir/../ok.txt"), "ok.txt")

	def test_ordinary_paths_survive_unchanged(self):
		safe = self._safe()
		self.assertEqual(safe("readme.md"), "readme.md")
		self.assertEqual(safe("docs/guide/intro.txt"), "docs/guide/intro.txt")

	def test_empty_and_dot_paths_are_refused(self):
		safe = self._safe()
		for empty in ("", ".", "./", None):
			self.assertIsNone(safe(empty))

	def test_backslash_separators_are_normalized_before_checking(self):
		"""A Windows-style path must not bypass the POSIX checks."""
		safe = self._safe()
		self.assertIsNone(safe("..\\..\\etc\\passwd"))
		self.assertEqual(safe("dir\\file.txt"), "dir/file.txt")


class TestConversationHistoryBehaviour(TestCase):
	"""CHAT-01 re-audit: run the real `get_conversation_history`, not its helper.

	The existing coverage exercises `window_latest_messages` in isolation and
	asserts the *source text* of `ai/conversation.py`. Neither observes the
	function the application actually calls, so the status filter, the
	newest-first ordering it depends on, and the tool-role mapping were all
	unverified. This drives the real function against a bench.
	"""

	def _history(self, rows):
		bench = install(FakeBench())
		bench.register_doctype(
			"AI Message",
			[
				"conversation",
				"role",
				"content",
				"status",
				"sequence",
				"tool",
				"tool_call_id",
				"tool_arguments",
				"tool_result",
			],
		)
		for row in rows:
			bench.db.insert_row("AI Message", row)
		module = import_app("ai_fr_hg.ai.conversation")
		return bench, module

	def _message(self, seq, role, *, status="Completed", content=None, **extra):
		row = {
			"name": f"MSG-{seq:04d}",
			"conversation": "CONV-1",
			"role": role,
			"content": content if content is not None else f"msg-{seq}",
			"status": status,
			"sequence": seq,
		}
		row.update(extra)
		return row

	def test_returns_the_latest_turns_not_the_oldest(self):
		rows = [self._message(i, "User") for i in range(1, 101)]
		_, module = self._history(rows)

		history = module.get_conversation_history("CONV-1", limit=20)

		contents = [message.content for message in history]
		self.assertEqual(len(contents), 20)
		self.assertEqual(contents[-1], "msg-100")
		self.assertNotIn("msg-1", contents)
		self.assertNotIn("msg-80", contents)

	def test_history_is_returned_oldest_first_for_the_model(self):
		"""The query is newest-first; the model must receive chronological order."""
		rows = [self._message(i, "User") for i in range(1, 6)]
		_, module = self._history(rows)

		history = module.get_conversation_history("CONV-1", limit=5)

		self.assertEqual([m.content for m in history], [f"msg-{i}" for i in range(1, 6)])

	def test_in_flight_and_cancelled_turns_are_excluded(self):
		"""Only Completed/Failed/Draft may be replayed as context."""
		rows = [
			self._message(1, "User", content="kept"),
			self._message(2, "Assistant", status="Running", content="in-flight"),
			self._message(3, "Assistant", status="Cancelled", content="cancelled"),
			self._message(4, "Assistant", status="Failed", content="failed-but-kept"),
		]
		_, module = self._history(rows)

		contents = [m.content for m in module.get_conversation_history("CONV-1", limit=20)]

		self.assertIn("kept", contents)
		self.assertIn("failed-but-kept", contents)
		self.assertNotIn("in-flight", contents)
		self.assertNotIn("cancelled", contents)

	def test_other_conversations_never_leak_into_history(self):
		rows = [
			self._message(1, "User", content="mine"),
			{
				"name": "MSG-9999",
				"conversation": "CONV-2",
				"role": "User",
				"content": "someone-else",
				"status": "Completed",
				"sequence": 2,
			},
		]
		_, module = self._history(rows)

		contents = [m.content for m in module.get_conversation_history("CONV-1", limit=20)]

		self.assertEqual(contents, ["mine"])

	def test_system_messages_are_dropped_and_tool_rows_carry_their_result(self):
		rows = [
			self._message(1, "System", content="hidden-system-prompt"),
			self._message(2, "User", content="question"),
			self._message(
				3,
				"Tool",
				content="ignored",
				tool="search",
				tool_call_id="call-1",
				tool_result="tool-output",
			),
		]
		_, module = self._history(rows)

		history = module.get_conversation_history("CONV-1", limit=20)

		roles = [m.role for m in history]
		self.assertNotIn("system", roles)
		tool_message = next(m for m in history if m.role == "tool")
		# The tool's payload is its result, never the content column.
		self.assertEqual(tool_message.content, "tool-output")
		self.assertEqual(tool_message.name, "search")
		self.assertEqual(tool_message.tool_call_id, "call-1")

	def test_limit_is_clamped_to_a_bounded_window(self):
		rows = [self._message(i, "User") for i in range(1, 40)]
		_, module = self._history(rows)

		self.assertEqual(len(module.get_conversation_history("CONV-1", limit=0)), 20)
		self.assertLessEqual(len(module.get_conversation_history("CONV-1", limit=10_000)), 200)


class TestFolderSubtreeIsolation(TestCase):
	"""RET-07 re-audit: query with the filters, don't just inspect their shape.

	The existing test asserts that `folder_match_or_filters` returns a
	particular list of triples. That is a restatement of the implementation:
	it would pass unchanged if `get_all` ignored `or_filters` entirely, and it
	says nothing about which rows a query actually returns. These tests run
	the filters through a bench and assert on the resulting rows.

	`fakebench` now models MariaDB's LIKE escaping, so `\\_` and `\\%` are
	literal here as they are in the database.
	"""

	def _bench(self):
		bench = install(FakeBench())
		bench.register_doctype("AI Document", ["folder", "source_folder", "title"])
		return bench

	def _query(self, bench, folder):
		import frappe

		from ai_fr_hg.ai.folders import folder_match_or_filters

		return {
			row["title"]
			for row in frappe.get_all(
				"AI Document",
				fields=["title"],
				or_filters=folder_match_or_filters(folder, ("folder", "source_folder")),
			)
		}

	def test_sibling_prefix_folder_is_not_included(self):
		"""The RET-07 bug: `Home/A%` also matched `Home/AB`."""
		bench = self._bench()
		for name, folder in (
			("inside", "Home/A"),
			("nested", "Home/A/deep"),
			("sibling", "Home/AB"),
			("sibling-nested", "Home/AB/deep"),
			("unrelated", "Home/Other"),
		):
			bench.db.insert_row(
				"AI Document", {"name": name, "folder": folder, "source_folder": folder, "title": name}
			)

		self.assertEqual(self._query(bench, "Home/A"), {"inside", "nested"})

	def test_like_metacharacters_in_a_folder_name_are_literal(self):
		"""An underscore in a folder name must not act as a single-char wildcard."""
		bench = self._bench()
		for name, folder in (
			("literal", "Home/Q_1/child"),
			("wildcard-victim", "Home/QX1/child"),
		):
			bench.db.insert_row(
				"AI Document", {"name": name, "folder": folder, "source_folder": folder, "title": name}
			)

		self.assertEqual(self._query(bench, "Home/Q_1"), {"literal"})

	def test_documents_moved_out_are_still_found_by_source_folder(self):
		"""Both columns are matched, so provenance survives a move."""
		bench = self._bench()
		bench.db.insert_row(
			"AI Document",
			{"name": "moved", "folder": "Home/Elsewhere", "source_folder": "Home/A", "title": "moved"},
		)

		self.assertEqual(self._query(bench, "Home/A"), {"moved"})

	def test_or_filters_are_actually_applied_by_the_harness(self):
		"""Guard the harness: if or_filters were ignored, everything would match."""
		bench = self._bench()
		bench.db.insert_row(
			"AI Document",
			{"name": "far", "folder": "Home/Zzz", "source_folder": "Home/Zzz", "title": "far"},
		)

		self.assertEqual(self._query(bench, "Home/A"), set())


class TestGenericToolPermissionEnforcement(TestCase):
	"""SEC-02 / SEC-03 re-audit: no test referenced `safe_count` at all.

	Both rows are CLOSED security claims — "count runs through permission-aware
	listing" and "central permlevel-aware projection plus sensitive-field
	deny" — and the whole of `ai/tools/query.py` had no direct coverage. These
	tests exercise the real functions against a bench whose `get_list` applies
	a row-permission hook, which is the exact distinction SEC-02 turns on:
	`frappe.db.count` ignores permission query conditions, `get_list` does not.
	"""

	def _bench(self):
		bench = install(FakeBench())
		bench.register_doctype("AI Document", ["title", "owner_user", "api_key", "vault_pin", "notes"])
		bench.field_types["AI Document"] = {
			"title": "Data",
			"owner_user": "Data",
			"api_key": "Data",
			# `vault_pin` is innocuous by name, so only its Password *type*
			# can deny it. Without this the type rule is untested: `api_key`
			# is already caught by the substring policy.
			"vault_pin": "Password",
			"notes": "Small Text",
		}
		for index in range(1, 7):
			bench.db.insert_row(
				"AI Document",
				{
					"name": f"DOC-{index}",
					"title": f"doc {index}",
					# Half belong to someone else.
					"owner_user": "alice@example.com" if index % 2 else "bob@example.com",
					"api_key": "super-secret",
					"vault_pin": "1234",
					"notes": "n",
				},
			)
		# Row-level rule: a user sees only their own rows.
		bench.permission_hooks["AI Document"] = lambda user, row: row.get("owner_user") == user
		bench.session.user = "alice@example.com"
		return bench

	def test_count_respects_row_level_permissions(self):
		"""SEC-02: the aggregate must not reveal rows the caller cannot list."""
		bench = self._bench()
		query = import_app("ai_fr_hg.ai.tools.query")

		result = query.safe_count("AI Document")

		# Six rows exist; three belong to alice.
		self.assertEqual(len(bench.db.tables["AI Document"]), 6)
		self.assertEqual(result["count"], 3)
		self.assertTrue(result["exact"])
		self.assertFalse(result["bounded"])

	def test_count_changes_with_the_acting_user(self):
		"""Proves the number tracks authority rather than being a constant."""
		bench = self._bench()
		query = import_app("ai_fr_hg.ai.tools.query")

		bench.session.user = "bob@example.com"
		self.assertEqual(query.safe_count("AI Document")["count"], 3)

		bench.permission_hooks["AI Document"] = lambda user, row: True
		self.assertEqual(query.safe_count("AI Document")["count"], 6)

	def test_count_is_reported_as_bounded_rather_than_wrong(self):
		"""Beyond the scan cap the tool must not pretend to be exact."""
		bench = self._bench()
		query = import_app("ai_fr_hg.ai.tools.query")
		bench.permission_hooks["AI Document"] = lambda user, row: True

		original = query.COUNT_SCAN_CAP
		try:
			query.COUNT_SCAN_CAP = 2
			result = query.safe_count("AI Document")
		finally:
			query.COUNT_SCAN_CAP = original

		self.assertEqual(result["count"], 2)
		self.assertFalse(result["exact"])
		self.assertTrue(result["bounded"])

	def test_unreadable_doctype_is_refused_before_any_query(self):
		self._bench()
		query = import_app("ai_fr_hg.ai.tools.query")
		import frappe

		def deny(doctype, ptype="read", doc=None, user=None, throw=False):
			return False

		frappe.has_permission = deny
		# `_assert_readable` must refuse before any row is read.
		with self.assertRaises(PermissionError_):
			query.safe_count("AI Document")

	def test_password_and_secret_fields_are_denied(self):
		"""SEC-03: sensitive fields are denied by type and by name."""
		self._bench()
		query = import_app("ai_fr_hg.ai.tools.query")

		denied = query.denied_fieldnames("AI Document")

		# Denied by name (substring policy).
		self.assertIn("api_key", denied)
		# Denied by fieldtype alone — the name gives nothing away.
		self.assertIn("vault_pin", denied)
		self.assertNotIn("title", denied)
		self.assertNotIn("notes", denied)

	def test_permlevel_restricted_fields_are_not_projected(self):
		"""SEC-03: the projection follows Frappe's permitted-field rules."""
		bench = self._bench()
		query = import_app("ai_fr_hg.ai.tools.query")

		bench.permitted_fields["AI Document"] = {"title", "api_key"}
		permitted, denied = query.readable_fields("AI Document")

		self.assertEqual(permitted, {"title", "api_key"})
		self.assertIn("api_key", denied)
		# The usable projection is what survives both rules.
		self.assertEqual(permitted - denied, {"title"})

	def test_write_payload_strips_fields_outside_write_authority(self):
		"""SEC-03: a model-supplied payload cannot set a restricted field."""
		bench = self._bench()
		query = import_app("ai_fr_hg.ai.tools.query")

		bench.permitted_fields["AI Document"] = {"title", "notes", "api_key", "vault_pin"}
		cleaned = query.safe_field_values(
			"AI Document",
			{
				"title": "ok",
				"api_key": "attempt",
				"vault_pin": "attempt",
				"owner_user": "escalate",
				"unknown": "x",
			},
		)

		self.assertEqual(cleaned, {"title": "ok"})


class TestSearchTelemetryRedaction(TestCase):
	"""SEC-07 re-audit: `redact` — the mechanism the row rests on — had no test.

	The claim is "query and bounded result snippets pass canonical redaction;
	full content never stored; `log_search_queries` control". Each clause is
	checked here by running `_log_search_job` and inspecting the row that
	reaches the database.
	"""

	def _bench(self, *, patterns="", enabled=1):
		bench = install(FakeBench())
		bench.register_doctype(
			"AI Platform Settings", ["redact_patterns", "log_search_queries"], is_single=True
		)
		bench.db.singles["AI Platform Settings"] = {
			"redact_patterns": patterns,
			"log_search_queries": enabled,
		}
		bench.register_doctype(
			"AI Search Query",
			[
				"query",
				"knowledge_base",
				"user",
				"search_type",
				"result_count",
				"top_score",
				"duration_ms",
				"results",
			],
		)
		logging_module = import_app("ai_fr_hg.ai.logging")
		logging_module.clear_pattern_cache()
		return bench, import_app("ai_fr_hg.ai.retrieval")

	def _rows(self, bench):
		return list(bench.db.tables["AI Search Query"].values())

	def _log(self, retrieval, *, query="q", results=None):
		retrieval._log_search_job(
			query=query,
			targets=["KB-1"],
			search_type="hybrid",
			results=results or [],
			result_count=len(results or []),
			top_score=0.9,
			duration_ms=12,
			user="alice@example.com",
		)

	def test_configured_patterns_redact_the_stored_query(self):
		bench, retrieval = self._bench(patterns=r"\d{3}-\d{2}-\d{4}")

		self._log(retrieval, query="lookup 123-45-6789 now")

		stored = self._rows(bench)[0]["query"]
		self.assertNotIn("123-45-6789", stored)
		self.assertIn("[REDACTED]", stored)

	def test_result_snippets_are_redacted_and_bounded(self):
		bench, retrieval = self._bench(patterns=r"SECRET-\w+")
		results = [
			{
				"chunk": "CH-1",
				"document": "DOC-1",
				"document_title": "SECRET-title",
				"score": 0.9,
				"content": "SECRET-body " + ("x" * 5000),
			}
		]

		self._log(retrieval, results=results)

		telemetry = json.loads(self._rows(bench)[0]["results"])
		self.assertNotIn("SECRET-body", telemetry[0]["snippet"])
		self.assertNotIn("SECRET-title", telemetry[0]["title"])
		# Bounded: the full passage is never persisted.
		self.assertLessEqual(len(telemetry[0]["snippet"]), 200)

	def test_only_the_first_ten_results_are_recorded(self):
		bench, retrieval = self._bench()
		results = [
			{"chunk": f"CH-{i}", "document": "DOC", "document_title": "t", "score": 0.5, "content": "c"}
			for i in range(50)
		]

		self._log(retrieval, results=results)

		self.assertEqual(len(json.loads(self._rows(bench)[0]["results"])), 10)

	def test_disabling_the_control_stops_telemetry_entirely(self):
		"""The operator switch unhidden by VER-05 must actually govern writes."""
		bench, retrieval = self._bench(enabled=0)

		self._log(retrieval, query="sensitive")

		self.assertEqual(self._rows(bench), [])

	def test_logging_failure_never_breaks_the_search(self):
		bench, retrieval = self._bench()
		bench.db.fail_next_write = True

		self._log(retrieval, query="q")

		self.assertEqual(self._rows(bench), [])

	def test_redact_bounds_output_and_survives_invalid_patterns(self):
		self._bench(patterns="[unclosed\n\nvalid[0-9]+")
		logging_module = import_app("ai_fr_hg.ai.logging")
		logging_module.clear_pattern_cache()

		# An uncompilable line is skipped rather than disabling redaction.
		self.assertEqual(logging_module.redact("valid123"), "[REDACTED]")
		self.assertEqual(logging_module.redact(None), "")
		self.assertEqual(
			len(logging_module.redact("y" * (logging_module.MAX_STORED_CHARACTERS + 500))),
			logging_module.MAX_STORED_CHARACTERS,
		)


class TestPipelineStepConfigContract(TestCase):
	"""PIPE-04 re-audit: the row names `validate_step_config` as its evidence.

	That function was never referenced by a test. It is the server-side gate
	behind the typed step dialogs, so it is what stops a malformed pipeline
	being saved through the API rather than the builder.
	"""

	def _validate(self):
		install(FakeBench())
		module = import_app("ai_fr_hg.ai.pipeline")
		# Take the exception from the module under test. A second
		# `import_app("...exceptions")` would rebuild the module and return a
		# *different* PipelineError class, which `assertRaises` would not
		# match even though the code raised correctly.
		return module, module.PipelineError

	def test_required_keys_are_enforced_per_step_type(self):
		module, PipelineError = self._validate()

		with self.assertRaises(PipelineError):
			module.validate_step_config("Classify", None)
		with self.assertRaises(PipelineError):
			module.validate_step_config("Translate", None)
		# A step with no contract may legitimately have no configuration.
		self.assertEqual(module.validate_step_config("Summarize", None), {})

	def test_declared_types_are_enforced_not_merely_presence(self):
		module, PipelineError = self._validate()

		with self.assertRaises(PipelineError):
			# `categories` must be a list, not a comma-separated string.
			module.validate_step_config("Classify", json.dumps({"categories": "a,b"}))
		with self.assertRaises(PipelineError):
			module.validate_step_config("Translate", json.dumps({"target_language": ["fr"]}))

		self.assertEqual(
			module.validate_step_config("Classify", json.dumps({"categories": ["a", "b"]})),
			{"categories": ["a", "b"]},
		)

	def test_missing_required_key_is_rejected_even_when_config_is_present(self):
		module, PipelineError = self._validate()

		with self.assertRaises(PipelineError):
			module.validate_step_config("Classify", json.dumps({"unrelated": 1}))

	def test_malformed_configuration_is_rejected_with_a_clear_error(self):
		module, PipelineError = self._validate()

		with self.assertRaises(PipelineError):
			module.validate_step_config("Summarize", "{not json")
		with self.assertRaises(PipelineError):
			# A JSON array is valid JSON but not a configuration object.
			module.validate_step_config("Summarize", json.dumps(["a"]))

	def test_unknown_step_type_does_not_bypass_object_validation(self):
		module, PipelineError = self._validate()

		with self.assertRaises(PipelineError):
			module.validate_step_config("Nonexistent", json.dumps(["still-not-an-object"]))
		self.assertEqual(module.validate_step_config("Nonexistent", json.dumps({"x": 1})), {"x": 1})


class TestUrlIngestionGate(TestCase):
	"""SEC-04 re-audit: the 17 cited tests never touch this function.

	SEC-04 is closed on "17 runtime tests against a real loopback HTTP/TLS
	server". Those tests are real and good, but they exercise
	`utils/netguard.py` — the transport: pinned dial, redirect refusal, DNS
	rebinding, peer revalidation. The *application's* URL gate,
	`ingestion._validate_fetch_url`, is never called by any of them.

	Deleting the embedded-credentials check and the `enforce_local_only` call
	both left all 17 passing. These tests close that gap: they assert the
	policy decisions the gate makes before a socket is ever opened.
	"""

	def _gate(self, *, user="alice@example.com", roles=(), allowed_hosts=(), enabled=1):
		bench = install(FakeBench())
		bench.register_doctype("User", ["enabled"])
		bench.db.insert_row("User", {"name": user, "enabled": enabled})
		bench.db.insert_row("User", {"name": "Administrator", "enabled": 1})
		bench.roles[user] = list(roles)
		bench.session.user = user

		module = import_app("ai_fr_hg.ai.ingestion")
		# Isolate the gate's own policy from the transport layer and from DNS.
		# The netguard suite already covers resolution and dialling against a
		# live server; what is untested is the policy this function applies
		# before any socket is opened.
		module.get_allowed_hosts = lambda: set(allowed_hosts)
		module.enforce_local_only = lambda url, label: None
		module.socket = types.SimpleNamespace(
			getaddrinfo=lambda host, port: [(2, 1, 6, "", ("203.0.113.1", port))]
		)
		return bench, module

	def _reject(self, module, url, user=None):
		with self.assertRaises(module.DocumentFetchError):
			module._validate_fetch_url(url, user)

	def test_embedded_credentials_are_refused(self):
		"""A URL carrying credentials must never be fetched."""
		_, module = self._gate(roles=["System Manager"])

		self._reject(module, "https://user:pass@example.com/doc.pdf")

	def test_non_http_schemes_are_refused(self):
		_, module = self._gate(roles=["System Manager"])

		for url in (
			"file:///etc/passwd",
			"ftp://example.com/doc.pdf",
			"gopher://example.com/",
			"data:text/plain;base64,AAAA",
		):
			self._reject(module, url)

	def test_relative_and_empty_urls_are_refused(self):
		_, module = self._gate(roles=["System Manager"])

		for url in ("", None, "/local/path", "notaurl"):
			self._reject(module, url)

	def test_non_manager_is_confined_to_the_allowlist(self):
		"""The allowlist is the whole authorization boundary for normal users."""
		_, module = self._gate(roles=[], allowed_hosts={"trusted.example.com"})

		self._reject(module, "https://evil.example.com/doc.pdf")
		# The permitted host passes the policy gate.
		module._validate_fetch_url("https://trusted.example.com/doc.pdf")

	def test_manager_is_not_confined_to_the_allowlist(self):
		_, module = self._gate(roles=["AI Manager"], allowed_hosts=set())

		module._validate_fetch_url("https://anywhere.example.com/doc.pdf")

	def test_allowlist_match_is_case_insensitive_on_host_only(self):
		_, module = self._gate(roles=[], allowed_hosts={"trusted.example.com"})

		module._validate_fetch_url("https://TRUSTED.example.com/doc.pdf")
		# A lookalike host that merely contains the allowed name is refused.
		self._reject(module, "https://trusted.example.com.evil.net/doc.pdf")

	def test_guest_and_disabled_users_cannot_fetch(self):
		_, module = self._gate(roles=["System Manager"])
		with self.assertRaises(module.DocumentSourcePermissionError):
			module._validate_fetch_url("https://example.com/d.pdf", "Guest")

		_, module = self._gate(user="dormant@example.com", roles=["System Manager"], enabled=0)
		with self.assertRaises(module.DocumentSourcePermissionError):
			module._validate_fetch_url("https://example.com/d.pdf", "dormant@example.com")

	def test_local_only_enforcement_is_invoked(self):
		"""The gate must delegate to the SSRF check, not just resolve DNS."""
		_, module = self._gate(roles=["System Manager"])
		calls = []

		def record(url, label):
			calls.append(url)

		module.enforce_local_only = record
		module._validate_fetch_url("https://example.com/doc.pdf")

		self.assertEqual(calls, ["https://example.com/doc.pdf"])


class TestQuotaReservationLedger(TestCase):
	"""GOV-03 re-audit: the cited evidence was `assertIn("reserve_request_quota", source)`.

	GOV-01/02/03 are all closed on source-text assertions in
	`test_phase_6_units.py` — that the engine *mentions* `reserve_request_quota`,
	`acquire_leases` and `check_rate_limit`. None of them runs the Lua that
	makes a reservation atomic, which is the entire point of the finding:
	"quotas are check-then-use, not reserved".

	`fakeredis[lua]` executes the real scripts, so these tests drive the
	actual ledger. They skip rather than pass when it is unavailable.
	"""

	def setUp(self):
		self.bench = install(FakeBench())
		if not self.bench.cache().available:
			self.skipTest("fakeredis[lua] is not installed")
		self.limits = import_app("ai_fr_hg.ai.limits")

	def _reserve(self, **kwargs):
		params = {
			"user": "alice@example.com",
			"request_limit": 3,
			"token_limit": 1000,
			"committed_requests": 0,
			"committed_tokens": 0,
			"estimated_tokens": 100,
		}
		params.update(kwargs)
		return self.limits.reserve_quota(**params)

	def test_concurrent_reservations_cannot_all_pass_the_same_check(self):
		"""The GOV-03 defect: N callers observing one pre-call total."""
		granted = [self._reserve() for _ in range(3)]
		self.assertTrue(all(r.enforced for r in granted))

		# The fourth exceeds max_requests_per_hour=3 while the others are
		# still in flight — nothing has been committed to the database yet.
		with self.assertRaises(self.limits.QuotaExceededError):
			self._reserve()

	def test_releasing_a_reservation_returns_the_allowance(self):
		held = [self._reserve() for _ in range(3)]
		with self.assertRaises(self.limits.QuotaExceededError):
			self._reserve()

		held[0].release()
		# The freed slot is immediately reusable.
		self._reserve()

	def test_committed_usage_and_in_flight_are_summed(self):
		"""Database usage plus ledger reservations, compared as one total."""
		# Two already recorded in the database, one in flight -> at the limit.
		self._reserve(committed_requests=2)

		with self.assertRaises(self.limits.QuotaExceededError):
			self._reserve(committed_requests=2)

	def test_worst_case_tokens_are_reserved_not_the_average(self):
		"""Reserving max_tokens is what makes a daily token cap a real cap."""
		self._reserve(request_limit=0, token_limit=500, estimated_tokens=400)

		# 400 held; a second 400 would exceed 500 even though nothing has
		# actually been consumed yet.
		with self.assertRaises(self.limits.QuotaExceededError):
			self._reserve(request_limit=0, token_limit=500, estimated_tokens=400)

	def test_token_ceiling_accounts_for_committed_tokens(self):
		with self.assertRaises(self.limits.QuotaExceededError):
			self._reserve(request_limit=0, token_limit=500, committed_tokens=450, estimated_tokens=100)

	def test_reservations_are_scoped_per_user(self):
		for _ in range(3):
			self._reserve(user="alice@example.com")
		with self.assertRaises(self.limits.QuotaExceededError):
			self._reserve(user="alice@example.com")

		# Bob's allowance is untouched by Alice saturating hers.
		self._reserve(user="bob@example.com")

	def test_no_limits_configured_means_no_enforcement(self):
		reservation = self._reserve(request_limit=0, token_limit=0)

		self.assertFalse(reservation.enforced)

	def test_release_is_idempotent(self):
		reservation = self._reserve()
		reservation.release()
		reservation.release()

		self.assertTrue(reservation.released)


class TestTaskLifecycleGovernance(TestCase):
	"""TASK-02 re-audit: state machine and actor authority, run rather than read.

	Part 2 sections 19 and 20 require a governed job lifecycle. The risk is not that the
	transition table is wrong on paper — it is that a caller reaches a
	transition without the authority for it, or that a terminal state turns
	out not to be terminal.
	"""

	def _bench(self, *, status="Open", requester="alice@example.com", requires_approval=0):
		bench = install(FakeBench())
		bench.register_doctype(
			"AI Task",
			["status", "requested_by", "requires_approval", "priority", "due_date", "error_message"],
		)
		bench.db.insert_row(
			"AI Task",
			{
				"name": "TASK-1",
				"owner": requester,
				"status": status,
				"requested_by": requester,
				"requires_approval": requires_approval,
				"priority": "Medium",
			},
		)
		bench.session.user = requester
		return bench, import_app("ai_fr_hg.ai.tasks")

	def _status(self, bench):
		return bench.db.tables["AI Task"]["TASK-1"]["status"]

	def test_terminal_states_are_actually_terminal(self):
		for terminal in ("Completed", "Cancelled", "Rejected"):
			bench, tasks = self._bench(status=terminal)
			bench.roles["alice@example.com"] = ["AI Manager"]
			with self.assertRaises(tasks.TaskIllegalTransition):
				tasks.cancel_task("TASK-1")
			self.assertEqual(self._status(bench), terminal)

	def test_a_stranger_cannot_cancel_someone_elses_task(self):
		bench, tasks = self._bench()
		bench.session.user = "mallory@example.com"
		bench.roles["mallory@example.com"] = []

		import frappe

		with self.assertRaises(frappe.PermissionError):
			tasks.cancel_task("TASK-1")
		self.assertEqual(self._status(bench), "Open")

	def test_guest_cannot_act_at_all(self):
		bench, tasks = self._bench()
		bench.session.user = "Guest"

		import frappe

		with self.assertRaises(frappe.PermissionError):
			tasks.cancel_task("TASK-1")

	def test_requester_may_cancel_their_own_task(self):
		bench, tasks = self._bench()

		tasks.cancel_task("TASK-1")

		self.assertEqual(self._status(bench), "Cancelled")

	def test_manager_may_cancel_any_task(self):
		bench, tasks = self._bench(requester="alice@example.com")
		bench.session.user = "boss@example.com"
		bench.roles["boss@example.com"] = ["AI Manager"]

		tasks.cancel_task("TASK-1")

		self.assertEqual(self._status(bench), "Cancelled")

	def test_approval_gate_blocks_a_non_manager_running_it_early(self):
		"""requires_approval must not be bypassable by the requester."""
		bench, tasks = self._bench(requires_approval=1)

		import frappe

		with self.assertRaises(frappe.PermissionError):
			tasks.run_now("TASK-1")
		self.assertEqual(self._status(bench), "Open")

	def test_claiming_reads_status_under_a_lock_before_acting(self):
		"""The claim must re-read status for update, not trust the list query."""
		bench, tasks = self._bench()
		bench.db.tables["AI Task"]["TASK-1"]["due_date"] = "2020-01-01 00:00:00"
		bench.db.for_update_reads.clear()

		tasks.claim_due_tasks(limit=10)

		self.assertIn(
			("AI Task", True),
			bench.db.for_update_reads,
			"claim_due_tasks did not re-read status FOR UPDATE",
		)

	def test_a_task_already_taken_is_not_claimed_twice(self):
		"""Simulates the row having moved on between the list and the lock."""
		bench, tasks = self._bench()
		bench.db.tables["AI Task"]["TASK-1"]["due_date"] = "2020-01-01 00:00:00"

		original = bench.db.get_value

		def moved_on(doctype, filters=None, fieldname="name", **kwargs):
			if kwargs.get("for_update") and fieldname == "status":
				return "In Progress"
			return original(doctype, filters, fieldname, **kwargs)

		bench.db.get_value = moved_on
		self.assertEqual(tasks.claim_due_tasks(limit=10), [])


class TestKnowledgeBaseVisibility(TestCase):
	"""`get_accessible_knowledge_bases` decides which corpora a user can search.

	It is the filter behind every retrieval call, and no test named it. A
	fault here does not raise — it silently widens the search to corpora the
	user may not read, which is the worst failure mode for a permission
	function: invisible, and it looks like the feature working.
	"""

	def _bench(self, *, user="alice@example.com", roles=()):
		bench = install(FakeBench())
		bench.register_doctype("AI Knowledge Base", ["enabled", "is_public"])
		bench.register_doctype("AI Knowledge Base Role", ["parent", "parenttype", "role", "can_write"])
		bench.session.user = user
		bench.roles[user] = list(roles)

		for name, enabled, public in (
			("KB-PUBLIC", 1, 1),
			("KB-PRIVATE", 1, 0),
			("KB-RESTRICTED", 1, 0),
			("KB-DISABLED", 0, 1),
		):
			bench.db.insert_row("AI Knowledge Base", {"name": name, "enabled": enabled, "is_public": public})
		bench.db.insert_row(
			"AI Knowledge Base Role",
			{
				"name": "KBR-1",
				"parent": "KB-RESTRICTED",
				"parenttype": "AI Knowledge Base",
				"role": "Legal",
				"can_write": 0,
			},
		)
		return bench, import_app("ai_fr_hg.ai.knowledge")

	def test_a_plain_user_sees_only_public_corpora(self):
		_, knowledge = self._bench(roles=[])

		self.assertEqual(knowledge.get_accessible_knowledge_bases(), ["KB-PUBLIC"])

	def test_a_role_grant_opens_exactly_one_private_corpus(self):
		_, knowledge = self._bench(roles=["Legal"])

		self.assertEqual(sorted(knowledge.get_accessible_knowledge_bases()), ["KB-PUBLIC", "KB-RESTRICTED"])
		# KB-PRIVATE has no grant at all and must stay invisible.
		self.assertNotIn("KB-PRIVATE", knowledge.get_accessible_knowledge_bases())

	def test_disabled_corpora_are_never_returned(self):
		"""Even a manager must not search a disabled knowledge base."""
		_, knowledge = self._bench(roles=["AI Manager"])

		self.assertNotIn("KB-DISABLED", knowledge.get_accessible_knowledge_bases())

	def test_a_manager_sees_every_enabled_corpus(self):
		_, knowledge = self._bench(roles=["AI Manager"])

		self.assertEqual(
			sorted(knowledge.get_accessible_knowledge_bases()),
			["KB-PRIVATE", "KB-PUBLIC", "KB-RESTRICTED"],
		)

	def test_a_grant_for_a_role_the_user_lacks_grants_nothing(self):
		_, knowledge = self._bench(roles=["Finance"])

		self.assertEqual(knowledge.get_accessible_knowledge_bases(), ["KB-PUBLIC"])

	def test_the_query_is_evaluated_for_the_named_user_not_the_session(self):
		"""Background jobs pass a user explicitly; it must be honoured."""
		bench, knowledge = self._bench(user="alice@example.com", roles=[])
		bench.roles["boss@example.com"] = ["AI Manager"]

		self.assertEqual(
			sorted(knowledge.get_accessible_knowledge_bases("boss@example.com")),
			["KB-PRIVATE", "KB-PUBLIC", "KB-RESTRICTED"],
		)


class TestAgentAndToolAccess(TestCase):
	"""`check_agent_access` and `_check_tool_access` gate model-facing capability.

	Both are role gates with a deliberate fail-open default: an agent or tool
	that lists no roles is unrestricted. That is a defensible product choice,
	but it means the *restricted* case is the only thing standing between a
	user and a capability, and neither function was tested.
	"""

	def _agent(self, allowed_roles, *, user="alice@example.com", roles=()):
		bench = install(FakeBench())
		bench.session.user = user
		bench.roles[user] = list(roles)
		agent_module = import_app("ai_fr_hg.ai.agent")
		doc = types.SimpleNamespace(
			name="AGENT-1",
			get=lambda field: (
				[types.SimpleNamespace(role=role) for role in allowed_roles]
				if field == "allowed_roles"
				else None
			),
		)
		return bench, agent_module, doc

	def test_a_user_without_the_listed_role_is_refused(self):
		_, agent, doc = self._agent(["Legal"], roles=["Finance"])
		import frappe

		with self.assertRaises(frappe.PermissionError):
			agent.check_agent_access(doc)

	def test_a_user_holding_one_listed_role_is_allowed(self):
		_, agent, doc = self._agent(["Legal", "Finance"], roles=["Finance"])

		agent.check_agent_access(doc)

	def test_an_agent_listing_no_roles_is_open_by_design(self):
		"""Documented fail-open: no roles means no restriction, not no access."""
		_, agent, doc = self._agent([], roles=[])

		agent.check_agent_access(doc)

	def test_administrator_bypasses_the_role_gate(self):
		_, agent, doc = self._agent(["Legal"], user="Administrator", roles=[])

		agent.check_agent_access(doc)

	def _tool(self, allowed_roles, *, user="alice@example.com", roles=()):
		bench = install(FakeBench())
		bench.session.user = user
		bench.roles[user] = list(roles)
		tools = import_app("ai_fr_hg.ai.tools")
		doc = types.SimpleNamespace(
			name="TOOL-1",
			get=lambda field: (
				[types.SimpleNamespace(role=role) for role in allowed_roles]
				if field == "allowed_roles"
				else None
			),
		)
		return bench, tools, doc

	def test_tool_role_gate_refuses_an_unlisted_user(self):
		_, tools, doc = self._tool(["Legal"], roles=["Finance"])
		import frappe

		with self.assertRaises(frappe.PermissionError):
			tools._check_tool_access(doc)

	def test_tool_role_gate_admits_a_listed_user(self):
		_, tools, doc = self._tool(["Legal"], roles=["Legal"])

		tools._check_tool_access(doc)


class TestTranslationMemoryScope(TestCase):
	"""SEC-01: `authorized_memory_scope` decides whose translations are reused.

	SEC-01 is "translation memory can be unscoped". The rule is that an empty
	or unauthorized scope yields *no* memory rather than every corpus — a
	failure here leaks one tenant's translated content into another's output.
	"""

	def _scope(self, *, user="alice@example.com", roles=(), public=0, grant_role=None):
		bench = install(FakeBench())
		bench.register_doctype("AI Knowledge Base", ["enabled", "is_public"])
		bench.register_doctype("AI Knowledge Base Role", ["parent", "parenttype", "role", "can_write"])
		bench.session.user = user
		bench.roles[user] = list(roles)
		bench.db.insert_row("AI Knowledge Base", {"name": "KB-1", "enabled": 1, "is_public": public})
		if grant_role:
			bench.db.insert_row(
				"AI Knowledge Base Role",
				{
					"name": "KBR-1",
					"parent": "KB-1",
					"parenttype": "AI Knowledge Base",
					"role": grant_role,
					"can_write": 0,
				},
			)
		return bench, import_app("ai_fr_hg.ai.translation")

	def test_no_knowledge_base_means_no_memory_not_global_memory(self):
		_, translation = self._scope()

		self.assertIsNone(translation.authorized_memory_scope(None))
		self.assertIsNone(translation.authorized_memory_scope(""))
		self.assertIsNone(translation.authorized_memory_scope("   "))

	def test_an_unauthorized_scope_is_dropped_rather_than_honoured(self):
		_, translation = self._scope(roles=["Finance"], public=0, grant_role="Legal")

		self.assertIsNone(translation.authorized_memory_scope("KB-1"))

	def test_an_authorized_scope_is_returned(self):
		_, translation = self._scope(roles=["Legal"], public=0, grant_role="Legal")

		self.assertEqual(translation.authorized_memory_scope("KB-1"), "KB-1")

	def test_a_manager_scope_is_returned_verbatim_and_stays_a_filter(self):
		"""A manager is not re-checked against the KB row, by design.

		`_knowledge_base_access` short-circuits for managers before looking
		the record up, so a name that does not exist is returned unchanged.
		That is safe *because* the value is only ever used as an equality
		filter downstream (`_memory_lookup` filters `knowledge_base = scope`),
		so a bogus name matches nothing rather than widening the query. This
		test pins that reasoning: if the scope ever became something other
		than a filter value, the missing existence check would matter.
		"""
		_, translation = self._scope(roles=["AI Manager"])

		self.assertEqual(translation.authorized_memory_scope("KB-DOES-NOT-EXIST"), "KB-DOES-NOT-EXIST")

		source = (Path(__file__).resolve().parents[1] / "ai/translation.py").read_text()
		self.assertIn('"knowledge_base": scope', source)


class TestAutomationEventClaim(TestCase):
	"""AUTO-03/AUTO-04: an event must run once, and counters must not race.

	`_claim_event` is the once-only guarantee for automation. It was not
	named by any test. A failure here runs a rule twice for one document
	change — writing a field twice, charging a model twice, and emitting two
	audit entries for one cause.
	"""

	def _bench(self, *, status="Queued"):
		bench = install(FakeBench())
		bench.register_doctype(
			"AI Automation Event",
			["status", "started_on", "finished_on", "error_message", "snapshot"],
		)
		bench.db.insert_row("AI Automation Event", {"name": "EVT-1", "status": status})
		return bench, import_app("ai_fr_hg.ai.automation")

	def _status(self, bench):
		return bench.db.tables["AI Automation Event"]["EVT-1"]["status"]

	def test_a_queued_event_is_claimed_once(self):
		bench, automation = self._bench()

		self.assertTrue(automation._claim_event("EVT-1"))
		self.assertEqual(self._status(bench), "Running")

	def test_a_second_worker_cannot_claim_the_same_event(self):
		"""The AUTO-04 guarantee: one event, one execution."""
		_, automation = self._bench()
		automation._claim_event("EVT-1")

		self.assertFalse(automation._claim_event("EVT-1"))

	def test_an_event_in_any_non_queued_state_is_refused(self):
		for status in ("Running", "Completed", "Failed", "Skipped"):
			bench, automation = self._bench(status=status)
			self.assertFalse(automation._claim_event("EVT-1"), status)
			self.assertEqual(self._status(bench), status)

	def test_the_claim_reads_status_under_a_lock(self):
		bench, automation = self._bench()
		bench.db.for_update_reads.clear()

		automation._claim_event("EVT-1")

		self.assertIn(("AI Automation Event", True), bench.db.for_update_reads)

	def test_counters_are_incremented_by_the_database_not_read_modify_write(self):
		"""AUTO-03: the increment must be relative, so two workers cannot lose one.

		`fakebench` records SQL without executing it, so the resulting count
		cannot be asserted here — that is runtime-tier work. What *can* be
		asserted is the shape: a self-referential `coalesce(col, 0) + 1`
		rather than a Python-side read, add, write, which is the pattern that
		loses increments under concurrency.
		"""
		bench, automation = self._bench()

		automation._record_success("RULE-1")
		automation._record_failure("RULE-1", "boom")

		statements = " ".join(bench.db.sql_log).lower()
		self.assertIn("run_count = coalesce(run_count, 0) + 1", statements)
		self.assertIn("failure_count = coalesce(failure_count, 0) + 1", statements)

	def test_failure_detail_is_bounded_before_storage(self):
		bench, automation = self._bench()

		automation._record_failure("RULE-1", "x" * 5000)

		stored = [value for stmt in bench.db.sql_values for value in stmt if isinstance(value, str)]
		longest = max((len(value) for value in stored), default=0)
		self.assertLessEqual(longest, 1000)


class TestCooperativeCancellation(TestCase):
	"""Cancellation checkpoints: the mechanism a running job stops by.

	`_assert_not_cancelled` (ingestion and translation) and `_is_cancelled`
	(pipeline) are how long-running work notices it should stop. None was
	named by any test. They are also the *reason* the design can treat RQ
	signalling as best-effort: if Redis never delivers the stop command, the
	job is supposed to halt at its next checkpoint anyway. That fallback is
	load-bearing, so it needs evidence.
	"""

	def _ingestion(self, *, cancel_requested=0):
		bench = install(FakeBench())
		bench.register_doctype("AI Document", ["status", "cancel_requested"])
		bench.db.insert_row(
			"AI Document",
			{"name": "DOC-1", "status": "Processing", "cancel_requested": cancel_requested},
		)
		return bench, import_app("ai_fr_hg.ai.ingestion")

	def _translation(self, *, cancel_requested=0):
		bench = install(FakeBench())
		bench.register_doctype("AI Translation", ["status", "cancel_requested"])
		bench.db.insert_row(
			"AI Translation",
			{"name": "TRN-1", "status": "Processing", "cancel_requested": cancel_requested},
		)
		return bench, import_app("ai_fr_hg.ai.translation")

	def _pipeline(self, *, status="Running"):
		bench = install(FakeBench())
		bench.register_doctype("AI Pipeline Run", ["status", "finished_at"])
		bench.db.insert_row("AI Pipeline Run", {"name": "RUN-1", "status": status})
		return bench, import_app("ai_fr_hg.ai.pipeline")

	def test_ingestion_checkpoint_halts_when_cancellation_was_requested(self):
		_, ingestion = self._ingestion(cancel_requested=1)

		with self.assertRaises(ingestion.DocumentProcessingCancelled):
			ingestion._assert_not_cancelled("DOC-1")

	def test_ingestion_checkpoint_is_transparent_when_not_cancelled(self):
		"""A checkpoint that raised spuriously would abort healthy work."""
		_, ingestion = self._ingestion(cancel_requested=0)

		ingestion._assert_not_cancelled("DOC-1")

	def test_ingestion_checkpoint_reads_live_state_not_a_snapshot(self):
		"""Cancellation arrives *during* the job, so each check must re-read."""
		bench, ingestion = self._ingestion(cancel_requested=0)
		ingestion._assert_not_cancelled("DOC-1")

		# Another session sets the flag while the job is mid-flight.
		bench.db.tables["AI Document"]["DOC-1"]["cancel_requested"] = 1

		with self.assertRaises(ingestion.DocumentProcessingCancelled):
			ingestion._assert_not_cancelled("DOC-1")

	def test_translation_checkpoint_halts_when_cancellation_was_requested(self):
		_, translation = self._translation(cancel_requested=1)
		import frappe

		with self.assertRaises(frappe.ValidationError):
			translation._assert_not_cancelled("TRN-1")

	def test_translation_checkpoint_is_transparent_when_not_cancelled(self):
		_, translation = self._translation(cancel_requested=0)

		translation._assert_not_cancelled("TRN-1")

	def test_pipeline_checkpoint_reports_only_the_cancelled_state(self):
		for status, expected in (
			("Cancelled", True),
			("Running", False),
			("Queued", False),
			("Completed", False),
			("Failed", False),
		):
			_, pipeline = self._pipeline(status=status)
			self.assertIs(pipeline._is_cancelled("RUN-1"), expected, status)

	def test_pipeline_backoff_aborts_immediately_on_cancellation(self):
		"""`_wait_for_retry` must not hold a worker for the full delay.

		The loop polls in 100ms ticks precisely so a cancellation lands
		quickly. A regression to `time.sleep(seconds)` would keep a cancelled
		run occupying a worker slot for the whole backoff.
		"""
		_, pipeline = self._pipeline(status="Cancelled")
		slept = []
		pipeline.time = types.SimpleNamespace(sleep=slept.append, monotonic=lambda: 0.0)

		self.assertTrue(pipeline._wait_for_retry("RUN-1", 30))
		self.assertEqual(slept, [], "a cancelled run must not sleep at all")

	def test_pipeline_backoff_polls_in_short_ticks_while_running(self):
		_, pipeline = self._pipeline(status="Running")
		slept = []
		pipeline.time = types.SimpleNamespace(sleep=slept.append, monotonic=lambda: 0.0)

		self.assertFalse(pipeline._wait_for_retry("RUN-1", 2))
		# 2 seconds at 100ms granularity, so cancellation is noticed quickly.
		self.assertEqual(len(slept), 20)
		self.assertTrue(all(tick <= 0.1 for tick in slept))


class TestPipelineRunCancelAndRetry(TestCase):
	"""`cancel_run` and `retry` on AI Pipeline Run: authority and state gates.

	Neither was named by any test. Both are terminal-state operations on a
	background job, so the risk is a caller cancelling someone else's run, a
	completed run being reopened, or a retry silently starting from corrupt
	stored input.

	**Boundary.** These tests cover the committed-state half of cancellation:
	the status transition, the authority check and the audit record. The RQ
	half — `send_stop_job_command` reaching a live worker — needs real Redis
	and is not claimed here. That split is the design's own: the code treats
	Redis signalling as best-effort *because* the committed state stops a
	queued job and the cooperative checkpoints stop a running one.
	"""

	def _bench(self, *, status="Running", owner="alice@example.com", roles=(), user=None):
		bench = install(FakeBench())
		module = import_app("ai_fr_hg.ai_automation.doctype.ai_pipeline_run.ai_pipeline_run")
		bench.register_doctype(
			"AI Pipeline Run",
			["status", "finished_at", "triggered_by", "pipeline", "input_data"],
			controller=module.AIPipelineRun,
		)
		bench.register_doctype("AI Audit Log", ["action", "category", "severity", "message"])
		bench.db.insert_row(
			"AI Pipeline Run",
			{
				"name": "RUN-1",
				"status": status,
				"triggered_by": owner,
				"pipeline": "PIPE-1",
				"input_data": '{"x": 1}',
			},
		)
		acting = user or owner
		bench.session.user = acting
		bench.roles[acting] = list(roles)
		return bench, module

	def _status(self, bench):
		return bench.db.tables["AI Pipeline Run"]["RUN-1"]["status"]

	def _run_doc(self, bench):
		import frappe

		return frappe.get_doc("AI Pipeline Run", "RUN-1")

	def test_a_running_run_is_cancelled_and_stamped(self):
		bench, _ = self._bench(status="Running")

		result = self._run_doc(bench).cancel_run()

		self.assertEqual(result["status"], "Cancelled")
		self.assertEqual(self._status(bench), "Cancelled")
		self.assertTrue(bench.db.tables["AI Pipeline Run"]["RUN-1"]["finished_at"])

	def test_a_terminal_run_cannot_be_cancelled(self):
		"""Cancelling a finished run would rewrite a completed outcome."""
		import frappe

		for terminal in ("Completed", "Failed", "Cancelled"):
			bench, _ = self._bench(status=terminal)
			with self.assertRaises(frappe.ValidationError):
				self._run_doc(bench).cancel_run()
			self.assertEqual(self._status(bench), terminal)

	def test_a_stranger_cannot_cancel_another_users_run(self):
		import frappe

		bench, _ = self._bench(owner="alice@example.com", user="mallory@example.com", roles=[])

		with self.assertRaises(frappe.PermissionError):
			self._run_doc(bench).cancel_run()
		self.assertEqual(self._status(bench), "Running")

	def test_a_manager_may_cancel_any_run(self):
		bench, _ = self._bench(owner="alice@example.com", user="boss@example.com", roles=["AI Manager"])

		self._run_doc(bench).cancel_run()

		self.assertEqual(self._status(bench), "Cancelled")

	def test_cancellation_writes_an_audit_record_before_signalling(self):
		"""§24: the decision must be recorded even if RQ signalling fails."""
		bench, _ = self._bench(status="Running")

		self._run_doc(bench).cancel_run()

		actions = [row.get("action") for row in bench.db.tables.get("AI Audit Log", {}).values()]
		self.assertIn("Pipeline Run Cancelled", actions)

	def test_only_terminal_runs_may_be_retried(self):
		import frappe

		for status in ("Queued", "Running", "Waiting Approval", "Completed"):
			bench, _ = self._bench(status=status)
			with self.assertRaises(frappe.ValidationError):
				self._run_doc(bench).retry()

	def test_retry_refuses_corrupt_stored_input_instead_of_guessing(self):
		"""A retry must not silently start from an empty payload."""
		import frappe

		for payload in ("{not json", "[1, 2]", '"a string"'):
			bench, _ = self._bench(status="Failed")
			bench.db.tables["AI Pipeline Run"]["RUN-1"]["input_data"] = payload
			with self.assertRaises(frappe.ValidationError):
				self._run_doc(bench).retry()

	def test_retry_authority_matches_cancellation_authority(self):
		import frappe

		bench, _ = self._bench(status="Failed", owner="alice@example.com", user="mallory@example.com")

		with self.assertRaises(frappe.PermissionError):
			self._run_doc(bench).retry()


class TestStaleWorkerRecovery(TestCase):
	"""`reap_stale_in_flight_documents`: recovery when a worker dies mid-job.

	Nothing referenced it. This is the path that stops a document being
	stranded in `Extracting` forever because the worker holding it was
	killed — the enterprise requirement in Part 2 §20 for heartbeat, lease
	and recovery.

	The failure modes are opposite and both bad: reap too eagerly and a live
	worker's document is failed underneath it, producing duplicate work; reap
	never and the document is stuck until someone notices by hand.
	"""

	def _bench(self, rows):
		bench = install(FakeBench())
		bench.register_doctype(
			"AI Document",
			[
				"status",
				"processing_heartbeat",
				"processing_requested_by",
				"error_type",
				"error_message",
				"processing_message",
				"retry_count",
			],
		)
		for row in rows:
			bench.db.insert_row("AI Document", row)
		return bench, import_app("ai_fr_hg.ai.ingestion")

	@staticmethod
	def _doc(name, status, heartbeat, **extra):
		row = {
			"name": name,
			"status": status,
			"processing_heartbeat": heartbeat,
			"owner": "alice@example.com",
			"processing_requested_by": "alice@example.com",
		}
		row.update(extra)
		return row

	def test_a_dead_worker_releases_its_document_to_the_retry_path(self):
		bench, ingestion = self._bench([self._doc("DOC-DEAD", "Extracting", "2026-08-21 10:00:00")])

		reaped = ingestion.reap_stale_in_flight_documents()

		self.assertEqual([row.name for row in reaped], ["DOC-DEAD"])
		stored = bench.db.tables["AI Document"]["DOC-DEAD"]
		self.assertEqual(stored["status"], "Failed")
		self.assertEqual(stored["error_type"], "StaleWorker")

	def test_a_live_worker_is_never_reaped(self):
		"""Reaping a live job would run the same document twice."""
		bench, ingestion = self._bench([self._doc("DOC-LIVE", "Extracting", "2026-08-21 11:59:00")])

		self.assertEqual(ingestion.reap_stale_in_flight_documents(), [])
		self.assertEqual(bench.db.tables["AI Document"]["DOC-LIVE"]["status"], "Extracting")

	def test_every_in_flight_status_is_covered(self):
		"""A worker can die during any stage, not only extraction."""
		_, ingestion = self._bench(
			[
				self._doc("DOC-E", "Extracting", "2026-08-21 10:00:00"),
				self._doc("DOC-C", "Chunking", "2026-08-21 10:00:00"),
				self._doc("DOC-M", "Embedding", "2026-08-21 10:00:00"),
			]
		)

		reaped = {row.name for row in ingestion.reap_stale_in_flight_documents()}

		self.assertEqual(reaped, {"DOC-E", "DOC-C", "DOC-M"})

	def test_settled_documents_are_left_alone(self):
		"""An old heartbeat on a finished row is not a stale worker."""
		bench, ingestion = self._bench(
			[
				self._doc("DOC-OK", "Indexed", "2026-08-21 09:00:00"),
				self._doc("DOC-FAIL", "Failed", "2026-08-21 09:00:00"),
				self._doc("DOC-Q", "Queued", "2026-08-21 09:00:00"),
			]
		)

		self.assertEqual(ingestion.reap_stale_in_flight_documents(), [])
		for name in ("DOC-OK", "DOC-FAIL", "DOC-Q"):
			self.assertNotEqual(bench.db.tables["AI Document"][name].get("error_type"), "StaleWorker")

	def test_the_reaper_is_bounded_per_run(self):
		"""A backlog must not become one unbounded transaction."""
		_, ingestion = self._bench(
			[self._doc(f"DOC-{i}", "Extracting", "2026-08-21 10:00:00") for i in range(30)]
		)

		self.assertEqual(len(ingestion.reap_stale_in_flight_documents(limit=5)), 5)

	def test_the_heartbeat_is_refreshed_so_a_row_is_not_reaped_repeatedly(self):
		bench, ingestion = self._bench([self._doc("DOC-DEAD", "Extracting", "2026-08-21 10:00:00")])

		ingestion.reap_stale_in_flight_documents()

		# Refreshed to "now", so the next sweep does not re-reap the same row.
		self.assertEqual(
			str(bench.db.tables["AI Document"]["DOC-DEAD"]["processing_heartbeat"]),
			"2026-08-21 12:00:00",
		)

	def test_recovery_preserves_the_original_requester_for_re_enqueue(self):
		"""§22: the retry must run under the durable requester, not the worker."""
		_, ingestion = self._bench(
			[
				self._doc(
					"DOC-DEAD",
					"Extracting",
					"2026-08-21 10:00:00",
					processing_requested_by="bob@example.com",
				)
			]
		)

		reaped = ingestion.reap_stale_in_flight_documents()

		self.assertEqual(reaped[0].processing_requested_by, "bob@example.com")


class TestAuditTrailIntegrity(TestCase):
	"""§24: `write_audit_log` is how the platform answers "what happened?".

	Its only existing coverage lives in bench-only suites that never execute
	offline or under the mutation gate, so in practice the audit writer was
	unverified. It is not a passive logger: it isolates itself in a savepoint
	so a failed audit cannot poison the caller's transaction, truncates
	attacker-influenced text, captures the acting user, and supports a
	fail-closed mode for security-sensitive callers.

	**Boundary.** fakebench records savepoint and rollback calls but does not
	undo writes, so "the caller's transaction survived a failed audit" is
	asserted through the savepoint/rollback protocol rather than by observing
	a real rollback. Actual transactional isolation is runtime-tier.
	"""

	def _bench(self, *, user="alice@example.com"):
		bench = install(FakeBench())
		bench.register_doctype(
			"AI Audit Log",
			[
				"action",
				"category",
				"severity",
				"user",
				"message",
				"details",
				"reference_doctype",
				"reference_name",
				"ip_address",
				"site_user_agent",
			],
		)
		bench.session.user = user
		return bench, import_app("ai_fr_hg.ai.logging")

	def _entries(self, bench):
		return list(bench.db.tables["AI Audit Log"].values())

	def test_an_entry_records_actor_action_and_reference(self):
		bench, logging_module = self._bench(user="alice@example.com")

		logging_module.write_audit_log(
			action="Pipeline Run Cancelled",
			category="Execution",
			severity="Warning",
			message="cancelled by request",
			reference_doctype="AI Pipeline Run",
			reference_name="RUN-1",
		)

		entry = self._entries(bench)[0]
		self.assertEqual(entry["action"], "Pipeline Run Cancelled")
		self.assertEqual(entry["user"], "alice@example.com")
		self.assertEqual(entry["severity"], "Warning")
		self.assertEqual(entry["reference_doctype"], "AI Pipeline Run")
		self.assertEqual(entry["reference_name"], "RUN-1")

	def test_the_actor_is_taken_from_the_session_not_the_caller(self):
		"""An audit entry a caller could attribute to someone else is worthless."""
		bench, logging_module = self._bench(user="bob@example.com")

		logging_module.write_audit_log(action="Thing Happened")

		self.assertEqual(self._entries(bench)[0]["user"], "bob@example.com")

	def test_message_text_is_bounded_before_storage(self):
		bench, logging_module = self._bench()

		logging_module.write_audit_log(action="Long", message="x" * 5000)

		self.assertEqual(len(self._entries(bench)[0]["message"]), 1000)

	def test_details_are_serialised_rather_than_dropped(self):
		bench, logging_module = self._bench()

		logging_module.write_audit_log(action="Detailed", details={"model": "m1", "rejected": 3})

		stored = json.loads(self._entries(bench)[0]["details"])
		self.assertEqual(stored["model"], "m1")
		self.assertEqual(stored["rejected"], 3)

	def test_unserialisable_details_do_not_lose_the_entry(self):
		"""A bad payload must not cost the audit record itself."""
		bench, logging_module = self._bench()

		logging_module.write_audit_log(action="Odd", details={"obj": object()})

		self.assertEqual(len(self._entries(bench)), 1)

	def test_a_failed_audit_is_swallowed_by_default(self):
		"""Observational call sites must not break the operation they describe."""
		bench, logging_module = self._bench()
		bench.db.fail_next_write = True

		logging_module.write_audit_log(action="Best Effort")

		self.assertEqual(self._entries(bench), [])

	def test_a_failed_audit_fails_closed_when_the_caller_demands_it(self):
		"""§24: a security-sensitive state change must not proceed unaudited."""
		bench, logging_module = self._bench()
		bench.db.fail_next_write = True

		with self.assertRaises(Exception):
			logging_module.write_audit_log(action="Must Be Audited", raise_on_error=True)

	def test_a_failed_audit_rolls_back_only_its_own_savepoint(self):
		"""The caller's work must survive an audit failure.

		fakebench does not undo writes on rollback, so this asserts the
		protocol -- a savepoint is taken, and rollback names that savepoint
		rather than discarding the whole transaction. Real isolation is
		runtime-tier.
		"""
		bench, logging_module = self._bench()
		bench.db.fail_next_write = True

		logging_module.write_audit_log(action="Doomed")

		self.assertEqual(bench.db.rolled_back, 1)
		self.assertTrue(bench.db.savepoints, "no savepoint was taken to roll back to")

	def test_a_successful_audit_releases_its_savepoint(self):
		"""Leaked savepoints accumulate across a long-running job."""
		bench, logging_module = self._bench()

		logging_module.write_audit_log(action="Fine")

		self.assertEqual(bench.db.savepoints, [])
		self.assertEqual(bench.db.rolled_back, 0)


class TestAutomationEventIdempotency(TestCase):
	"""One document change must produce one execution, however often it is delivered.

	Frappe's queue is at-least-once, and `doc_events` can fire more than once
	for a single logical change, so `_register_event` is the deduplication
	boundary. `event_revision_key` had a test for its *string format*; the
	decision that consumes it had none — neither `_register_event` nor
	`_insert_event` was referenced anywhere in the repository.

	The failure this prevents is not a duplicate row. It is a duplicate
	*effect*: the target field written twice, the model billed twice, two
	audit entries for one cause.
	"""

	def _bench(self, *, coalesce=1):
		bench = install(FakeBench())
		bench.register_doctype(
			"AI Automation Event",
			[
				"rule",
				"event",
				"status",
				"requested_by",
				"source_doctype",
				"source_name",
				"document_modified",
				"revision_key",
				"snapshot",
			],
		)
		bench.session.user = "alice@example.com"
		automation = import_app("ai_fr_hg.ai.automation")
		rule = types.SimpleNamespace(
			name="RULE-1",
			coalesce_events=coalesce,
			get=lambda key, default=None: {"coalesce_events": coalesce}.get(key, default),
		)
		return bench, automation, rule

	@staticmethod
	def _doc(name="TODO-1", modified="2026-08-21 10:00:00"):
		return types.SimpleNamespace(
			doctype="ToDo",
			name=name,
			modified=modified,
			as_dict=lambda: {"doctype": "ToDo", "name": name, "modified": modified},
		)

	def _events(self, bench):
		return list(bench.db.tables["AI Automation Event"].values())

	def test_the_same_revision_delivered_twice_runs_once(self):
		"""At-least-once delivery must not become at-least-once execution."""
		bench, automation, rule = self._bench()
		doc = self._doc()

		first = automation._register_event(rule, doc, "on_update")
		second = automation._register_event(rule, doc, "on_update")

		self.assertEqual(first["status"], "Queued")
		self.assertTrue(second["skipped"])
		self.assertEqual(second["reason"], "duplicate_revision")
		self.assertEqual(second["event"], first["event"])
		self.assertEqual(len([e for e in self._events(bench) if e["status"] == "Queued"]), 1)

	def test_a_genuinely_new_revision_is_not_suppressed(self):
		"""Dedupe must not swallow a real second change (the AUTO-04 defect)."""
		bench, automation, rule = self._bench(coalesce=0)

		automation._register_event(rule, self._doc(modified="2026-08-21 10:00:00"), "on_update")
		second = automation._register_event(rule, self._doc(modified="2026-08-21 10:05:00"), "on_update")

		self.assertNotIn("skipped", second)
		self.assertEqual(second["status"], "Queued")
		self.assertEqual(len([e for e in self._events(bench) if e["status"] == "Queued"]), 2)

	def test_the_revision_key_is_deterministic_across_deliveries(self):
		"""A key with any per-call entropy would defeat deduplication entirely."""
		_, _automation, _rule = self._bench()
		module = import_app("ai_fr_hg.ai.automation_utils")

		first = module.event_revision_key("RULE-1", "ToDo", "TODO-1", "2026-08-21 10:00:00")
		second = module.event_revision_key("RULE-1", "ToDo", "TODO-1", "2026-08-21 10:00:00")

		self.assertEqual(first, second)
		# Distinct on every component that identifies the work.
		self.assertNotEqual(
			first, module.event_revision_key("RULE-2", "ToDo", "TODO-1", "2026-08-21 10:00:00")
		)
		self.assertNotEqual(
			first, module.event_revision_key("RULE-1", "Note", "TODO-1", "2026-08-21 10:00:00")
		)
		self.assertNotEqual(
			first, module.event_revision_key("RULE-1", "ToDo", "TODO-2", "2026-08-21 10:00:00")
		)
		self.assertNotEqual(
			first, module.event_revision_key("RULE-1", "ToDo", "TODO-1", "2026-08-21 11:00:00")
		)

	def test_the_identity_is_stored_before_the_work_is_queued(self):
		"""A key written after execution cannot deduplicate anything."""
		bench, automation, rule = self._bench()

		result = automation._register_event(rule, self._doc(), "on_update")

		queued = next(e for e in self._events(bench) if e["name"] == result["event"])
		self.assertEqual(queued["revision_key"], "RULE-1::ToDo::TODO-1::2026-08-21 10:00:00")
		self.assertEqual(queued["status"], "Queued")

	def test_a_completed_revision_is_still_suppressed(self):
		"""Re-delivery after success must not re-run the work."""
		bench, automation, rule = self._bench()
		first = automation._register_event(rule, self._doc(), "on_update")
		bench.db.tables["AI Automation Event"][first["event"]]["status"] = "Success"

		second = automation._register_event(rule, self._doc(), "on_update")

		self.assertTrue(second["skipped"])
		self.assertEqual(second["reason"], "duplicate_revision")

	def test_a_failed_revision_may_be_retried(self):
		"""Suppression must not turn a transient failure into a permanent one."""
		bench, automation, rule = self._bench(coalesce=0)
		first = automation._register_event(rule, self._doc(), "on_update")
		bench.db.tables["AI Automation Event"][first["event"]]["status"] = "Failed"

		second = automation._register_event(rule, self._doc(), "on_update")

		self.assertEqual(second["status"], "Queued")

	def test_coalescing_records_the_superseded_change_rather_than_dropping_it(self):
		"""A coalesced event must remain visible for audit, not vanish."""
		bench, automation, rule = self._bench(coalesce=1)
		automation._register_event(rule, self._doc(modified="2026-08-21 10:00:00"), "on_update")

		second = automation._register_event(rule, self._doc(modified="2026-08-21 10:05:00"), "on_update")

		self.assertEqual(second["status"], "Coalesced")
		self.assertTrue(second["skipped"])
		statuses = sorted(e["status"] for e in self._events(bench))
		self.assertEqual(statuses, ["Coalesced", "Queued"])

	def test_a_coalesced_row_does_not_collide_with_the_live_revision_key(self):
		"""Two rows sharing a unique key would fail the insert outright."""
		bench, automation, rule = self._bench(coalesce=1)
		automation._register_event(rule, self._doc(modified="2026-08-21 10:00:00"), "on_update")
		automation._register_event(rule, self._doc(modified="2026-08-21 10:05:00"), "on_update")

		keys = [e["revision_key"] for e in self._events(bench)]
		self.assertEqual(len(keys), len(set(keys)), "revision keys collided")

	def test_coalescing_is_scoped_to_one_document(self):
		"""An unrelated document must not be suppressed by a busy neighbour."""
		_, automation, rule = self._bench(coalesce=1)
		automation._register_event(rule, self._doc(name="TODO-1"), "on_update")

		other = automation._register_event(rule, self._doc(name="TODO-2"), "on_update")

		self.assertEqual(other["status"], "Queued")

	def test_the_immutable_snapshot_is_captured_at_registration(self):
		"""AUTO-01: delete-event automation depends on this being stored up front."""
		bench, automation, rule = self._bench()

		result = automation._register_event(rule, self._doc(), "on_trash")

		stored = json.loads(next(e for e in self._events(bench) if e["name"] == result["event"])["snapshot"])
		self.assertEqual(stored["name"], "TODO-1")


class TestRetryExhaustion(TestCase):
	"""Retries must terminate, and cancellation must not consume an attempt.

	`_max_retries` and the reconciliation filter that applies it were never
	referenced by a test. The failure mode is a document that requeues
	forever: each attempt burns extraction and model budget, and because the
	row keeps changing it never looks stuck.
	"""

	def _bench(self, *, max_retries=2, rows=()):
		bench = install(FakeBench())
		bench.register_doctype("AI Platform Settings", ["max_retries"], is_single=True)
		bench.db.singles["AI Platform Settings"] = {"max_retries": max_retries}
		bench.register_doctype(
			"AI Document",
			[
				"status",
				"retry_count",
				"processing_heartbeat",
				"processing_requested_by",
				"error_type",
				"error_message",
				"processing_message",
			],
		)
		for row in rows:
			bench.db.insert_row("AI Document", row)
		return bench, import_app("ai_fr_hg.ai.ingestion")

	@staticmethod
	def _failed(name, retry_count, modified="2026-08-21 10:00:00"):
		return {
			"name": name,
			"status": "Failed",
			"retry_count": retry_count,
			"modified": modified,
			"owner": "alice@example.com",
			"processing_requested_by": "alice@example.com",
			"processing_heartbeat": modified,
		}

	def test_the_retry_ceiling_comes_from_settings(self):
		_, ingestion = self._bench(max_retries=5)

		self.assertEqual(ingestion._max_retries(), 5)

	def test_an_unset_ceiling_falls_back_to_a_bounded_default(self):
		"""An unconfigured site must not mean unlimited retries."""
		bench, ingestion = self._bench()
		bench.db.singles["AI Platform Settings"]["max_retries"] = None

		self.assertEqual(ingestion._max_retries(), 2)

	def test_a_negative_ceiling_is_clamped_rather_than_inverting_the_filter(self):
		bench, ingestion = self._bench()
		bench.db.singles["AI Platform Settings"]["max_retries"] = -5

		self.assertEqual(ingestion._max_retries(), 0)

	def test_documents_past_the_ceiling_are_not_requeued(self):
		"""The exhaustion boundary: this is what stops an infinite retry loop."""
		_, ingestion = self._bench(
			max_retries=2,
			rows=[self._failed("DOC-OK", 1), self._failed("DOC-EXHAUSTED", 9)],
		)
		enqueued = []
		ingestion.enqueue_processing = lambda name, requested_by=None: enqueued.append(name)

		ingestion.process_pending_documents()

		self.assertIn("DOC-OK", enqueued)
		self.assertNotIn("DOC-EXHAUSTED", enqueued)

	def test_reconciliation_is_bounded_per_sweep(self):
		"""A large failure backlog must not become one unbounded sweep."""
		_, ingestion = self._bench(
			max_retries=99,
			rows=[self._failed(f"DOC-{i}", 0) for i in range(60)],
		)
		enqueued = []
		ingestion.enqueue_processing = lambda name, requested_by=None: enqueued.append(name)

		ingestion.process_pending_documents()

		self.assertLessEqual(len(enqueued), 40, "reconciliation enqueued an unbounded batch")

	def test_a_document_is_never_enqueued_twice_in_one_sweep(self):
		"""A row can match both the stale-in-flight and failed queries."""
		bench, ingestion = self._bench(
			max_retries=9,
			rows=[self._failed("DOC-DUP", 0)],
		)
		# Also make it look like a dead in-flight worker.
		bench.db.tables["AI Document"]["DOC-DUP"]["status"] = "Extracting"
		enqueued = []
		ingestion.enqueue_processing = lambda name, requested_by=None: enqueued.append(name)

		ingestion.process_pending_documents()

		self.assertEqual(enqueued.count("DOC-DUP"), 1)

	def test_the_retry_runs_under_the_durable_requester(self):
		"""§22: a retry must not silently acquire the scheduler's authority."""
		bench, ingestion = self._bench(
			max_retries=9,
			rows=[self._failed("DOC-1", 0)],
		)
		bench.db.tables["AI Document"]["DOC-1"]["processing_requested_by"] = "bob@example.com"
		seen = {}
		ingestion.enqueue_processing = lambda name, requested_by=None: seen.setdefault(name, requested_by)

		ingestion.process_pending_documents()

		self.assertEqual(seen["DOC-1"], "bob@example.com")

	def test_one_failing_document_does_not_abort_the_sweep(self):
		"""Reconciliation is a batch: one bad row must not strand the rest."""
		_, ingestion = self._bench(
			max_retries=9,
			rows=[self._failed("DOC-BAD", 0), self._failed("DOC-GOOD", 0)],
		)
		enqueued = []

		def flaky(name, requested_by=None):
			if name == "DOC-BAD":
				raise RuntimeError("enqueue exploded")
			enqueued.append(name)

		ingestion.enqueue_processing = flaky
		ingestion.process_pending_documents()

		self.assertIn("DOC-GOOD", enqueued)


class TestLearningTrustBoundary(TestCase):
	"""Can untrusted feedback become trusted knowledge, and whose?

	The learning loop turns user input into content that shapes future
	answers, so it is the platform's clearest poisoning surface. None of
	`_default_scope`, `_validate_scope`, `_memory_applies` or `recall` was
	referenced by any offline test.

	Two failure directions matter, and they are opposite. Too permissive and
	an ordinary user teaches the whole tenant, or reads another user's
	memories. Too strict and legitimate teaching silently stops working.
	"""

	def _bench(self, *, user="alice@example.com", roles=(), memories=()):
		bench = install(FakeBench())
		bench.register_doctype("User", ["enabled"])
		bench.register_doctype("Role", [])
		bench.register_doctype("AI Agent", [])
		# Load the real Settings schema rather than listing fields by hand, so
		# the fixture cannot drift from the DocType and a field added upstream
		# does not silently abort `teach()` before the scope check runs.
		settings_meta = bench.load_doctype_json(
			APP / "ai_core" / "doctype" / "ai_platform_settings" / "ai_platform_settings.json"
		)
		settings_meta.is_single = True
		bench.db.singles["AI Platform Settings"] = dict.fromkeys(settings_meta.fieldnames(), 0)
		bench.db.singles["AI Platform Settings"].update(
			{"memory_top_k": 10, "learning_enabled": 1, "enable_learning": 1}
		)
		bench.register_doctype(
			"AI Memory",
			[
				"content",
				"memory_type",
				"scope",
				"scope_value",
				"status",
				"confidence",
				"usage_count",
				"last_used_on",
			],
		)
		bench.register_doctype(
			"AI Skill",
			["skill_name", "description", "instructions", "skill_type", "enabled", "scope", "scope_value"],
		)
		for name in ("alice@example.com", "bob@example.com", "boss@example.com"):
			bench.db.insert_row("User", {"name": name, "enabled": 1})
		bench.db.insert_row("Role", {"name": "Legal"})
		bench.db.insert_row("AI Agent", {"name": "AGENT-1"})
		for row in memories:
			bench.db.insert_row("AI Memory", row)
		bench.session.user = user
		bench.roles[user] = list(roles)
		return bench, import_app("ai_fr_hg.ai.learning")

	@staticmethod
	def _memory(name, scope, value, content="secret"):
		return {
			"name": name,
			"content": content,
			"scope": scope,
			"scope_value": value,
			"status": "Active",
		}

	# -- tenant isolation --------------------------------------------------

	def test_a_user_scoped_memory_is_invisible_to_another_user(self):
		"""The core isolation claim: one user's memory must not leak."""
		_, learning = self._bench(user="bob@example.com")

		self.assertFalse(
			learning._memory_applies(
				self._memory("M1", "User", "alice@example.com"), "bob@example.com", set(), None
			)
		)

	def test_a_user_scoped_memory_is_visible_to_its_owner(self):
		_, learning = self._bench(user="alice@example.com")

		self.assertTrue(
			learning._memory_applies(
				self._memory("M1", "User", "alice@example.com"), "alice@example.com", set(), None
			)
		)

	def test_role_scope_requires_holding_the_role(self):
		_, learning = self._bench()
		memory = self._memory("M1", "Role", "Legal")

		self.assertTrue(learning._memory_applies(memory, "alice@example.com", {"Legal"}, None))
		self.assertFalse(learning._memory_applies(memory, "alice@example.com", {"Finance"}, None))

	def test_agent_scope_requires_the_matching_agent(self):
		_, learning = self._bench()
		memory = self._memory("M1", "Agent", "AGENT-1")

		self.assertTrue(learning._memory_applies(memory, "alice@example.com", set(), "AGENT-1"))
		self.assertFalse(learning._memory_applies(memory, "alice@example.com", set(), "AGENT-2"))

	def test_a_scoped_memory_with_no_value_is_not_treated_as_global(self):
		"""A malformed row must fail closed, not become visible to everyone."""
		_, learning = self._bench()

		for scope in ("User", "Role", "Agent"):
			self.assertFalse(
				learning._memory_applies(
					self._memory("M1", scope, None), "alice@example.com", {"Legal"}, "AGENT-1"
				),
				scope,
			)

	def test_recall_returns_only_memories_in_the_callers_scope(self):
		"""End-to-end: the predicate must actually be applied by recall()."""
		_bench_unused, learning = self._bench(
			user="bob@example.com",
			memories=[
				self._memory("M-MINE", "User", "bob@example.com", "quarterly revenue figures"),
				self._memory("M-THEIRS", "User", "alice@example.com", "quarterly revenue secret"),
				self._memory("M-GLOBAL", "Global", None, "quarterly revenue policy"),
			],
		)

		# All three are equally relevant to the query, so anything excluded
		# was excluded by scope rather than by ranking.
		memories, _skills = learning.recall("quarterly revenue", user="bob@example.com", track_usage=False)

		contents = {m.get("content") for m in memories}
		self.assertIn("quarterly revenue figures", contents)
		self.assertIn("quarterly revenue policy", contents)
		self.assertNotIn("quarterly revenue secret", contents)

	# -- poisoning resistance ---------------------------------------------

	def test_an_ordinary_user_teaching_is_confined_to_their_own_scope(self):
		"""Untrusted feedback must not default to tenant-wide knowledge."""
		_, learning = self._bench(user="alice@example.com", roles=[])

		scope, value = learning._default_scope("Fact", "Chat Correction", "alice@example.com")

		self.assertEqual((scope, value), ("User", "alice@example.com"))

	def test_an_ordinary_user_cannot_request_global_scope(self):
		_, learning = self._bench(user="alice@example.com", roles=[])

		ok, message = learning._validate_scope("Global", None, "alice@example.com")

		self.assertFalse(ok)
		self.assertIn("AI Manager", message)

	def test_an_ordinary_user_cannot_target_another_user(self):
		"""Otherwise one user could poison a specific colleague's answers."""
		_, learning = self._bench(user="alice@example.com", roles=[])

		ok, _message = learning._validate_scope("User", "bob@example.com", "alice@example.com")

		self.assertFalse(ok)

	def test_an_ordinary_user_cannot_target_a_role_or_agent(self):
		_, learning = self._bench(user="alice@example.com", roles=[])

		self.assertFalse(learning._validate_scope("Role", "Legal", "alice@example.com")[0])
		self.assertFalse(learning._validate_scope("Agent", "AGENT-1", "alice@example.com")[0])

	def test_a_manager_may_teach_globally(self):
		"""The restriction must not break legitimate curation."""
		_, learning = self._bench(user="boss@example.com", roles=["AI Manager"])

		self.assertTrue(learning._validate_scope("Global", None, "boss@example.com")[0])

	def test_manager_feedback_still_defaults_to_user_scope(self):
		"""Even a manager's correction is an opinion until deliberately promoted."""
		_, learning = self._bench(user="boss@example.com", roles=["AI Manager"])

		scope, value = learning._default_scope("Feedback", "Feedback", "boss@example.com")

		self.assertEqual((scope, value), ("User", "boss@example.com"))

	def test_a_scope_target_that_does_not_exist_is_refused(self):
		"""Scope values are attacker-supplied and must be validated."""
		_, learning = self._bench(user="boss@example.com", roles=["AI Manager"])

		self.assertFalse(learning._validate_scope("User", "ghost@example.com", "boss@example.com")[0])
		self.assertFalse(learning._validate_scope("Role", "Nonexistent", "boss@example.com")[0])
		self.assertFalse(learning._validate_scope("Agent", "AGENT-404", "boss@example.com")[0])

	def test_an_unknown_scope_name_is_rejected(self):
		_, learning = self._bench(user="boss@example.com", roles=["AI Manager"])

		self.assertFalse(learning._validate_scope("Everyone", None, "boss@example.com")[0])

	def test_a_memory_with_an_unrecognised_scope_is_withheld(self):
		"""An unreadable scope must fail closed, not broadcast to everyone.

		Frappe enforces Select options when a document is saved, not on the
		direct SQL, patches and imports that also write this table, so a row
		carrying an unknown scope is reachable. The safe reading of a scope
		the code does not understand is "show nobody", not "show everyone".
		"""
		_, learning = self._bench(user="bob@example.com")

		applies = learning._memory_applies(
			{"scope": "Everyone", "scope_value": None},
			"bob@example.com",
			set(),
			None,
		)

		self.assertFalse(applies)

	def test_an_ordinary_user_cannot_teach_globally_through_the_api(self):
		"""The escalation must be closed at the service, not just the default.

		`_default_scope` only picks a safe scope for callers who omit one.
		`target_scope` is caller-supplied all the way from
		`api.learning.teach`, so this asserts the explicit request is refused
		rather than trusting the default to cover it.
		"""
		bench, learning = self._bench(user="bob@example.com")
		bench.db.singles.setdefault("AI Platform Settings", {})["enable_learning"] = 1

		with self.assertRaises(Exception) as caught:
			learning.teach(content="everyone should know this", target_scope="Global")

		self.assertIn("AI Manager", str(caught.exception))
