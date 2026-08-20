# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""CHAT-02: unique (conversation, sequence) and a turn_id index.

Existing sites may already have duplicate sequences from the pre-lock
allocator. Those rows are renumbered in creation order before the unique
index is added. Identifiers are literals, never caller input.
"""

from __future__ import annotations

import frappe

UNIQUE_NAME = "unique_conversation_sequence"
TURN_INDEX = "turn_id_index"


def _index_exists(name: str) -> bool:
	try:
		rows = frappe.db.sql("SHOW INDEX FROM `tabAI Message` WHERE Key_name=%s", (name,))
		return bool(rows)
	except Exception:
		return False


def _renumber_duplicate_sequences() -> None:
	conversations = frappe.db.sql("select name from `tabAI Conversation`")
	for (conversation,) in conversations:
		rows = frappe.db.sql(
			"""
			select name, sequence
			from `tabAI Message`
			where conversation = %s
			order by coalesce(sequence, 0) asc, creation asc, name asc
			""",
			(conversation,),
		)
		seen: set[int] = set()
		needs_renumber = False
		for _name, sequence in rows:
			value = int(sequence or 0)
			if value <= 0 or value in seen:
				needs_renumber = True
				break
			seen.add(value)
		if not needs_renumber:
			continue
		for index, (name, _sequence) in enumerate(rows, start=1):
			frappe.db.set_value("AI Message", name, "sequence", index, update_modified=False)


def execute() -> None:
	if getattr(frappe.db, "db_type", None) == "postgres":
		return

	_renumber_duplicate_sequences()

	if not _index_exists(UNIQUE_NAME):
		try:
			frappe.db.sql(
				"ALTER TABLE `tabAI Message` "
				"ADD UNIQUE INDEX `unique_conversation_sequence` (`conversation`, `sequence`)"
			)
		except Exception:
			frappe.log_error(
				title="AI conversation unique sequence index skipped", message=frappe.get_traceback()
			)

	if not _index_exists(TURN_INDEX):
		try:
			frappe.db.sql("ALTER TABLE `tabAI Message` ADD INDEX `turn_id_index` (`turn_id`)")
		except Exception:
			frappe.log_error(title="AI conversation turn_id index skipped", message=frappe.get_traceback())
