# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Add retrieval indexes used by complete candidate evaluation.

Frappe has no native FULLTEXT or vector-index field type. MariaDB FULLTEXT on
``AI Document Chunk.content`` is the database-native keyword ranking path;
the composite ``(knowledge_base, embedding_model, embedding_dimensions)``
index supports mixed-model scans. Both statements are idempotent.
"""

from __future__ import annotations

import frappe

CONTENT_FTS = "content_fts"
KB_EMBED_IDX = "kb_embed_model_idx"


def _index_exists(table: str, name: str) -> bool:
	try:
		rows = frappe.db.sql("SHOW INDEX FROM `{0}` WHERE Key_name=%s".format(table), (name,))
		return bool(rows)
	except Exception:
		return False


def execute() -> None:
	if getattr(frappe.db, "db_type", None) == "postgres":
		return

	table = "tabAI Document Chunk"
	if not _index_exists(table, CONTENT_FTS):
		try:
			frappe.db.sql(
				f"ALTER TABLE `{table}` ADD FULLTEXT INDEX `{CONTENT_FTS}` (`content`)"
			)  # nosemgrep
		except Exception:
			frappe.log_error(title="AI retrieval FULLTEXT index skipped", message=frappe.get_traceback())

	if not _index_exists(table, KB_EMBED_IDX):
		try:
			frappe.db.sql(  # nosemgrep
				f"ALTER TABLE `{table}` ADD INDEX `{KB_EMBED_IDX}` "
				"(`knowledge_base`, `embedding_model`, `embedding_dimensions`)"
			)
		except Exception:
			frappe.log_error(title="AI retrieval embedding index skipped", message=frappe.get_traceback())
