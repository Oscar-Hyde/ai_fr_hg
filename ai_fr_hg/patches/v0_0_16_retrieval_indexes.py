# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Add retrieval indexes used by complete candidate evaluation.

Frappe has no native FULLTEXT or vector-index field type. MariaDB FULLTEXT on
``AI Document Chunk.content`` is the database-native keyword ranking path;
the composite ``(knowledge_base, embedding_model, embedding_dimensions)``
index supports mixed-model scans. Both statements are idempotent. Table and
index identifiers are literals, never caller input.
"""

from __future__ import annotations

import frappe

CONTENT_FTS = "content_fts"
KB_EMBED_IDX = "kb_embed_model_idx"


def _index_exists(name: str) -> bool:
	try:
		rows = frappe.db.sql("SHOW INDEX FROM `tabAI Document Chunk` WHERE Key_name=%s", (name,))
		return bool(rows)
	except Exception:
		return False


def execute() -> None:
	if getattr(frappe.db, "db_type", None) == "postgres":
		return

	if not _index_exists(CONTENT_FTS):
		try:
			frappe.db.sql("ALTER TABLE `tabAI Document Chunk` ADD FULLTEXT INDEX `content_fts` (`content`)")
		except Exception:
			frappe.log_error(title="AI retrieval FULLTEXT index skipped", message=frappe.get_traceback())

	if not _index_exists(KB_EMBED_IDX):
		try:
			frappe.db.sql(
				"ALTER TABLE `tabAI Document Chunk` ADD INDEX `kb_embed_model_idx` "
				"(`knowledge_base`, `embedding_model`, `embedding_dimensions`)"
			)
		except Exception:
			frappe.log_error(title="AI retrieval embedding index skipped", message=frappe.get_traceback())
