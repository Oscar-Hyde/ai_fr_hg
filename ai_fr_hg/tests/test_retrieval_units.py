# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Pure unit tests for Phase 2 retrieval algorithms (no database)."""

from pathlib import Path
from types import SimpleNamespace

try:
	from frappe.tests import UnitTestCase
except ImportError:
	from unittest import TestCase as UnitTestCase

from ai_fr_hg.ai import retrieval_utils as ru
from ai_fr_hg.ai import vector


class _Chunk(SimpleNamespace):
	pass


class TestTokenizeQuery(UnitTestCase):
	def test_keeps_identifiers_and_deduplicates(self):
		terms = ru.tokenize_query("Refund INV-2024 refund policy SKUs/AA")
		self.assertEqual(terms[0], "refund")
		self.assertIn("inv-2024", terms)
		self.assertIn("skus/aa", terms)
		self.assertEqual(terms.count("refund"), 1)

	def test_arabic_and_hebrew_terms(self):
		terms = ru.tokenize_query("سياسة الاسترجاع ומדיניות החזר")
		self.assertTrue(any(ord(term[0]) > 127 for term in terms))
		self.assertGreaterEqual(len(terms), 2)

	def test_fulltext_eligibility(self):
		self.assertTrue(ru.is_fulltext_term("refund"))
		self.assertFalse(ru.is_fulltext_term("ab"))
		self.assertFalse(ru.is_fulltext_term("سياسة"))
		self.assertFalse(ru.is_fulltext_term("מדיניות"))


class TestKeywordScore(UnitTestCase):
	def test_distinct_terms_outrank_repetition(self):
		terms = ["alpha", "needle"]
		repeated = ru.keyword_score("alpha alpha alpha alpha", terms)
		covered = ru.keyword_score("alpha needle once", terms)
		self.assertGreater(covered, repeated)

	def test_empty_is_zero(self):
		self.assertEqual(ru.keyword_score("", ["a"]), 0.0)
		self.assertEqual(ru.keyword_score("hello", []), 0.0)


class TestFusionAndPolicy(UnitTestCase):
	def test_rrf_prefers_agreement(self):
		semantic = {"a": 0.9, "b": 0.8}
		keyword = {"a": 0.4, "c": 0.9}
		fused = ru.fuse_rrf([semantic, keyword])
		self.assertEqual(max(fused, key=fused.get), "a")

	def test_weights_reorder_and_zero_excludes(self):
		scores = {"c1": 0.5, "c2": 0.5}
		identity = {"c1": "KB-A", "c2": "KB-B"}
		weighted = ru.apply_identity_weights(scores, {"KB-A": 0.1, "KB-B": 5.0}, identity)
		self.assertGreater(weighted["c2"], weighted["c1"])
		dropped = ru.apply_identity_weights(scores, {"KB-A": 0, "KB-B": 1}, identity)
		self.assertNotIn("c1", dropped)
		self.assertIn("c2", dropped)

	def test_per_group_top_k(self):
		scores = {"a1": 0.9, "a2": 0.8, "a3": 0.7, "b1": 0.6}
		groups = {"a1": "A", "a2": "A", "a3": "A", "b1": "B"}
		kept = ru.take_top_per_group(scores, groups, {"A": 2, "B": 1}, default_limit=6)
		self.assertEqual(set(kept), {"a1", "a2", "b1"})

	def test_group_thresholds(self):
		scores = {"a": 0.9, "b": 0.2}
		groups = {"a": "KA", "b": "KB"}
		kept = ru.apply_group_thresholds(scores, groups, {"KA": 0.5, "KB": 0.5}, default_threshold=0.25)
		self.assertEqual(set(kept), {"a"})


