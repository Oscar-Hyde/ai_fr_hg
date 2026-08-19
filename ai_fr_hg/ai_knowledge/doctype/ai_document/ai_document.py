# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

from contextlib import contextmanager
from contextvars import ContextVar

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint

from ai_fr_hg.ai.ingestion import enqueue_processing, validate_source_access
from ai_fr_hg.ai.organization import organization_name_key


_COPY_PROVENANCE_ALLOWED = ContextVar("ai_document_copy_provenance_allowed", default=False)
_DEFER_FILE_SOURCE_SYNC = ContextVar("ai_document_defer_file_source_sync", default=False)


@contextmanager
def allow_copy_provenance():
	"""Authorize provenance initialization only inside the canonical copy service."""
	token = _COPY_PROVENANCE_ALLOWED.set(True)
	try:
		yield
	finally:
		_COPY_PROVENANCE_ALLOWED.reset(token)


@contextmanager
def allow_deferred_file_source_sync():
	"""Permit an atomic copy to insert its identity before its new File row."""
	token = _DEFER_FILE_SOURCE_SYNC.set(True)
	try:
		yield
	finally:
		_DEFER_FILE_SOURCE_SYNC.reset(token)


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
		folder: DF.Link
		organization_name: DF.Data
		organization_name_key: DF.Data
		organization_revision: DF.Int
		source_file_record: DF.Link | None
		copied_from: DF.Data | None
		copied_on: DF.Datetime | None
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

	def before_validate(self):
		if not self.title and self.source_file:
			self.title = self.source_file.rsplit("/", 1)[-1]
		self.sync_folder_from_file()
		self.folder = self.folder or "Home"
		self.source_folder = self.folder

		# Organization names are stable location-local display identities.  A
		# title edit follows the name only for non-file sources that had not
		# already been independently renamed through the tree service.
		old = self.get_doc_before_save()
		if not self.organization_name:
			self.organization_name = self._default_organization_name()
		elif (
			old
			and self.source_type != "File"
			and self.has_value_changed("title")
			and old.organization_name == old.title
		):
			self.organization_name = self.title

	def sync_folder_from_file(self):
		"""Resolve file-backed provenance through a stable File record identity."""
		if _DEFER_FILE_SOURCE_SYNC.get():
			return
		if self.source_type == "File" and self.source_file:
			file_name = self.source_file_record
			file_row = None
			if file_name:
				file_row = frappe.db.get_value(
					"File", file_name, ["file_url", "file_name", "folder", "is_folder"], as_dict=True
				)
				if not file_row:
					frappe.throw(_("Source File record {0} does not exist.").format(file_name))
				if file_row.file_url != self.source_file:
					# Replacing an attachment legitimately changes both identities. A
					# mismatch without a URL edit is stale or tampered state.
					if self.has_value_changed("source_file"):
						file_name = None
						file_row = None
					else:
						frappe.throw(_("Source File record and Source File URL do not match."))
			if not file_name:
				from ai_fr_hg.ai.ingestion import _file_doc

				file_row = _file_doc(self.source_file, document_name=self.name)
				file_name = file_row.name
			if file_row and file_row.is_folder:
				frappe.throw(_("A folder cannot be used as a document source file."))
			if file_name:
				self.source_file_record = file_name
				self.folder = file_row.folder or self.folder or "Home"
				self.source_folder = self.folder
		elif self.source_type == "Folder" and self.source_file and not self.source_folder:
			self.source_folder = self.source_file

	def _default_organization_name(self) -> str:
		if self.source_file_record:
			file_name = frappe.db.get_value("File", self.source_file_record, "file_name")
			if file_name:
				return file_name
		return self.title or self.name or _("Document")

	def validate(self):
		self.validate_source()
		self.validate_copy_provenance()
		self.validate_organization()
		self.lock_and_revalidate_file_source()
		self.set_organization_revision()
		validate_source_access(self, user=frappe.session.user)

	def lock_and_revalidate_file_source(self) -> None:
		"""Serialize canonical source changes after the parent folder is locked."""
		if self.source_type != "File" or _DEFER_FILE_SOURCE_SYNC.get():
			return
		if not self.source_file_record:
			frappe.throw(_("A stable Source File record is required for file-backed documents."))
		from ai_fr_hg.ai.folders import _lock_file_rows

		_lock_file_rows(self.source_file_record)
		row = frappe.db.get_value(
			"File",
			self.source_file_record,
			["file_url", "folder", "is_folder"],
			as_dict=True,
		)
		if (
			not row
			or row.is_folder
			or row.file_url != self.source_file
			or (row.folder or "Home") != (self.folder or "Home")
		):
			frappe.throw(_("The source File changed while this document was being saved. Refresh and try again."), frappe.TimestampMismatchError)

	def validate_copy_provenance(self) -> None:
		"""Keep copy lineage immutable and writable only by the canonical service."""
		old = self.get_doc_before_save()
		if old:
			if self.copied_from != old.copied_from or self.copied_on != old.copied_on:
				frappe.throw(_("Copy provenance is immutable."), frappe.ValidationError)
			return

		if not (self.copied_from or self.copied_on):
			return
		if not (self.copied_from and self.copied_on):
			frappe.throw(_("Copy provenance requires both a source identity and timestamp."), frappe.ValidationError)
		if not _COPY_PROVENANCE_ALLOWED.get():
			frappe.throw(_("Copy provenance can only be set by the document copy service."), frappe.PermissionError)
		if not frappe.db.exists(self.doctype, self.copied_from):
			frappe.throw(_("The source document for this copy no longer exists."), frappe.DoesNotExistError)

	def set_organization_revision(self) -> None:
		"""Keep optimistic-concurrency state server-authored for all placement edits."""
		old = self.get_doc_before_save()
		if not old:
			self.organization_revision = 0
			return
		organization_changed = any(
			getattr(self, field, None) != getattr(old, field, None)
			for field in (
				"folder",
				"source_folder",
				"organization_name",
				"source_file",
				"source_file_record",
			)
		)
		self.organization_revision = cint(old.organization_revision) + (1 if organization_changed else 0)

	def validate_organization(self) -> None:
		"""Enforce the canonical parent relation and location-local identity."""
		from ai_fr_hg.ai.folders import _assert_folder_exists, _check_write_access, _clean_name, _lock_folder_rows

		self.folder = _assert_folder_exists(self.folder or "Home")
		self.source_folder = self.folder
		self.organization_name = _clean_name(self.organization_name or self._default_organization_name())
		self.organization_name_key = organization_name_key(self.organization_name)

		if self.is_new() or self.has_value_changed("folder"):
			_check_write_access(self.folder, user=frappe.session.user)
			old = self.get_doc_before_save()
			_lock_folder_rows(getattr(old, "folder", None), self.folder)

		filters = {"folder": self.folder, "organization_name_key": self.organization_name_key}
		if self.name:
			filters["name"] = ["!=", self.name]
		duplicate = frappe.db.get_value("AI Document", filters, "name")
		if duplicate:
			frappe.throw(
				_("A document named {0} already exists in {1}.").format(
					frappe.bold(self.organization_name), frappe.bold(self.folder)
				),
				frappe.DuplicateEntryError,
			)

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
		"""Lock location, native Files, then identity before lifecycle cleanup."""
		from ai_fr_hg.ai.folders import _lock_file_rows, _lock_folder_rows

		# Frappe removes every native attachment after deleting the parent row.
		# Discover them before that lifecycle step and use the same deterministic
		# lock order as tree mutations, including the canonical source even when it
		# is not attached to this identity.
		files = frappe.get_all(
			"File",
			filters={"attached_to_doctype": self.doctype, "attached_to_name": self.name},
			fields=["name", "folder"],
			limit_page_length=0,
		)
		file_names = {row.name for row in files}
		if self.source_file_record and frappe.db.exists("File", self.source_file_record):
			file_names.add(self.source_file_record)
		folder_names = {self.folder or "Home"}
		folder_names.update(row.folder or "Home" for row in files)
		if self.source_file_record:
			source_folder = frappe.db.get_value("File", self.source_file_record, "folder")
			if source_folder:
				folder_names.add(source_folder)
		_lock_folder_rows(*folder_names)
		_lock_file_rows(*file_names)
		current = frappe.db.get_value(
			self.doctype,
			self.name,
			["folder", "source_file_record", "source_file"],
			as_dict=True,
			for_update=True,
		)
		if not current or (
			(current.folder or "Home") != (self.folder or "Home")
			or current.source_file_record != self.source_file_record
			or current.source_file != self.source_file
		):
			frappe.throw(_("The document changed while it was being deleted. Refresh and try again."), frappe.TimestampMismatchError)
		frappe.db.delete("AI Document Chunk", {"document": self.name})
		self._detach_translations()

	def _detach_translations(self):
		"""Keep translations of this document, but as standalone records.

		A translation is reviewed, cited and sometimes indexed content of its
		own. Deleting the source must neither be blocked by that link nor
		silently destroy the reviewed output, so the reference is cleared and
		the provenance is preserved in the title.
		"""
		translations = frappe.get_all(
			"AI Translation", filters={"source_document": self.name}, pluck="name"
		)
		for name in translations:
			frappe.db.set_value(
				"AI Translation",
				name,
				{"source_document": None},
				update_modified=False,
			)

	def after_delete(self):
		if self.flags.get("skip_knowledge_base_stats"):
			return
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

	@frappe.whitelist()
	def translate(
		self,
		target_language: str,
		source_language: str | None = None,
		model: str | None = None,
		glossary: str | None = None,
		tone: str = "Neutral",
		domain: str = "",
		index_output: bool = False,
		background: bool = True,
	) -> dict:
		"""Translate this document's extracted text into Arabic, English or Hebrew."""
		self.check_permission("read")
		from ai_fr_hg.api.translation import translate_document

		return translate_document(
			self.name,
			target_language,
			source_language=source_language,
			model=model,
			glossary=glossary,
			tone=tone,
			domain=domain,
			index_output=index_output,
			background=background,
		)
