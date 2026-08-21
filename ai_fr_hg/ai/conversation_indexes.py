# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""CHAT-02 database guarantees for message sequencing.

This module is the single owner of the ``(conversation, sequence)`` uniqueness
constraint. Two callers use it and neither carries a copy:

* ``AI Message.on_doctype_update`` — Frappe invokes this for every DocType on
  ``bench migrate`` **and** on a fresh install, which is what makes the
  constraint a property of the schema.
* ``patches/v0_0_17_conversation_turn_identity`` — so an existing site adopts it
  during the migration that introduced it.

Defining the index only in that patch was the original defect. Frappe marks
historical patches as already-applied when a site is installed fresh, so no new
site ever ran it: every fresh install, including CI, had no database-level
uniqueness backstop while the gap register recorded CHAT-02 as closed.

Imports are deliberately limited to ``frappe`` so the migration behaviour can be
executed against the fake-database harness in ``tests/test_patch_regressions``
without a bench.
"""

from __future__ import annotations

import frappe

UNIQUE_SEQUENCE_INDEX = "unique_conversation_sequence"
TURN_ID_INDEX = "turn_id_index"
MESSAGE_TABLE = "tabAI Message"


def index_exists(name: str) -> bool:
	try:
		return bool(frappe.db.sql(f"SHOW INDEX FROM `{MESSAGE_TABLE}` WHERE Key_name=%s", (name,)))
	except Exception:
		return False


def renumber_duplicate_sequences() -> int:
	"""Give every message in a conversation a distinct, positive sequence.

	Returns the number of conversations repaired. Idempotent: a conversation
	whose sequences are already unique and positive is left untouched.
	"""
	repaired = 0
	for (conversation,) in frappe.db.sql("select name from `tabAI Conversation`"):
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
		repaired += 1
	return repaired


def ensure_sequence_constraints() -> None:
	"""Repair sequence data if needed, then create the indexes."""
	if getattr(frappe.db, "db_type", None) == "postgres":
		return  # ADR-001: MariaDB is the supported engine.

	if not index_exists(UNIQUE_SEQUENCE_INDEX):
		renumber_duplicate_sequences()
		try:
			frappe.db.sql(
				f"ALTER TABLE `{MESSAGE_TABLE}` "
				f"ADD UNIQUE INDEX `{UNIQUE_SEQUENCE_INDEX}` (`conversation`, `sequence`)"
			)
		except Exception as exc:
			# Never downgrade this to a log line. A previous version did, and a
			# real site ran without the backstop while reporting green.
			duplicates = frappe.db.sql(
				"""
				select conversation, sequence, count(*)
				from `tabAI Message`
				where conversation is not null
				group by conversation, sequence
				having count(*) > 1
				limit 20
				"""
			)
			raise RuntimeError(
				"Could not create the unique (conversation, sequence) index on `tabAI Message`. "
				"Message ordering has no database-level guarantee until this is resolved. "
				f"Remaining duplicate groups: {duplicates or 'none found'}."
			) from exc

	if not index_exists(TURN_ID_INDEX):
		try:
			frappe.db.sql(f"ALTER TABLE `{MESSAGE_TABLE}` ADD INDEX `{TURN_ID_INDEX}` (`turn_id`)")
		except Exception:
			# A missing secondary index costs lookup speed, not correctness.
			frappe.log_error(title="AI conversation turn_id index skipped", message=frappe.get_traceback())
