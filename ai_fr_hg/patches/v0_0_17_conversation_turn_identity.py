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


def _remaining_duplicates() -> list[tuple]:
	"""Conversations that still hold a duplicate or non-positive sequence."""
	return frappe.db.sql(
		"""
		select conversation, sequence, count(*) as copies
		from `tabAI Message`
		where conversation is not null
		group by conversation, sequence
		having count(*) > 1
		limit 20
		"""
	)


def execute() -> None:
	if getattr(frappe.db, "db_type", None) == "postgres":
		return

	_renumber_duplicate_sequences()

	if not _index_exists(UNIQUE_NAME):
		# CHAT-02: this index is the database-level backstop for message
		# sequence uniqueness. A previous version of this patch swallowed the
		# failure into `frappe.log_error`, and a real site was later found
		# running *without* the index and without anyone knowing — while the
		# test that was supposed to catch it skipped itself because the index
		# was missing. A backstop that can vanish silently is not a backstop.
		#
		# If the ALTER cannot be applied the migration now fails loudly with
		# the offending rows named, because the allocator's correctness
		# guarantee is weaker without it.
		try:
			frappe.db.sql(
				"ALTER TABLE `tabAI Message` "
				"ADD UNIQUE INDEX `unique_conversation_sequence` (`conversation`, `sequence`)"
			)
		except Exception as exc:
			duplicates = _remaining_duplicates()
			raise RuntimeError(
				"Could not create the unique (conversation, sequence) index on `tabAI Message`. "
				"Message ordering has no database-level guarantee until this is resolved. "
				f"Remaining duplicate (conversation, sequence) groups: {duplicates or 'none found'}."
			) from exc

	if not _index_exists(TURN_INDEX):
		try:
			frappe.db.sql("ALTER TABLE `tabAI Message` ADD INDEX `turn_id_index` (`turn_id`)")
		except Exception:
			# A missing secondary index costs lookup speed, not correctness.
			frappe.log_error(title="AI conversation turn_id index skipped", message=frappe.get_traceback())
