# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class AIDocument(Document):
	_DOCTYPE_NAME = "AI Document"

	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		from ai_fr_hg.ai_knowledge.doctype.ai_document_tag.ai_document_tag import AIDocumentTag

		character_count: DF.LongInt
		checksum: DF.Data | None
		chunk_count: DF.Int
		confidence: DF.Percent
		content: DF.LongText | None
		document_type: DF.Data | None
		embedded_chunk_count: DF.Int
		error_message: DF.SmallText | None
		extracted_data: DF.Code | None
		extraction_schema: DF.Link | None
		file_size: DF.LongInt
		indexed_on: DF.Datetime | None
		knowledge_base: DF.Link
		language: DF.Data | None
		metadata: DF.Code | None
		mime_type: DF.Data | None
		naming_series: DF.Literal["AIDOC-.YYYY.-"]
		page_count: DF.Int
		processing_duration_ms: DF.Int
		reader_used: DF.Data | None
		retry_count: DF.Int
		source_doctype: DF.Link | None
		source_file: DF.Attach | None
		source_name: DF.DynamicLink | None
		source_type: DF.Literal["File", "Text", "URL", "DocType Record", "Folder"]
		source_url: DF.Data | None
		status: DF.Literal[
			"Draft", "Queued", "Extracting", "Chunking", "Embedding", "Indexed", "Failed", "Archived"
		]
		summary: DF.LongText | None
		tags: DF.Table[AIDocumentTag]
		title: DF.Data
		word_count: DF.Int
	# end: auto-generated types

	def before_insert(self):
		if not self.title and self.source_file:
			self.title = self.source_file.rsplit("/", 1)[-1]

	def validate(self):
		self.validate_source()

	def validate_source(self):
		if self.source_type == "File" and not self.source_file:
			frappe.throw(_("A source file is required when Source Type is File."))
		if self.source_type == "Text" and not self.content:
			frappe.throw(_("Text content is required when Source Type is Text."))
		if self.source_type == "URL" and not self.source_url:
			frappe.throw(_("A source URL is required when Source Type is URL."))
		if self.source_type == "DocType Record" and not (self.source_doctype and self.source_name):
			frappe.throw(_("Source DocType and Source Name are required."))

	def after_insert(self):
		"""Auto-process new documents when the platform is configured to."""
		if self.status != "Queued":
			return
		if not frappe.db.get_single_value("AI Platform Settings", "auto_process_documents"):
			return

		from ai_fr_hg.ai.ingestion import enqueue_processing

		enqueue_processing(self.name)

	def on_trash(self):
		frappe.db.delete("AI Document Chunk", {"document": self.name})

	def after_delete(self):
		from ai_fr_hg.ai.knowledge import update_knowledge_base_stats

		if frappe.db.exists("AI Knowledge Base", self.knowledge_base):
			update_knowledge_base_stats(self.knowledge_base)

	@frappe.whitelist()
	def process(self) -> dict:
		"""Extract and index this document now."""
		from ai_fr_hg.ai.ingestion import enqueue_processing

		enqueue_processing(self.name)
		return {"document": self.name, "status": "Queued"}

	@frappe.whitelist()
	def reprocess(self) -> dict:
		"""Discard existing chunks and process this document again."""
		frappe.db.delete("AI Document Chunk", {"document": self.name})
		return self.process()

	@frappe.whitelist()
	def generate_summary(self) -> dict:
		"""Summarise this document and store the result."""
		from ai_fr_hg.api.knowledge import summarize_document

		return summarize_document(self.name)

	@frappe.whitelist()
	def run_extraction(self, schema: str | None = None) -> dict:
		"""Extract structured data using the configured schema."""
		from ai_fr_hg.api.knowledge import extract_document_data

		target = schema or self.extraction_schema
		if not target:
			frappe.throw(_("Select an Extraction Schema first."))
		return extract_document_data(self.name, target)
