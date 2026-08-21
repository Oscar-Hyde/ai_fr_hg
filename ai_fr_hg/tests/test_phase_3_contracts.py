# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Structural regressions for Phase 3 conversation contracts.

Scope is deliberately narrow: DocType schema and patch registration, which are
declarative facts a source read can legitimately establish.

The behaviour of the history window is NOT asserted here. Two tests in this
module previously read `ai/conversation.py` and `ai_assistant.js` as text and
asserted that identifiers appeared in them, which passes whether or not the
code works. They were removed in the CLOSED-claim re-audit and replaced by
`TestConversationHistoryBehaviour` in `test_part2_behaviour.py`, which runs
`get_conversation_history` against a bench, and by the `history-selects-oldest`
and `history-replays-in-flight-turns` mutations that prove those tests fail
when the behaviour regresses.
"""

from pathlib import Path
from unittest import TestCase

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "ai_fr_hg"


class TestPhase3SourceContracts(TestCase):
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
