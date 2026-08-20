# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Static regressions for Phase 3 conversation contracts."""

from pathlib import Path
from unittest import TestCase

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "ai_fr_hg"


class TestPhase3SourceContracts(TestCase):
	def test_history_owner_does_not_use_deprecated_pagination(self):
		source = (APP / "ai/conversation.py").read_text()
		self.assertNotIn("limit_page_length", source)
		self.assertNotIn("limit_start", source)
		self.assertIn("window_latest_messages", source)
		self.assertIn("allocate_sequence", source)
		self.assertIn("for update", source.lower())

	def test_chat_api_is_thin_and_paginated(self):
		source = (APP / "api/chat.py").read_text()
		self.assertNotIn("limit_page_length", source)
		self.assertNotIn("limit_start", source)
		self.assertIn("from ai_fr_hg.ai.conversation import", source)
		self.assertIn("cancel_turn", source)
		self.assertIn("get_turn_status", source)

	def test_assistant_uses_file_identity_and_route_state(self):
		source = (APP / "ai_core/page/ai_assistant/ai_assistant.js").read_text()
		self.assertIn("file_record: file.name", source)
		self.assertIn("parseAssistantRoute", source)
		self.assertIn("cancel_turn", source)
		self.assertIn("turn_id", source)
		self.assertIn("pending_documents", source)
		self.assertIn("Improve this answer", source)

	def test_turn_id_field_exists_on_message(self):
		import json

		meta = json.loads((APP / "ai_conversation/doctype/ai_message/ai_message.json").read_text())
		names = [field["fieldname"] for field in meta["fields"]]
		self.assertIn("turn_id", names)
		status = next(field for field in meta["fields"] if field["fieldname"] == "status")
		self.assertIn("Cancelled", status["options"])

	def test_conversation_patch_is_registered(self):
		patches = (APP / "patches.txt").read_text()
		self.assertIn("ai_fr_hg.patches.v0_0_17_conversation_turn_identity", patches)
		self.assertEqual(patches.count("ai_fr_hg.patches.v0_0_17_conversation_turn_identity"), 1)
