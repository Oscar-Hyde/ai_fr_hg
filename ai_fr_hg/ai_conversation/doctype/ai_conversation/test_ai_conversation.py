# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Phase 3 conversation contracts: history, sequencing, config, actions, cancel."""

from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import frappe

from ai_fr_hg.ai.providers.base import CompletionResult
from ai_fr_hg.tests.integration_test_case import AIPlatformTestCase


class TestConversationHistory(AIPlatformTestCase):
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

	def _conversation(self):
		from ai_fr_hg.ai.agent import create_conversation

		return create_conversation(agent="Test Agent", title="History Test", knowledge_bases=[])

	def test_latest_history_uses_newest_messages(self):
		from ai_fr_hg.ai.agent import get_conversation_history, save_message

		conversation = self._conversation()
		for index in range(1, 101):
			save_message(conversation.name, role="User", content=f"msg-{index}", turn_id=f"t-{index}")

		history = get_conversation_history(conversation.name, limit=20)
		contents = [row.content for row in history]
		self.assertEqual(contents[0], "msg-81")
		self.assertEqual(contents[-1], "msg-100")
		self.assertNotIn("msg-1", contents)

	def test_sequences_are_unique_and_monotonic(self):
		from ai_fr_hg.ai.agent import save_message

		conversation = self._conversation()
		names = []
		for index in range(8):
			names.append(save_message(conversation.name, role="User", content=f"n-{index}").name)
		rows = frappe.get_all(
			"AI Message",
			filters={"conversation": conversation.name, "name": ["in", names]},
			fields=["name", "sequence"],
			order_by="sequence asc",
		)
		sequences = [row.sequence for row in rows]
		self.assertEqual(sequences, sorted(sequences))
		self.assertEqual(len(sequences), len(set(sequences)))

	def test_100_concurrent_sends_preserve_order_and_uniqueness(self):
		"""Exercise CHAT-02 with real worker connections, not sequential calls.

		Each worker opens its own Frappe connection and commits one message. The
		conversation-row FOR UPDATE allocator must serialize the 100 competing
		transactions, while the unique (conversation, sequence) index remains the
		database-level backstop.
		"""
		from ai_fr_hg.ai.agent import save_message

		conversation = self._conversation()
		frappe.db.commit()  # nosemgrep: required to make the test parent visible to worker transactions
		site = frappe.local.site

		def send(index):
			frappe.init(site=site)
			frappe.connect()
			frappe.set_user("Administrator")
			try:
				message = save_message(
					conversation.name,
					role="User",
					content=f"concurrent-{index}",
					turn_id=f"concurrent-turn-{index}",
				)
				frappe.db.commit()  # nosemgrep: each worker must commit its competing transaction
				return message.name
			finally:
				frappe.destroy()

		with ThreadPoolExecutor(max_workers=100) as pool:
			names = list(pool.map(send, range(100)))

		rows = frappe.get_all(
			"AI Message",
			filters={"conversation": conversation.name, "name": ["in", names]},
			fields=["sequence"],
			order_by="sequence asc",
		)
		sequences = [row.sequence for row in rows]
		self.assertEqual(len(names), 100)
		self.assertEqual(len(rows), 100)
		self.assertEqual(sequences, list(range(1, 101)))
		self.assertEqual(len(sequences), len(set(sequences)))

	def test_duplicate_sequence_is_rejected_when_index_exists(self):
		from ai_fr_hg.ai.agent import save_message

		conversation = self._conversation()
		first = save_message(conversation.name, role="User", content="one")
		indexes = frappe.db.sql(
			"SHOW INDEX FROM `tabAI Message` WHERE Key_name=%s",
			("unique_conversation_sequence",),
		)
		if not indexes:
			self.skipTest("unique_conversation_sequence index is not present on this site")
		duplicate = frappe.get_doc(
			{
				"doctype": "AI Message",
				"conversation": conversation.name,
				"role": "User",
				"content": "clash",
				"sequence": first.sequence,
				"status": "Completed",
			}
		)
		duplicate.flags.sequence_allocated = True
		with self.assertRaises(Exception):
			duplicate.insert(ignore_permissions=True)


