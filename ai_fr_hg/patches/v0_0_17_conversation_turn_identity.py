# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""CHAT-02: unique (conversation, sequence) and a turn_id index.

The constraints are owned by `ai.conversation_indexes`, which Frappe also
invokes through `AI Message.on_doctype_update` on every migrate and on a fresh
install. This patch exists so an existing site adopts them in the migration
that introduced them; it deliberately carries no copy of the logic.
"""

from __future__ import annotations

from ai_fr_hg.ai.conversation_indexes import ensure_sequence_constraints


def execute() -> None:
	ensure_sequence_constraints()
