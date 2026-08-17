# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Pure unit tests for the Learning Loop's scoring / dedup / formatting logic.

These deliberately touch no database and no model runtime, so they run with
plain `python -m unittest` (or pytest) with no Frappe site present. Frappe's
integration tests exercise the orchestration layer in the colocated
:mod:`ai_fr_hg.ai_learning.doctype.ai_knowledge_candidate.test_ai_knowledge_candidate`
module instead.
"""

import unittest

from ai_fr_hg.ai import learning_utils as lu


class TestScoreRelevance(unittest.TestCase):
	def test_no_overlap_is_zero(self):
		self.assertEqual(lu.score_relevance("refund policy", "The sky is blue today."), 0.0)
		self.assertEqual(lu.score_relevance("", "anything"), 0.0)
		self.assertEqual(lu.score_relevance("anything", ""), 0.0)

	def test_exact_match_scores_high(self):
		self.assertGreater(
			lu.score_relevance("use markdown tables", "Always use markdown tables for results."), 0.5
		)

	def test_case_and_punctuation_insensitive(self):
		a = lu.score_relevance("Refund Policy", "Our refund policy allows returns within thirty days.")
		b = lu.score_relevance("refund policy", "refund policy allows returns")
		self.assertGreater(a, 0.0)
		self.assertGreater(b, 0.0)

	def test_more_query_coverage_scores_higher(self):
		query = "always greet the customer by name"
		partial = lu.score_relevance(query, "Always greet the customer warmly.")
		full = lu.score_relevance(query, "Always greet the customer by name and smile.")
		self.assertGreater(full, partial)


class TestRankMemories(unittest.TestCase):
	def test_sorts_by_relevance_descending(self):
		memories = [
			{"name": "a", "content": "Refunds are allowed within thirty days."},
			{"name": "b", "content": "The office closes at five on Fridays."},
			{"name": "c", "content": "Refund requests need manager approval."},
		]
		ranked = lu.rank_memories("refund approval", memories)
		# 'c' covers both query tokens; 'a' covers only 'refund'.
		self.assertEqual(ranked[0]["name"], "c")
		self.assertEqual(ranked[1]["name"], "a")

	def test_irrelevant_memories_are_dropped(self):
		memories = [{"name": "a", "content": "Colour scheme is dark mode."}]
		self.assertEqual(lu.rank_memories("what is the refund period", memories), [])

	def test_empty_input_is_safe(self):
		self.assertEqual(lu.rank_memories(None, []), [])
		self.assertEqual(lu.rank_memories("q", []), [])


class TestDeduplication(unittest.TestCase):
	def test_near_duplicate_detected(self):
		self.assertTrue(
			lu.is_near_duplicate("Always cite sources in answers.", "Always cite sources in your answers.")
		)
		self.assertTrue(
			lu.is_near_duplicate("Refunds require manager approval.", "Refunds require the manager approval.")
		)

	def test_distinct_text_not_duplicate(self):
		self.assertFalse(lu.is_near_duplicate("refund policy is thirty days", "colours are dark mode"))

	def test_empty_is_never_duplicate(self):
		self.assertFalse(lu.is_near_duplicate("", "x"))
		self.assertFalse(lu.is_near_duplicate("x", ""))

	def test_dedupe_key_normalises(self):
		self.assertEqual(lu.dedupe_key("  Refund, Policy!  "), "refund policy")


class TestClassifyCandidate(unittest.TestCase):
	def test_instruction_detected(self):
		self.assertEqual(
			lu.classify_candidate("Always use markdown tables when comparing options."), "Instruction"
		)
		self.assertEqual(lu.classify_candidate("Never share internal prices with customers."), "Instruction")
		self.assertEqual(lu.classify_candidate("1. Read the file\n2. Summarise it"), "Instruction")

	def test_preference_detected(self):
		self.assertEqual(lu.classify_candidate("I prefer concise bullet points in summaries."), "Preference")

	def test_fact_default(self):
		self.assertEqual(lu.classify_candidate("The refund period is thirty days."), "Fact")
		self.assertEqual(lu.classify_candidate(""), "Fact")


class TestBuildBlocks(unittest.TestCase):
	def test_memory_block_numbers_and_types(self):
		block = lu.build_memory_block(
			[
				{"name": "m1", "content": "Use dark mode.", "memory_type": "Preference"},
				{"name": "m2", "content": "Refunds are thirty days.", "memory_type": "Fact"},
			]
		)
		self.assertIn("[1] (Preference) Use dark mode.", block)
		self.assertIn("[2] (Fact) Refunds are thirty days.", block)

	def test_memory_block_empty(self):
		self.assertEqual(lu.build_memory_block([]), "")

	def test_skill_block_includes_name_and_instructions(self):
		block = lu.build_skill_block(
			[
				{
					"name": "invoice summary",
					"description": "Summarise invoices.",
					"instructions": "Step one: read.",
				}
			]
		)
		self.assertIn("SKILL: invoice summary", block)
		self.assertIn("Step one: read.", block)

	def test_blocks_respect_character_budget(self):
		block = lu.build_memory_block(
			[{"name": f"m{i}", "content": "x" * 100, "memory_type": "Fact"} for i in range(20)],
			max_characters=250,
		)
		self.assertLessEqual(len(block), 400)


class TestSkillToDict(unittest.TestCase):
	def test_skill_dict_carries_scope(self):
		from types import SimpleNamespace

		skill = SimpleNamespace(
			skill_name="invoice review",
			name="AI Skill",
			description="Review invoices.",
			instructions="Step one: read.",
			skill_type="Workflow",
			scope="Role",
			scope_value="Accounts",
		)
		as_dict = lu.skill_to_dict(skill)
		self.assertEqual(as_dict["scope"], "Role")
		self.assertEqual(as_dict["scope_value"], "Accounts")
		self.assertEqual(as_dict["instructions"], "Step one: read.")


if __name__ == "__main__":
	unittest.main()
