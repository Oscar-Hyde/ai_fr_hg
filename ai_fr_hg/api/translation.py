# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Whitelisted translation endpoints (Arabic / English / Hebrew).

Every call runs against local models only. Long documents are translated on a
background worker; short inline text is translated in the request so chat and
form actions stay responsive.
"""

import json

import frappe
from frappe import _
from frappe.utils import cint

from ai_fr_hg.ai.translation import (
	MAX_INLINE_CHARACTERS,
	authorized_memory_scope,
	cancel_translation,
	create_translation,
	enqueue_translation,
	index_translation,
	retranslate_segment,
	run_translation,
	supported_languages,
	translate_text,
	translation_enabled,
)


@frappe.whitelist()
def get_languages() -> dict:
	"""The language pairs this platform can translate, for pickers and tools."""
	languages = supported_languages()
	return {
		"enabled": translation_enabled(),
		"languages": languages,
		"pairs": [
			{"source": source["code"], "target": target["code"]}
			for source in languages
			for target in languages
			if source["code"] != target["code"]
		],
	}


@frappe.whitelist()
def translate(
	text: str,
	target_language: str,
	source_language: str | None = None,
	model: str | None = None,
	glossary: str | None = None,
	tone: str = "Neutral",
	domain: str = "",
	knowledge_base: str | None = None,
) -> dict:
	"""Translate a bounded piece of text inline and return the result.

	Translation memory is used only when `knowledge_base` is an authorized
	scope. Omitting it does not search every corpus.
	"""
	frappe.has_permission("AI Translation", "create", throw=True)

	if len(text or "") > MAX_INLINE_CHARACTERS:
		frappe.throw(
			_("Inline translation is limited to {0} characters. Translate the document instead.").format(
				MAX_INLINE_CHARACTERS
			)
		)

	scope = authorized_memory_scope(knowledge_base)
	if knowledge_base and not scope:
		frappe.throw(_("You cannot use translation memory from that knowledge base."), frappe.PermissionError)

	outcome = translate_text(
		text,
		target_language,
		source_language,
		model=model,
		glossary=glossary,
		tone=tone,
		domain=domain,
		knowledge_base=scope,
	)
	return outcome.as_dict()


@frappe.whitelist()
def translate_document(
	document: str,
	target_language: str,
	source_language: str | None = None,
	model: str | None = None,
	glossary: str | None = None,
	tone: str = "Neutral",
	domain: str = "",
	preserve_formatting: bool = True,
	index_output: bool = False,
	background: bool = True,
) -> dict:
	"""Translate an extracted document and store the result as `AI Translation`."""
	frappe.has_permission("AI Translation", "create", throw=True)

	translation = create_translation(
		document,
		target_language,
		source_language=source_language,
		model=model,
		glossary=glossary,
		tone=tone,
		domain=domain,
		preserve_formatting=cint(preserve_formatting),
		index_output=cint(index_output),
	)

	if cint(background):
		return {"translation": translation, **enqueue_translation(translation)}
	return {"translation": translation, **run_translation(translation)}


@frappe.whitelist()
def get_translation(translation: str, include_segments: bool = True) -> dict:
	"""Read one translation, with its segments, for review UIs."""
	doc = frappe.get_doc("AI Translation", translation)
	doc.check_permission("read")

	payload = {
		"name": doc.name,
		"title": doc.title,
		"status": doc.status,
		"source_document": doc.source_document,
		"knowledge_base": doc.knowledge_base,
		"source_language": doc.source_language,
		"target_language": doc.target_language,
		"direction": doc.direction,
		"translated_text": doc.translated_text,
		"quality_score": doc.quality_score,
		"segment_count": doc.segment_count,
		"flagged_segments": doc.flagged_segments,
		"memory_hits": doc.memory_hits,
		"model_used": doc.model_used,
		"duration_ms": doc.duration_ms,
		"total_tokens": doc.total_tokens,
		"translated_document": doc.translated_document,
		"error_message": doc.error_message,
		"processing_progress": doc.get("processing_progress"),
		"processing_message": doc.get("processing_message"),
		"cancel_requested": cint(doc.get("cancel_requested")),
		"issues": json.loads(doc.issue_summary) if doc.issue_summary else {},
	}
	if cint(include_segments):
		payload["segments"] = [
			{
				"segment_index": row.segment_index,
				"kind": row.kind,
				"heading": row.heading,
				"page_number": row.page_number,
				"status": row.status,
				"quality_score": row.quality_score,
				"issues": row.issues,
				"reused": row.reused,
				"reviewed": row.reviewed,
				"source_text": row.source_text,
				"translated_text": row.translated_text,
			}
			for row in sorted(doc.get("segments") or [], key=lambda item: cint(item.segment_index))
		]
	return payload


@frappe.whitelist()
def list_translations(
	document: str | None = None,
	knowledge_base: str | None = None,
	target_language: str | None = None,
	limit: int = 20,
) -> list:
	"""List translations the current user may read."""
	from ai_fr_hg.utils import api_validation

	document = api_validation.valid_identifier(document, label=_("Document")) if document else None
	knowledge_base = (
		api_validation.valid_identifier(knowledge_base, label=_("Knowledge base")) if knowledge_base else None
	)
	target_language = (
		api_validation.enum_choice(target_language, allowed=("ar", "en", "he"), label=_("Language"))
		if target_language
		else None
	)
	filters: dict = {}
	if document:
		filters["source_document"] = document
	if knowledge_base:
		filters["knowledge_base"] = knowledge_base
	if target_language:
		filters["target_language"] = target_language

	return frappe.get_list(
		"AI Translation",
		filters=filters,
		fields=[
			"name",
			"title",
			"status",
			"source_document",
			"source_language",
			"target_language",
			"quality_score",
			"flagged_segments",
			"translated_document",
			"modified",
		],
		order_by="modified desc",
		limit_page_length=api_validation.bounded_integer(
			limit, label=_("limit"), default=20, maximum=api_validation.MAX_TRANSLATION_PAGE
		),
	)


@frappe.whitelist()
def retranslate(translation: str, segment_index: int, instructions: str = "") -> dict:
	"""Re-run one segment of a stored translation."""
	return retranslate_segment(translation, cint(segment_index), instructions=instructions)


@frappe.whitelist()
def index_output(translation: str) -> dict:
	"""Index a finished translation as its own searchable document."""
	return {"translation": translation, "document": index_translation(translation)}


@frappe.whitelist()
def cancel(translation: str) -> dict:
	"""Cancel a queued or in-flight translation."""
	from ai_fr_hg.utils import api_validation

	name = api_validation.valid_identifier(translation, label=_("Translation"), required=True)
	return cancel_translation(name)


@frappe.whitelist()
def get_glossaries(knowledge_base: str | None = None) -> list:
	"""Enabled glossaries the caller may read, optionally filtered to a KB."""
	from ai_fr_hg.utils import api_validation
	from ai_fr_hg.utils.permissions import _knowledge_base_access

	filters: dict = {"enabled": 1}
	if knowledge_base:
		scope = api_validation.valid_identifier(knowledge_base, label=_("Knowledge base"))
		if not _knowledge_base_access(scope, frappe.session.user, write=False):
			frappe.throw(_("You cannot list glossaries for that knowledge base."), frappe.PermissionError)
		filters["knowledge_base"] = ["in", [scope, ""]]
	return frappe.get_list(
		"AI Translation Glossary",
		filters=filters,
		fields=["name", "glossary_name", "knowledge_base", "description"],
		order_by="glossary_name asc",
		limit=api_validation.bounded_integer(100, label=_("limit"), default=100, maximum=100),
	)
