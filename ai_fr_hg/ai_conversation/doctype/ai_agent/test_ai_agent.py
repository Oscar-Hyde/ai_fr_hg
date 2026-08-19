# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Frappe integration coverage for this DocType and its canonical domain services."""

from unittest.mock import patch

import frappe

from ai_fr_hg.ai.providers.base import CompletionResult
from ai_fr_hg.tests.integration_test_case import AIPlatformTestCase


class TestAgentRuntime(AIPlatformTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		if not frappe.db.exists("AI Agent", "Test Agent"):
			frappe.get_doc(
				{
					"doctype": "AI Agent",
					"agent_name": "Test Agent",
					"enabled": 1,
					"model": cls.chat_model.name,
					"use_knowledge": 0,
					"use_tools": 0,
					"temperature": 0.1,
					"system_prompt": "You are a test assistant.",
				}
			).insert(ignore_permissions=True)

	def test_conversation_is_created(self):
		from ai_fr_hg.ai.agent import create_conversation

		conversation = create_conversation(agent="Test Agent", title="Test Conversation")
		self.assertTrue(frappe.db.exists("AI Conversation", conversation.name))
		self.assertEqual(conversation.agent, "Test Agent")

	def test_agent_turn_saves_messages(self):
		from ai_fr_hg.ai.agent import create_conversation, run_agent_turn

		conversation = create_conversation(agent="Test Agent")
		with patch("ai_fr_hg.ai.agent.run_chat") as mock_chat:
			mock_chat.return_value = CompletionResult(
				content="The answer is 42.", total_tokens=20, duration_ms=15, model="stub"
			)
			response = run_agent_turn(
				"What is the answer?", agent="Test Agent", conversation=conversation.name
			)

		self.assertEqual(response["answer"], "The answer is 42.")

		messages = frappe.get_all(
			"AI Message",
			filters={"conversation": conversation.name},
			fields=["role", "content"],
			order_by="sequence asc",
		)
		roles = [message.role for message in messages]
		self.assertIn("User", roles)
		self.assertIn("Assistant", roles)

	def test_exhausted_budget_saves_an_answer_instead_of_hanging(self):
		"""A blown budget must end the turn, not leave the proxy to time out."""
		from ai_fr_hg.ai.agent import TIMED_OUT_ANSWER, create_conversation, run_agent_turn
		from ai_fr_hg.ai.deadline import turn_budget
		from ai_fr_hg.ai.exceptions import DeadlineExceededError

		conversation = create_conversation(agent="Test Agent")

		with patch("ai_fr_hg.ai.agent.run_chat", side_effect=DeadlineExceededError("out of time")):
			with turn_budget(60):
				response = run_agent_turn(
					"Summarise the document I just uploaded.",
					agent="Test Agent",
					conversation=conversation.name,
				)

		# The turn returns normally, flagged, with a usable explanation.
		self.assertTrue(response["timed_out"])
		self.assertEqual(response["answer"], TIMED_OUT_ANSWER)

		# And the reply is persisted, so the thread stays coherent on reload.
		saved = frappe.get_all(
			"AI Message",
			filters={"conversation": conversation.name, "role": "Assistant"},
			fields=["content", "status"],
			order_by="sequence desc",
			limit=1,
		)
		self.assertEqual(saved[0].status, "Failed")
		self.assertIn("ran out of time", saved[0].content)

	def test_provider_timeout_saves_friendly_answer(self):
		"""A provider read timeout must not surface as a 417; it becomes a saved
		explanation so the conversation stays coherent."""
		from ai_fr_hg.ai.agent import PROVIDER_TIMEOUT_ANSWER, create_conversation, run_agent_turn
		from ai_fr_hg.ai.deadline import turn_budget
		from ai_fr_hg.ai.exceptions import ProviderTimeoutError

		conversation = create_conversation(agent="Test Agent")

		with patch("ai_fr_hg.ai.agent.run_chat", side_effect=ProviderTimeoutError("slow model")):
			with turn_budget(60):
				response = run_agent_turn("Question?", agent="Test Agent", conversation=conversation.name)

		self.assertTrue(response["timed_out"])
		self.assertEqual(response["answer"], PROVIDER_TIMEOUT_ANSWER)

		saved = frappe.get_all(
			"AI Message",
			filters={"conversation": conversation.name, "role": "Assistant"},
			fields=["status"],
			order_by="sequence desc",
			limit=1,
		)
		self.assertEqual(saved[0].status, "Failed")

	def test_provider_oom_saves_friendly_answer(self):
		"""An Ollama memory error must not surface as HTTP 417."""
		from ai_fr_hg.ai.agent import PROVIDER_OOM_ANSWER, create_conversation, run_agent_turn
		from ai_fr_hg.ai.exceptions import ProviderError

		conversation = create_conversation(agent="Test Agent")
		exc = ProviderError(
			'Provider Local Ollama returned HTTP 500: {"error":"model requires more system memory (10.8 GiB) than is available (9.8 GiB)"}'
		)
		with patch("ai_fr_hg.ai.agent.run_chat", side_effect=exc):
			response = run_agent_turn("Question?", agent="Test Agent", conversation=conversation.name)

		self.assertTrue(response["timed_out"])
		self.assertEqual(response["answer"], PROVIDER_OOM_ANSWER)
		saved = frappe.get_all(
			"AI Message",
			filters={"conversation": conversation.name, "role": "Assistant"},
			fields=["status"],
			order_by="sequence desc",
			limit=1,
		)
		self.assertEqual(saved[0].status, "Failed")

	def test_tools_are_withheld_when_the_budget_cannot_fund_a_follow_up(self):
		"""Near the deadline, ask for prose rather than another tool round trip."""
		from ai_fr_hg.ai.agent import run_agent_turn
		from ai_fr_hg.ai.deadline import turn_budget

		agent = frappe.get_doc("AI Agent", "Test Agent")
		agent.use_tools = 1
		agent.flags.ignore_permissions = True

		captured = {}

		def capture(messages, **kwargs):
			captured["tools"] = kwargs.get("tools")
			return CompletionResult(content="Final answer.", total_tokens=5)

		with patch("ai_fr_hg.ai.agent.run_chat", side_effect=capture):
			# A 12s budget cannot fund a tool call plus the call that would
			# interpret its result, so no tools should be offered.
			with turn_budget(12):
				run_agent_turn("Question?", agent="Test Agent", save_messages=False)

		self.assertIsNone(captured["tools"])

	def test_generous_budget_still_offers_tools(self):
		"""The guard must not disable tool calling under normal conditions."""
		from ai_fr_hg.ai.agent import run_agent_turn
		from ai_fr_hg.ai.deadline import turn_budget

		captured = {}

		def capture(messages, **kwargs):
			captured["tools"] = kwargs.get("tools")
			return CompletionResult(content="Final answer.", total_tokens=5)

		agent = frappe.get_doc("AI Agent", "Test Agent")
		if not agent.use_tools:
			self.skipTest("Test Agent has no tools configured.")

		with patch("ai_fr_hg.ai.agent.run_chat", side_effect=capture):
			with turn_budget(600):
				run_agent_turn("Question?", agent="Test Agent", save_messages=False)

		self.assertIsNotNone(captured["tools"])

	def test_system_prompt_includes_context(self):
		from ai_fr_hg.ai.agent import build_system_prompt

		agent = frappe.get_doc("AI Agent", "Test Agent")
		prompt = build_system_prompt(agent, context="[1] Some retrieved fact.")
		self.assertIn("CONTEXT", prompt)
		self.assertIn("Some retrieved fact", prompt)

	def test_strict_grounding_adds_instruction(self):
		from ai_fr_hg.ai.agent import GROUNDING_INSTRUCTIONS, build_system_prompt

		agent = frappe.get_doc("AI Agent", "Test Agent")
		agent.strict_grounding = 1
		prompt = build_system_prompt(agent, context="[1] Fact.")
		self.assertIn(GROUNDING_INSTRUCTIONS, prompt)


class TestAgentAPI(AIPlatformTestCase):
	def test_get_chat_context(self):
		from ai_fr_hg.api.chat import get_chat_context

		context = get_chat_context()
		self.assertIn("agents", context)
		self.assertIn("models", context)
		self.assertIn("knowledge_bases", context)
