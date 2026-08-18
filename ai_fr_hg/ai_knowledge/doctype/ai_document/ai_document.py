# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document

from ai_fr_hg.ai.ingestion import enqueue_processing, validate_source_access


class AIDocument(Document):
	_DOCTYPE_NAME = "AI Document"

	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		from ai_fr_hg.ai_knowledge.doctype.ai_document_tag.ai_document_tag import AIDocumentTag

		character_count: DF.Float
		checksum: DF.Data | None
		chunk_count: DF.Int
		confidence: DF.Percent
		content: DF.LongText | None
		document_type: DF.Data | None
		embedded_chunk_count: DF.Int
		error_message: DF.SmallText | None
		extracted_data: DF.Code | None
		extraction_schema: DF.Link | None
		file_size: DF.Float
		folder: DF.Link | None
		indexed_on: DF.Datetime | None
		is_private: DF.Check
		knowledge_base: DF.Link
		language: DF.Data | None
		metadata: DF.Code | None
		mime_type: DF.Data | None
		naming_series: DF.Literal["AIDOC-.YYYY.-"]
		page_count: DF.Int
		processing_duration_ms: DF.Int
		processing_job_id: DF.Data | None
		processing_requested_by: DF.Link | None
		processing_requested_on: DF.Datetime | None
		reader_used: DF.Data | None
		error_type: DF.Data | None
		retry_count: DF.Int
		source_doctype: DF.Link | None
		source_file: DF.Attach | None
		source_folder: DF.Data | None
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
		self.sync_folder_from_file()

	def before_save(self):
		self.sync_folder_from_file()

	def sync_folder_from_file(self):
		"""Derive folder provenance from the attached File's folder."""
		if self.source_type == "File" and self.source_file:
			try:
				file_name = frappe.db.get_value("File", {"file_url": self.source_file}, "name")
				if file_name:
					folder = frappe.db.get_value("File", file_name, "folder")
					if folder:
						self.folder = folder
						self.source_folder = folder
					else:
						# Fallback to default if file has no explicit folder
						from ai_fr_hg.ai.folders import get_default_folder

						default = get_default_folder(user=self.owner or frappe.session.user)
						self.folder = default
						self.source_folder = default
			except Exception:
				pass
		elif self.source_type == "Folder" and self.source_file:
			# When source is a folder path, preserve it
			if not self.source_folder:
				self.source_folder = self.source_file

	def validate(self):
		self.validate_source()
		validate_source_access(self, user=frappe.session.user)

	def validate_source(self):
		if self.source_type == "File" and not self.source_file:
			frappe.throw(_("A source file is required when Source Type is File."))
		if self.source_type == "Text" and not self.content:
			frappe.throw(_("Text content is required when Source Type is Text."))
		if self.source_type == "URL" and not self.source_url:
			frappe.throw(_("A source URL is required when Source Type is URL."))
		if self.source_type == "DocType Record" and not (self.source_doctype and self.source_name):
			frappe.throw(_("Source DocType and Source Name are required."))

	def _assert_write_access(self) -> None:
		self.check_permission("write")

	def _assert_status(self, allowed: set[str], action: str) -> None:
		if self.status not in allowed:
			frappe.throw(
				_("Cannot {0} a document in {1} status.").format(action, self.status),
				frappe.ValidationError,
			)

	def after_insert(self):
		"""Auto-process new documents when the platform is configured to."""
		if self.flags.get("skip_auto_process") or self.status not in {"Draft", "Queued"}:
			return
		if not frappe.db.get_single_value("AI Platform Settings", "auto_process_documents"):
			return

		enqueue_processing(self.name, requested_by=self.owner)

	def on_trash(self):
		frappe.db.delete("AI Document Chunk", {"document": self.name})

	def after_delete(self):
		from ai_fr_hg.ai.knowledge import update_knowledge_base_stats

		if frappe.db.exists("AI Knowledge Base", self.knowledge_base):
			update_knowledge_base_stats(self.knowledge_base)

	@frappe.whitelist()
	def process(self) -> dict:
		"""Validate source authority and enqueue first-time or failed processing."""
		self._assert_write_access()
		self._assert_status({"Draft", "Failed", "Queued"}, _("process"))
		validate_source_access(self, user=frappe.session.user)
		return enqueue_processing(self.name, requested_by=frappe.session.user)

	@frappe.whitelist()
	def reprocess(self) -> dict:
		"""Discard existing chunks and enqueue a fresh authorized processing run."""
		self._assert_write_access()
		self._assert_status({"Draft", "Failed", "Indexed"}, _("reprocess"))
		validate_source_access(self, user=frappe.session.user)
		frappe.db.delete("AI Document Chunk", {"document": self.name})
		frappe.db.set_value(
			self.doctype,
			self.name,
			{
				"status": "Draft",
				"chunk_count": 0,
				"embedded_chunk_count": 0,
				"indexed_on": None,
			},
			update_modified=False,
		)
		return enqueue_processing(
			self.name,
			requested_by=frappe.session.user,
			reset_retries=True,
		)

	@frappe.whitelist()
	def generate_summary(self) -> dict:
		"""Summarise an indexed document and store the result."""
		self._assert_write_access()
		self._assert_status({"Indexed"}, _("summarize"))
		from ai_fr_hg.api.knowledge import summarize_document

		return summarize_document(self.name)

	@frappe.whitelist()
	def run_extraction(self, schema: str | None = None) -> dict:
		"""Extract structured data from an indexed document."""
		self._assert_write_access()
		self._assert_status({"Indexed"}, _("extract data from"))
		from ai_fr_hg.api.knowledge import extract_document_data

		target = schema or self.extraction_schema
		if not target:
			frappe.throw(_("Select an Extraction Schema first."))
		return extract_document_data(self.name, target)