class TestConversationActions(AIPlatformTestCase):
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
				}
			).insert(ignore_permissions=True)

	def _conversation(self):
		from ai_fr_hg.ai.agent import create_conversation

		return create_conversation(agent="Test Agent", title="Actions", knowledge_bases=[])

	def test_rename_pin_archive_restore_export(self):
		from ai_fr_hg.ai.conversation import (
			archive_conversation,
			export_conversation,
			pin_conversation,
			rename_conversation,
			restore_conversation,
		)

		conversation = self._conversation()
		self.assertEqual(rename_conversation(conversation.name, "Renamed")["title"], "Renamed")
		self.assertEqual(frappe.db.get_value("AI Conversation", conversation.name, "title"), "Renamed")
		self.assertEqual(pin_conversation(conversation.name, True)["pinned"], 1)
		self.assertEqual(archive_conversation(conversation.name)["status"], "archived")
		self.assertEqual(frappe.db.get_value("AI Conversation", conversation.name, "status"), "Archived")
		self.assertEqual(restore_conversation(conversation.name)["status"], "restored")
		exported = export_conversation(conversation.name)
		self.assertEqual(exported["conversation"]["name"], conversation.name)
		self.assertIn("messages", exported)

	def test_list_pagination_uses_limit(self):
		from ai_fr_hg.ai.conversation import list_conversations

		payload = list_conversations(limit=2, offset=0)
		self.assertLessEqual(len(payload["conversations"]), 2)
		self.assertEqual(payload["limit"], 2)
		self.assertEqual(payload["offset"], 0)

	def test_other_user_cannot_rename(self):
		from ai_fr_hg.ai.conversation import rename_conversation

		conversation = self._conversation()
		other = "Guest"
		if frappe.session.user == other:
			self.skipTest("already Guest")
		try:
			frappe.set_user(other)
			with self.assertRaises(frappe.PermissionError):
				rename_conversation(conversation.name, "Hijack")
		finally:
			frappe.set_user("Administrator")


class TestConversationTurnCancel(AIPlatformTestCase):
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
				}
			).insert(ignore_permissions=True)

	def test_cancel_turn_by_id_is_idempotent(self):
		from ai_fr_hg.ai.agent import create_conversation, save_message
		from ai_fr_hg.ai.conversation import cancel_turn, get_turn_status, is_turn_cancelled

		conversation = create_conversation(agent="Test Agent", knowledge_bases=[])
		save_message(
			conversation.name,
			role="Assistant",
			content="",
			status="Streaming",
			turn_id="turn-alpha",
		)
		first = cancel_turn(conversation.name, "turn-alpha")
		second = cancel_turn(conversation.name, "turn-alpha")
		self.assertEqual(first["status"], "cancelled")
		self.assertEqual(second["status"], "cancelled")
		self.assertTrue(is_turn_cancelled("turn-alpha"))
		status = get_turn_status(conversation.name, "turn-alpha")
		self.assertEqual(status["status"], "Cancelled")

	def test_cancel_does_not_touch_another_turn(self):
		from ai_fr_hg.ai.agent import create_conversation, save_message
		from ai_fr_hg.ai.conversation import cancel_turn

		conversation = create_conversation(agent="Test Agent", knowledge_bases=[])
		keep = save_message(
			conversation.name, role="Assistant", content="", status="Streaming", turn_id="keep-me"
		)
		save_message(conversation.name, role="Assistant", content="", status="Streaming", turn_id="drop-me")
		cancel_turn(conversation.name, "drop-me")
		self.assertEqual(frappe.db.get_value("AI Message", keep.name, "status"), "Streaming")

	def test_run_agent_turn_honours_cancel_before_model(self):
		from ai_fr_hg.ai.agent import create_conversation, run_agent_turn
		from ai_fr_hg.ai.conversation import request_cancel

		conversation = create_conversation(agent="Test Agent", knowledge_bases=[])
		request_cancel("turn-stop")
		with patch("ai_fr_hg.ai.agent.run_chat") as mock_chat:
			mock_chat.return_value = CompletionResult(content="should not run", total_tokens=1)
			try:
				result = run_agent_turn(
					"Hello?",
					agent="Test Agent",
					conversation=conversation.name,
					turn_id="turn-stop",
				)
			except Exception:
				# raise_if_cancelled before persistence is acceptable
				mock_chat.assert_not_called()
				return
		self.assertTrue(result.get("cancelled") or mock_chat.call_count == 0)


