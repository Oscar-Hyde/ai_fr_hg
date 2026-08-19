# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Controller for one stored translation of a document."""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint

from ai_fr_hg.ai.translation_utils import (
	is_supported,
	language_label,
	normalise_language,
	text_direction,
)


class AITranslation(Document):
	_DOCTYPE_NAME = "AI Translation"

	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		from ai_fr_hg.ai_knowledge.doctype.ai_translation_segment.ai_translation_segment import (
			AITranslationSegment,
		)

		character_count: DF.Int
		completed_on: DF.Datetime | None
		direction: DF.Data | None
		domain: DF.Data | None
		duration_ms: DF.Int
		error_message: DF.SmallText | None
		flagged_segments: DF.Int
		glossary: DF.Link | None
		index_output: DF.Check
		issue_summary: DF.Code | None
		job_id: DF.Data | None
		knowledge_base: DF.Link | None
		memory_hits: DF.Int
		model: DF.Link | None
		model_used: DF.Data | None
		naming_series: DF.Literal["AITRN-.YYYY.-"]
		preserve_formatting: DF.Check
		quality_score: DF.Percent
		requested_by: DF.Link | None
		requested_on: DF.Datetime | None
		segment_count: DF.Int
		segments: DF.Table[AITranslationSegment]
		source_document: DF.Link | None
		source_language: DF.Literal["", "ar", "en", "he"]
		source_text: DF.LongText | None
		status: DF.Literal["Draft", "Queued", "Translating", "Completed", "Needs Review", "Failed"]
		target_language: DF.Literal["ar", "en", "he"]
		title: DF.Data
		tone: DF.Literal["Neutral", "Formal", "Informal", "Technical", "Legal"]
		total_tokens: DF.Int
		translated_document: DF.Link | None
		translated_text: DF.LongText | None
	# end: auto-generated types

	def before_validate(self):
		self.source_language = normalise_language(self.source_language)
		self.target_language = normalise_language(self.target_language)
		self.direction = text_direction(self.target_language)

		if not self.title:
			source_title = (
				frappe.db.get_value("AI Document", self.source_document, "title")
				if self.source_document
				else None
			)
			self.title = f"{source_title or _('Text')} → {language_label(self.target_language)}"

		if self.source_document and not self.source_text:
			self.source_text = frappe.db.get_value("AI Document", self.source_document, "content")

	def validate(self):
		if not is_supported(self.target_language):
			frappe.throw(_("Choose Arabic, English or Hebrew as the target language."))
		if self.source_language and self.source_language == self.target_language:
			frappe.throw(_("The source and target languages must differ."))
		if not (self.source_text or "").strip() and not self.source_document:
			frappe.throw(_("Provide source text or a source document to translate."))

		self.segment_count = len(self.get("segments") or [])
		self.flagged_segments = sum(
			1 for row in self.get("segments") or [] if row.status in {"Flagged", "Failed"}
		)

	def on_trash(self):
		# The indexed copy is an ordinary knowledge document with its own
		# lifecycle; deleting the translation record must not silently delete
		# content someone may be citing.
		if self.translated_document and frappe.db.exists("AI Document", self.translated_document):
			frappe.msgprint(
				_("The indexed document {0} was kept.").format(self.translated_document),
				indicator="blue",
				alert=True,
			)

	# -- Actions ---------------------------------------------------------

	def _assert_write(self) -> None:
		self.check_permission("write")

	@frappe.whitelist()
	def translate(self, background: bool = True) -> dict:
		"""Run this translation now, or queue it on a background worker."""
		from ai_fr_hg.ai.translation import enqueue_translation, run_translation

		self._assert_write()
		if self.status in {"Queued", "Translating"}:
			frappe.throw(_("This translation is already running."))

		if cint(background):
			return enqueue_translation(self.name)
		return run_translation(self.name, requested_by=frappe.session.user)

	@frappe.whitelist()
	def retranslate(self, segment_index: int, instructions: str = "") -> dict:
		"""Re-run a single segment, optionally with a reviewer's hint."""
		from ai_fr_hg.ai.translation import retranslate_segment

		self._assert_write()
		return retranslate_segment(self.name, cint(segment_index), instructions=instructions)

	@frappe.whitelist()
	def index_output_document(self) -> dict:
		"""Store the finished translation as its own searchable AI Document."""
		from ai_fr_hg.ai.translation import index_translation

		self._assert_write()
		document = index_translation(self.name)
		return {"translation": self.name, "document": document}

	@frappe.whitelist()
	def mark_reviewed(self) -> dict:
		"""Accept the current translation as human-reviewed."""
		self._assert_write()
		for row in self.get("segments") or []:
			row.reviewed = 1
			if row.status == "Flagged":
				row.status = "Reviewed"
		self.flagged_segments = 0
		self.status = "Completed"
		self.save()
		return {"translation": self.name, "status": self.status}