class TestContextPacking(UnitTestCase):
	def test_oversized_first_block_still_yields_excerpt(self):
		chunk = _Chunk(document="DOC-1", content="x" * 20000)
		kept, text = ru.pack_context_blocks(
			[(chunk, "[1] Handbook", chunk.content)],
			limit=800,
		)
		self.assertEqual(kept, [chunk])
		self.assertTrue(text.startswith("[1] Handbook"))
		self.assertLessEqual(len(text), 800)
		self.assertIn("…", text)

	def test_budget_includes_later_blocks_when_they_fit(self):
		a = _Chunk(document="A", content="alpha " * 20)
		b = _Chunk(document="B", content="beta " * 20)
		kept, text = ru.pack_context_blocks(
			[(a, "[1] A", a.content), (b, "[2] B", b.content)],
			limit=5000,
		)
		self.assertEqual(len(kept), 2)
		self.assertIn("[1] A", text)
		self.assertIn("[2] B", text)

	def test_overlapping_same_document_is_deduplicated(self):
		long = "Employees receive twenty days of leave each calendar year. " * 3
		a = _Chunk(document="DOC-1", content=long)
		b = _Chunk(document="DOC-1", content=long[20:80])
		kept, text = ru.pack_context_blocks(
			[(a, "[1] A", a.content), (b, "[2] B", b.content)],
			limit=5000,
		)
		self.assertEqual(kept, [a])
		self.assertNotIn("[2] B", text)

	def test_sibling_documents_are_not_deduplicated(self):
		a = _Chunk(document="DOC-1", content="same text in both")
		b = _Chunk(document="DOC-2", content="same text in both")
		kept, _text = ru.pack_context_blocks(
			[(a, "[1] A", a.content), (b, "[2] B", b.content)],
			limit=5000,
		)
		self.assertEqual(kept, [a, b])

	def test_citation_numbers_follow_the_packed_list(self):
		long = "Employees receive twenty days of leave each calendar year. " * 3
		a = _Chunk(document="DOC-1", content=long)
		b = _Chunk(document="DOC-1", content=long[20:80])
		c = _Chunk(document="DOC-2", content="Overtime must be approved in advance.")
		kept, text = ru.pack_context_blocks(
			[(a, "Handbook", a.content), (b, "Dup", b.content), (c, "Policy", c.content)],
			limit=5000,
			number_citations=True,
		)
		self.assertEqual(kept, [a, c])
		self.assertIn("[1] Handbook", text)
		self.assertIn("[2] Policy", text)
		self.assertNotIn("[2] Dup", text)
		self.assertNotIn("[3]", text)


class TestRetrievalSourceContract(UnitTestCase):
	def test_old_candidate_caps_are_absent_from_the_retriever(self):
		source = (Path(__file__).resolve().parents[1] / "ai" / "retrieval.py").read_text()
		self.assertNotIn("limit_page_length=max(top_k * 20, 200)", source)
		self.assertNotIn("limit_page_length=min(max(cint(limit), 1), 500)", source)
		self.assertNotIn('["folder", "like", f"{norm}%"]', source)
		self.assertIn("brute_force", source)
		self.assertIn('reranker: str = "unsupported"', source)
		self.assertIn("def search_facets", source)
		self.assertNotIn("limit_page_length=5000", source)

	def test_agent_wires_folder_weights_and_packed_citations(self):
		source = (Path(__file__).resolve().parents[1] / "ai" / "agent.py").read_text()
		self.assertIn("folder=folder", source)
		self.assertIn("get_agent_knowledge_base_weights", source)
		self.assertIn("packed=packed", source)
		self.assertIn("or folder", source)


class TestScorePairsCompleteness(UnitTestCase):
	def test_score_pairs_does_not_truncate(self):
		query = vector.normalize([1.0, 0.0])
		candidates = [(str(i), vector.normalize([0.0, 1.0])) for i in range(250)]
		candidates.append(("needle", vector.normalize([1.0, 0.0])))
		scored = vector.score_pairs(query, candidates)
		self.assertEqual(len(scored), 251)
		best = max(scored, key=lambda row: row[1])
		self.assertEqual(best[0], "needle")

	def test_incompatible_dimensions_are_omitted(self):
		query = [1.0, 0.0]
		scored = vector.score_pairs(query, [("ok", [1.0, 0.0]), ("bad", [1.0, 0.0, 0.0])])
		self.assertEqual([name for name, _ in scored], ["ok"])
