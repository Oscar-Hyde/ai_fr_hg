# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""CHAT-02: seed the message sequence counter on existing conversations.

`allocate_sequence` increments `AI Conversation.message_sequence_counter` with
a bare single-row UPDATE - no subquery, because a subquery there is a
consistent read (raising `1020 Record has changed since last read`) and takes
locks on `tabAI Message` that deadlock against concurrent inserts.

Seeding therefore has to happen once, here, rather than on every allocation.
Conversations created after this patch start at 0 with no messages, so they
need nothing.

Idempotent: only rows whose counter is still below their real maximum are
touched.
"""

from __future__ import annotations

import frappe


def execute() -> None:
	if not frappe.db.has_column("AI Conversation", "message_sequence_counter"):
		return

	frappe.db.sql(
		"""
		update `tabAI Conversation` as c
		set c.message_sequence_counter = coalesce(
			(select max(m.sequence) from `tabAI Message` as m where m.conversation = c.name), 0
		)
		where coalesce(c.message_sequence_counter, 0) < coalesce(
			(select max(m.sequence) from `tabAI Message` as m where m.conversation = c.name), 0
		)
		"""
	)
