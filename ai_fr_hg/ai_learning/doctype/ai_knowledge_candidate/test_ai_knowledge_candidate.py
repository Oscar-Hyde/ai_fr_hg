# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Frappe integration coverage for this DocType and its canonical domain services."""

from unittest.mock import patch

import frappe

from ai_fr_hg.tests.integration_test_case import AIPlatformTestCase


class TestLearningLoop(AIPlatformTestCase):
	"""The teach → validate → conflict → approve → recall → observe lifecycle."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		# Cache the original values so tearDownClass can restore them.
		cls._original_learning = frappe.db.get_single_value(
			"AI Platform Settings", "learning_enabled"
		)
		cls._original_approval = frappe.db.get_single_value(
			"AI Platform Settings", "require_memory_approval"
		)
		# Enable the learning loop for all tests in this class.
		frappe.db.set_single_value("AI Platform Settings", "learning_enabled", 1)
		frappe.db.set_single_value("AI Platform Settings", "require_memory_approval", 1)
		frappe.clear_cache()

	@classmethod
	def tearDownClass(cls):
		# Restore original values so other test classes are not affected.
		frappe.db.set_single_value(
			"AI Platform Settings", "learning_enabled", cls._original_learning
		)
		frappe.db.set_single_value(
			"AI Platform Settings", "require_memory_approval", cls._original_approval
		)
		frappe.clear_cache()
		super().tearDownClass()

	def make_memory(self, content, candidate_type="Fact"):
		from ai_fr_hg.ai.learning import _promote_to_memory, create_candidate

		candidate = create_candidate(content=content, candidate_type=candidate_type, user="Administrator")
		candidate.db_set("status", "Validated")
		return _promote_to_memory(candidate)["name"]

	def test_teach_creates_a_validated_candidate(self):
		from ai_fr_hg.ai.learning import create_candidate, validate_candidate

		candidate = create_candidate(
			content="The refund period is thirty days.", candidate_type="Fact", user="Administrator"
		)
		self.assertEqual(candidate.status, "Draft")
		self.assertEqual(candidate.user, "Administrator")

		report = validate_candidate(candidate)
		self.assertTrue(report["valid"])

	def test_provenance_is_server_attributed_and_caller_context_is_labelled_unverified(self):
		from ai_fr_hg.ai.learning import create_candidate

		candidate = create_candidate(
			content="A provenance-sensitive fact.",
			user="Administrator",
			provenance="Approved by an external authority.",
		)
		self.assertIn("Source Type: Explicit Teaching", candidate.provenance)
		self.assertIn("Teaching User: Administrator", candidate.provenance)
		self.assertIn("Recorded By: Administrator", candidate.provenance)
		self.assertIn("User-Provided Context (Unverified)", candidate.provenance)
		self.assertEqual(candidate.provenance_context, "Approved by an external authority.")

	def test_direct_candidate_insertion_is_attributed_and_audited(self):
		doc = frappe.get_doc(
			{
				"doctype": "AI Knowledge Candidate",
				"title": "Direct Governed Candidate",
				"content": "Direct insertions still use authoritative learning provenance.",
				"candidate_type": "Fact",
				"source_type": "Explicit Teaching",
				"status": "Draft",
				"target_scope": "Global",
				"provenance": "Client-supplied provenance claim",
			}
		).insert(ignore_permissions=True)

		self.assertEqual(doc.user, "Administrator")
		self.assertIn("Teaching User: Administrator", doc.provenance)
		self.assertIn("User-Provided Context (Unverified)", doc.provenance)
		self.assertTrue(
			frappe.db.exists(
				"AI Audit Log",
				{
					"action": "Knowledge Candidate Created",
					"reference_doctype": "AI Knowledge Candidate",
					"reference_name": doc.name,
				},
			)
		)

	def test_direct_candidate_audit_failure_propagates_for_transaction_rollback(self):
		save_point = "candidate_strict_audit_failure"
		frappe.db.savepoint(save_point)
		doc = frappe.get_doc(
			{
				"doctype": "AI Knowledge Candidate",
				"title": "Unaudited Candidate Must Roll Back",
				"content": "This candidate cannot survive a strict audit failure.",
				"candidate_type": "Fact",
				"source_type": "Explicit Teaching",
				"status": "Draft",
				"target_scope": "Global",
			}
		)
		with (
			patch("ai_fr_hg.ai.logging.write_audit_log", side_effect=RuntimeError("audit unavailable")),
			self.assertRaisesRegex(RuntimeError, "audit unavailable"),
		):
			doc.insert(ignore_permissions=True)
		frappe.db.rollback(save_point=save_point)
		frappe.db.release_savepoint(save_point)

		self.assertFalse(frappe.db.exists("AI Knowledge Candidate", doc.name))

	def test_direct_candidate_requires_source_record_for_document_provenance(self):
		doc = frappe.get_doc(
			{
				"doctype": "AI Knowledge Candidate",
				"title": "Missing Source",
				"content": "This claims to come from a document.",
				"candidate_type": "Document",
				"source_type": "Document",
				"status": "Draft",
				"target_scope": "Global",
			}
		)
		with self.assertRaises(frappe.ValidationError):
			doc.insert(ignore_permissions=True)

	def test_empty_teaching_is_rejected(self):
		from ai_fr_hg.ai.learning import LearningError, create_candidate

		with self.assertRaises(LearningError):
			create_candidate(content="   ", user="Administrator")

	def test_approve_fact_creates_memory_with_embedding(self):
		from ai_fr_hg.ai.learning import approve_candidate, create_candidate

		candidate = create_candidate(
			content="Customers are greeted by name on arrival.",
			candidate_type="Preference",
			user="Administrator",
		)
		candidate.db_set("status", "Validated")

		with patch("ai_fr_hg.ai.engine.run_embedding", return_value=[[0.1, 0.2, 0.3]]):
			result = approve_candidate(candidate.name)

		self.assertEqual(result["promoted_to"], "AI Memory")
		memory = frappe.get_doc("AI Memory", result["promoted_name"])
		self.assertEqual(memory.content, candidate.content)
		self.assertTrue(memory.embedding)
		self.assertEqual(memory.embedding_dimensions, 3)
		self.assertEqual(frappe.db.get_value("AI Knowledge Candidate", candidate.name, "status"), "Approved")

	def test_approve_instruction_creates_a_skill(self):
		from ai_fr_hg.ai.learning import approve_candidate, create_candidate

		candidate = create_candidate(
			content="Always use markdown tables when comparing two options.",
			candidate_type="Instruction",
			user="Administrator",
		)
		candidate.db_set("status", "Validated")
		result = approve_candidate(candidate.name)

		self.assertEqual(result["promoted_to"], "AI Skill")
		self.assertTrue(frappe.db.exists("AI Skill", result["promoted_name"]))

	def test_duplicate_teaching_flags_a_conflict(self):
		from ai_fr_hg.ai.learning import teach

		self.make_memory("The office closes at five on weekdays.")
		result = teach("The office closes at five on weekdays.", user="Administrator")

		self.assertEqual(result["status"], "Conflict")
		self.assertTrue(result["conflicts"]["duplicates"])

	def test_reject_never_learns(self):
		from ai_fr_hg.ai.learning import create_candidate, reject_candidate

		candidate = create_candidate(content="Colours are dark mode only.", user="Administrator")
		candidate.db_set("status", "Validated")
		reject_candidate(candidate.name)
		self.assertEqual(frappe.db.get_value("AI Knowledge Candidate", candidate.name, "status"), "Rejected")
		self.assertFalse(frappe.db.exists("AI Memory", {"source_candidate": candidate.name}))

	def test_recall_returns_only_relevant_memories(self):
		from ai_fr_hg.ai.learning import build_memory_context

		self.make_memory("Always cite the source document in answers.")
		self.make_memory("Colours follow the company dark mode palette.")

		memory_block, _skills = build_memory_context("how do i cite sources in my answers", agent=None)
		self.assertIn("LEARNED KNOWLEDGE", memory_block)
		self.assertIn("cite the source document", memory_block)

		# An unrelated question must not pull in the citation memory.
		memory_block2, _ = build_memory_context("what time does the cafeteria close", agent=None)
		self.assertNotIn("cite the source document", memory_block2)

	def test_memory_scoping_hides_other_users_memories(self):
		from ai_fr_hg.ai.learning import recall

		memory_name = self.make_memory("This is a private fact for another user.")
		frappe.db.set_value("AI Memory", memory_name, {"scope": "User", "scope_value": "alice"})

		memories, _skills = recall("private fact", agent=None, user="Administrator")
		self.assertFalse(memories)

		memories_alice, _ = recall("private fact", agent=None, user="alice")
		self.assertTrue(memories_alice)

	def test_observe_negative_feedback_creates_candidate(self):
		from ai_fr_hg.ai.learning import observe_feedback

		conversation = frappe.get_doc(
			{
				"doctype": "AI Conversation",
				"title": "Learning Feedback",
				"user": "Administrator",
				"status": "Active",
			}
		).insert(ignore_permissions=True)
		message = frappe.get_doc(
			{
				"doctype": "AI Message",
				"conversation": conversation.name,
				"role": "Assistant",
				"content": "This answer was wrong and should be corrected.",
				"sequence": 1,
				"status": "Completed",
				"user": "Administrator",
			}
		).insert(ignore_permissions=True)

		result = observe_feedback(message.name, "Negative")
		self.assertTrue(result["candidate"])
		candidate = frappe.get_doc("AI Knowledge Candidate", result["candidate"])
		self.assertEqual(candidate.source_type, "Chat Correction")
		self.assertEqual(candidate.source_reference_name, message.name)
		self.assertEqual(candidate.candidate_type, "Feedback")
		self.assertIn("failure example", candidate.content)
		self.assertNotEqual(candidate.content, message.content)

	def test_preference_defaults_to_teaching_user_scope(self):
		from ai_fr_hg.ai.learning import approve_candidate, create_candidate

		candidate = create_candidate(
			content="I prefer concise monthly reports.",
			candidate_type="Preference",
			user="Administrator",
		)
		candidate.db_set("status", "Validated")
		with patch("ai_fr_hg.ai.engine.run_embedding", return_value=[]):
			result = approve_candidate(candidate.name)

		memory = frappe.get_doc("AI Memory", result["promoted_name"])
		self.assertEqual(memory.scope, "User")
		self.assertEqual(memory.scope_value, "Administrator")

	def test_disabled_approval_gate_auto_promotes_conflict_free_teaching(self):
		from ai_fr_hg.ai.learning import teach

		settings = frappe.get_single("AI Platform Settings")
		settings.learning_enabled = 1
		settings.require_memory_approval = 0
		with (
			patch("ai_fr_hg.ai.learning._settings", return_value=settings),
			patch("ai_fr_hg.ai.engine.run_embedding", return_value=[]),
		):
			result = teach(
				"Warehouse aisle turquoise has a safety inspection every 19 days.",
				user="Administrator",
			)

		self.assertEqual(result["status"], "Approved")
		self.assertTrue(frappe.db.exists("AI Memory", {"source_candidate": result["candidate"]}))

	def test_approve_is_idempotent(self):
		from ai_fr_hg.ai.learning import approve_candidate, create_candidate

		candidate = create_candidate(
			content="Idempotent approvals prevent duplicate learned records.",
			user="Administrator",
		)
		candidate.db_set("status", "Validated")
		with patch("ai_fr_hg.ai.engine.run_embedding", return_value=[]):
			first = approve_candidate(candidate.name)
			second = approve_candidate(candidate.name)

		self.assertEqual(first["promoted_name"], second["promoted_name"])
		self.assertEqual(
			frappe.db.count("AI Memory", {"source_candidate": candidate.name}),
			1,
		)

	def test_document_candidate_promotes_to_fact_memory(self):
		from ai_fr_hg.ai.learning import approve_candidate, create_candidate

		candidate = create_candidate(
			content="The document establishes a quarterly calibration schedule.",
			candidate_type="Document",
			source_type="Document",
			source_reference_doctype="AI Document",
			source_reference_name=self.make_document("Learning Source", "Quarterly calibration.").name,
			user="Administrator",
		)
		candidate.db_set("status", "Validated")
		with patch("ai_fr_hg.ai.engine.run_embedding", return_value=[]):
			result = approve_candidate(candidate.name)

		memory = frappe.get_doc("AI Memory", result["promoted_name"])
		self.assertEqual(memory.memory_type, "Fact")
		self.assertEqual(memory.source_type, "Document")

	def test_feedback_updates_recalled_memory_counters_once(self):
		from ai_fr_hg.ai.learning import record_feedback

		memory_name = self.make_memory("Always include an owner in action items.")
		conversation = frappe.get_doc(
			{
				"doctype": "AI Conversation",
				"title": "Feedback Counters",
				"user": "Administrator",
				"status": "Active",
			}
		).insert(ignore_permissions=True)
		message = frappe.get_doc(
			{
				"doctype": "AI Message",
				"conversation": conversation.name,
				"role": "Assistant",
				"content": "An answer shaped by memory.",
				"sequence": 1,
				"status": "Completed",
				"user": "Administrator",
				"learned_context": frappe.as_json({"memories": [memory_name], "skills": []}),
			}
		).insert(ignore_permissions=True)

		record_feedback(message.name, "Positive")
		record_feedback(message.name, "Positive")
		self.assertEqual(frappe.db.get_value("AI Memory", memory_name, "helpful_count"), 1)

		record_feedback(message.name, "Negative", correction="Always name an owner for each action item.")
		counts = frappe.db.get_value(
			"AI Memory",
			memory_name,
			["helpful_count", "not_helpful_count"],
			as_dict=True,
		)
		self.assertEqual(counts.helpful_count, 0)
		self.assertEqual(counts.not_helpful_count, 1)

	def test_build_system_prompt_includes_memory_block(self):
		from ai_fr_hg.ai.agent import build_system_prompt
		from ai_fr_hg.ai.learning_utils import build_memory_block

		agent = frappe.get_doc(
			{
				"doctype": "AI Agent",
				"agent_name": "Learning Prompt Agent",
				"enabled": 1,
				"model": self.chat_model.name,
				"use_knowledge": 0,
				"system_prompt": "You are a learning test assistant.",
			}
		).insert(ignore_permissions=True)

		memory = build_memory_block(
			[{"name": "m", "content": "Always greet customers by name.", "memory_type": "Instruction"}]
		)
		prompt = build_system_prompt(agent, memory=memory)
		self.assertIn("Always greet customers by name.", prompt)
