# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, flt


class AIKnowledgeBase(Document):
	_DOCTYPE_NAME = "AI Knowledge Base"

	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		from ai_fr_hg.ai_knowledge.doctype.ai_knowledge_base_role.ai_knowledge_base_role import (
			AIKnowledgeBaseRole,
		)

		chunk_count: DF.Int
		chunk_overlap: DF.Int
		chunk_size: DF.Int
		description: DF.SmallText | None
		document_count: DF.Int
		embedded_chunk_count: DF.Int
		embedding_model: DF.Link | None
		enabled: DF.Check
		index_status: DF.Literal["Idle", "Indexing", "Stale", "Error"]
		is_public: DF.Check
		knowledge_base_name: DF.Data
		last_indexed_on: DF.Datetime | None
		restrict_to_roles: DF.Table[AIKnowledgeBaseRole]
		similarity_threshold: DF.Float
		top_k: DF.Int
		total_characters: DF.LongInt
	# end: auto-generated types

	def validate(self):
		self.validate_chunking()
		self.validate_embedding_model()

	def validate_chunking(self):
		if cint(self.chunk_size) < 100:
			frappe.throw(_("Chunk Size must be at least 100 characters."))
		if cint(self.chunk_overlap) >= cint(self.chunk_size):
			frappe.throw(_("Chunk Overlap must be smaller than Chunk Size."))
		if not 0 <= flt(self.similarity_threshold) <= 1:
			frappe.throw(_("Similarity Threshold must be between 0 and 1."))

	def validate_embedding_model(self):
		if not self.embedding_model:
			return
		model_type = frappe.db.get_value("AI Model", self.embedding_model, "model_type")
		if model_type != "Embedding":
			frappe.throw(_("{0} is not an embedding model.").format(self.embedding_model))

		if self.is_new():
			return
		before = self.get_doc_before_save()
		if before and before.embedding_model and before.embedding_model != self.embedding_model:
			self.flags.embedding_model_changed = True

	def on_update(self):
		if self.flags.get("embedding_model_changed"):
			self.db_set("index_status", "Stale", update_modified=False)
			frappe.msgprint(
				_(
					"The embedding model changed. Existing vectors are no longer comparable - "
					"re-index this knowledge base."
				),
				title=_("Re-indexing Required"),
				indicator="orange",
			)

	def on_trash(self):
		"""Remove the chunks and documents belonging to this knowledge base."""
		frappe.db.delete("AI Document Chunk", {"knowledge_base": self.name})
		for document in frappe.get_all("AI Document", filters={"knowledge_base": self.name}, pluck="name"):
			frappe.delete_doc("AI Document", document, force=True, ignore_permissions=True)

	@frappe.whitelist()
	def reindex(self) -> dict:
		"""Queue every document in this knowledge base for reprocessing."""
		from ai_fr_hg.api.knowledge import reindex_knowledge_base

		return reindex_knowledge_base(self.name)

	@frappe.whitelist()
	def refresh_stats(self) -> dict:
		"""Recompute the document and chunk counters."""
		from ai_fr_hg.ai.knowledge import update_knowledge_base_stats

		update_knowledge_base_stats(self.name)
		self.reload()
		return {
			"documents": self.document_count,
			"chunks": self.chunk_count,
			"embedded": self.embedded_chunk_count,
		}