class TestChat04Fields(AIPlatformTestCase):
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
				}
			).insert(ignore_permissions=True)

	def test_focused_document_is_passed_to_retrieve(self):
		from ai_fr_hg.ai.agent import create_conversation, run_agent_turn

		document = self.make_document("Focus Doc", "The focused clause lives only in this file. " * 8)
		conversation = create_conversation(agent="Test Agent", knowledge_bases=[])
		conversation.db_set("context_document", document.name)

		captured = {}

		def fake_retrieve(*args, **kwargs):
			captured.update(kwargs)
			return []

		with (
			patch("ai_fr_hg.ai.agent.retrieve", side_effect=fake_retrieve),
			patch(
				"ai_fr_hg.ai.agent.run_chat",
				return_value=CompletionResult(content="ok", total_tokens=1),
			),
		):
			run_agent_turn("What is in the focused file?", agent="Test Agent", conversation=conversation.name)

		self.assertIn(document.name, captured.get("documents") or [])

	def test_fallback_answer_when_strict_and_empty(self):
		from ai_fr_hg.ai.agent import create_conversation, run_agent_turn

		agent = frappe.get_doc("AI Agent", "Test Agent")
		agent.db_set({"strict_grounding": 1, "use_knowledge": 1, "fallback_answer": "I do not know that."})
		conversation = create_conversation(agent="Test Agent", knowledge_bases=[])
		with (
			patch("ai_fr_hg.ai.agent.retrieve", return_value=[]),
			patch("ai_fr_hg.ai.agent.run_chat") as mock_chat,
		):
			result = run_agent_turn("Unknown topic", agent="Test Agent", conversation=conversation.name)
		self.assertTrue(result.get("fallback"))
		self.assertEqual(result["answer"], "I do not know that.")
		mock_chat.assert_not_called()
		agent.db_set({"strict_grounding": 0, "use_knowledge": 0, "fallback_answer": ""})

	def test_footnote_citation_mode_is_in_prompt(self):
		from ai_fr_hg.ai.agent import CITATION_FOOTNOTE_INSTRUCTIONS, build_system_prompt

		agent = frappe.get_doc("AI Agent", "Test Agent")
		agent.citation_mode = "Footnote"
		prompt = build_system_prompt(agent, context="[1] Fact.")
		self.assertIn(CITATION_FOOTNOTE_INSTRUCTIONS, prompt)

	def test_negative_feedback_persists_reason_and_correction(self):
		from ai_fr_hg.ai.agent import create_conversation, save_message
		from ai_fr_hg.ai.learning import record_feedback

		conversation = create_conversation(agent="Test Agent", knowledge_bases=[])
		message = save_message(conversation.name, role="Assistant", content="Wrong answer.")
		result = record_feedback(
			message.name,
			"Negative",
			reason="Incorrect Information",
			correction="The correct figure is 42.",
		)
		self.assertEqual(frappe.db.get_value("AI Message", message.name, "feedback"), "Negative")
		self.assertEqual(
			frappe.db.get_value("AI Message", message.name, "feedback_reason"), "Incorrect Information"
		)
		self.assertIn("42", frappe.db.get_value("AI Message", message.name, "feedback_comment") or "")
		self.assertTrue(result.get("candidate") or result.get("learning_status"))
