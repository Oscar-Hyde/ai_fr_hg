# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Pure CHAT-01/CHAT-03 helpers — no database, no Frappe runtime."""

from unittest import TestCase

from ai_fr_hg.ai.conversation_utils import (
	HISTORY_LIMIT,
	cancel_cache_key,
	looks_like_conversation_name,
	window_latest_messages,
)


def _row(sequence, role, content=None):
	return {"sequence": sequence, "role": role, "content": content or f"{role}-{sequence}"}


class TestLatestHistoryWindow(TestCase):
	def test_empty_and_non_positive_limit(self):
		self.assertEqual(window_latest_messages([], 20), [])
		self.assertEqual(window_latest_messages([_row(1, "User")], 0), [])

	def test_latest_n_not_oldest(self):
		# Newest first, as the SQL query returns them.
		rows = [_row(i, "User", f"msg-{i}") for i in range(100, 0, -1)]
		window = window_latest_messages(rows, 20)
		self.assertEqual(len(window), 20)
		self.assertEqual(window[0]["content"], "msg-81")
		self.assertEqual(window[-1]["content"], "msg-100")
		self.assertNotIn("msg-1", [row["content"] for row in window])

	def test_tool_group_is_not_split(self):
		# Newest first: Tool(5), Tool(4), Assistant(3), User(2), User(1)
		rows = [
			_row(5, "Tool"),
			_row(4, "Tool"),
			_row(3, "Assistant"),
			_row(2, "User"),
			_row(1, "User"),
		]
		window = window_latest_messages(rows, 2)
		roles = [row["role"] for row in window]
		self.assertEqual(roles[0], "Assistant")
		self.assertIn("Tool", roles)
		self.assertGreaterEqual(len(window), 3)

	def test_default_limit_constant(self):
		self.assertEqual(HISTORY_LIMIT, 20)


class TestConversationName(TestCase):
	def test_accepts_series_names(self):
		self.assertTrue(looks_like_conversation_name("AICONV-2026-00001"))

	def test_rejects_paths_and_blank(self):
		self.assertFalse(looks_like_conversation_name(""))
		self.assertFalse(looks_like_conversation_name("ai-assistant/extra"))
		self.assertFalse(looks_like_conversation_name("has space"))

	def test_cancel_cache_key_uses_turn_id(self):
		self.assertEqual(cancel_cache_key("abc"), "ai_fr_hg:turn_cancel:abc")
