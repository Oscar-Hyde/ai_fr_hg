# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Built-in tool handlers.

Each handler runs with the calling user's permissions and returns plain,
JSON-serialisable data that a model can reason about.
"""

import frappe
from frappe import _
from frappe.utils import cint, now_datetime


def _resolve_ai_document(identifier: str | None) -> str | None:
	"""Find an AI Document name by ID, title, filename, or partial match."""
	if not identifier or not isinstance(identifier, str):
		return None

	identifier = identifier.strip()
	if not identifier:
		return None

	# 1. Exact primary key match
	if frappe.db.exists("AI Document", identifier):
		return identifier

	# 2. Extract clean filename / basename if a path was passed (e.g. /files/foo.docx or C:\foo.docx)
	basename = identifier.replace("\\", "/").rsplit("/", 1)[-1].strip()

	# 3. Exact title or source_file match
	for field in ("title", "source_file"):
		match = frappe.db.get_value("AI Document", {field: identifier}, "name")
		if match:
			return match
		if basename and basename != identifier:
			match = frappe.db.get_value("AI Document", {field: basename}, "name")
			if match:
				return match

	# 4. Partial match on title or source_file
	search_terms = [basename] if basename else [identifier]
	if basename and "." in basename:
		stem = basename.rsplit(".", 1)[0].strip()
		if stem and stem not in search_terms:
			search_terms.append(stem)

	for term in search_terms:
		match = frappe.db.get_value(
			"AI Document",
			{"title": ["like", f"%{term}%"]},
			"name",
			order_by="modified desc",
		)
		if match:
			return match
		match = frappe.db.get_value(
			"AI Document",
			{"source_file": ["like", f"%{term}%"]},
			"name",
			order_by="modified desc",
		)
		if match:
			return match

	# 5. Check if there's a recent document by this user if generic query
	if len(search_terms) == 1 and search_terms[0].lower() in (
		"latest",
		"recent",
		"uploaded",
		"last",
		"document",
		"this document",
	):
		recent = frappe.get_list(
			"AI Document",
			filters={"owner": frappe.session.user},
			fields=["name"],
			order_by="modified desc",
			limit_page_length=1,
		)
		if recent:
			return recent[0].name

	return None


def search_knowledge_base(
	query: str | None = None,
	knowledge_base: str | None = None,
	limit: int = 5,
	**kwargs,
) -> dict:
	"""Semantic search across the knowledge bases the user may read."""
	from ai_fr_hg.ai.knowledge import retrieve

	resolved_query = (
		query
		or kwargs.get("q")
		or kwargs.get("search_query")
		or kwargs.get("text")
		or kwargs.get("prompt")
		or kwargs.get("question")
		or ""
	)
	resolved_kb = (
		knowledge_base or kwargs.get("kb") or kwargs.get("kb_name") or kwargs.get("knowledge_base_name")
	)
	resolved_limit = limit or kwargs.get("top_k") or kwargs.get("max_results") or kwargs.get("count") or 5

	if not resolved_query:
		return {"query": "", "count": 0, "results": []}

	results = retrieve(
		resolved_query,
		knowledge_bases=[resolved_kb] if resolved_kb else None,
		top_k=min(cint(resolved_limit) or 5, 20),
	)
	return {
		"query": resolved_query,
		"count": len(results),
		"results": [
			{
				"document": r.document_title,
				"heading": r.heading,
				"page": r.page_number,
				"score": round(r.score, 4),
				"content": r.content[:2000],
			}
			for r in results
		],
	}


def get_document(
	doctype: str | None = None,
	name: str | None = None,
	fields: list | str | None = None,
	**kwargs,
) -> dict:
	"""Fetch a single Frappe document the user is allowed to read.

	Row-level and field-level enforcement is centralized in
	:mod:`ai_fr_hg.ai.tools.query`; requesting no fields returns every
	readable, non-sensitive field, never a raw ``as_dict`` dump.
	"""
	resolved_doctype = doctype or kwargs.get("doc_type") or kwargs.get("document_type")
	resolved_name = (
		name or kwargs.get("id") or kwargs.get("docname") or kwargs.get("doc_name") or kwargs.get("document")
	)
	resolved_fields = fields or kwargs.get("columns") or kwargs.get("fieldnames")

	if not resolved_doctype or not resolved_name:
		return {"error": "Both 'doctype' and 'name' are required to fetch a document."}

	if isinstance(resolved_fields, str):
		resolved_fields = [f.strip() for f in resolved_fields.split(",") if f.strip()]

	from ai_fr_hg.ai.tools.query import safe_get

	return safe_get(resolved_doctype, resolved_name, resolved_fields or None)


def list_documents(
	doctype: str | None = None,
	filters: dict | str | None = None,
	fields: list | str | None = None,
	limit: int = 20,
	order_by: str | None = None,
	**kwargs,
) -> list:
	"""List records of a DocType, respecting the user's permissions."""
	from ai_fr_hg.ai.tools.query import safe_list

	resolved_doctype = doctype or kwargs.get("doc_type") or kwargs.get("document_type")
	if not resolved_doctype:
		return []

	resolved_filters = filters or kwargs.get("filter") or kwargs.get("where") or {}
	resolved_fields = fields or kwargs.get("columns") or kwargs.get("fieldnames")
	if isinstance(resolved_fields, str):
		resolved_fields = [f.strip() for f in resolved_fields.split(",") if f.strip()]

	resolved_limit = limit or kwargs.get("limit_page_length") or kwargs.get("count") or 20
	resolved_order = order_by or kwargs.get("sort_by") or kwargs.get("order")

	return safe_list(
		resolved_doctype,
		filters=resolved_filters,
		fields=resolved_fields or None,
		limit=cint(resolved_limit),
		order_by=resolved_order,
	)


def count_documents(
	doctype: str | None = None,
	filters: dict | str | None = None,
	**kwargs,
) -> dict:
	"""Count records of a DocType matching optional filters.

	The count is row-level permission aware and bounded; it never uses
	``frappe.db.count``, which ignores permission query conditions.
	"""
	from ai_fr_hg.ai.tools.query import safe_count

	resolved_doctype = doctype or kwargs.get("doc_type") or kwargs.get("document_type")
	if not resolved_doctype:
		return {"doctype": "", "count": 0, "exact": True, "bounded": False}

	resolved_filters = filters or kwargs.get("filter") or kwargs.get("where") or {}
	return safe_count(resolved_doctype, resolved_filters)


def run_report(
	report: str | None = None,
	filters: dict | str | None = None,
	**kwargs,
) -> dict:
	"""Execute a query report and return its columns and rows."""
	import json

	from frappe.desk.query_report import run

	resolved_report = report or kwargs.get("report_name") or kwargs.get("name")
	if not resolved_report:
		return {"error": "Report name is required."}

	frappe.has_permission("Report", "read", doc=resolved_report, throw=True)

	resolved_filters = filters or kwargs.get("filter") or kwargs.get("params") or {}
	if isinstance(resolved_filters, str):
		try:
			resolved_filters = json.loads(resolved_filters)
		except ValueError:
			resolved_filters = {}

	result = run(resolved_report, filters=resolved_filters or {}, ignore_prepared_report=True)
	return {
		"columns": [c.get("label") if isinstance(c, dict) else c for c in (result.get("columns") or [])],
		"rows": (result.get("result") or [])[:100],
	}


def get_document_text(
	document: str | None = None,
	max_characters: int = 8000,
	**kwargs,
) -> dict:
	"""Return the extracted text of an `AI Document`."""
	doc_identifier = (
		document
		or kwargs.get("file_path")
		or kwargs.get("file_name")
		or kwargs.get("filename")
		or kwargs.get("file")
		or kwargs.get("title")
		or kwargs.get("name")
		or kwargs.get("doc_name")
		or kwargs.get("document_name")
		or kwargs.get("document_title")
		or kwargs.get("doc")
		or kwargs.get("query")
		or kwargs.get("id")
		or kwargs.get("path")
	)

	max_chars = (
		max_characters or kwargs.get("max_chars") or kwargs.get("limit") or kwargs.get("length") or 8000
	)

	if not doc_identifier:
		doc_name = _resolve_ai_document("recent")
		if not doc_name:
			return {
				"error": "No document identifier provided. Please specify the document name, title, or filename.",
				"found": False,
			}
	else:
		doc_name = _resolve_ai_document(str(doc_identifier))

	if not doc_name:
		# Name the documents that do exist. Without this the model can only say
		# "I couldn't find it", which reads as a failure even when the real
		# cause is a slightly different title or a still-queued upload.
		return {
			"error": f"Document '{doc_identifier}' was not found in the knowledge base.",
			"found": False,
			"available_documents": _recent_document_choices(),
		}

	frappe.has_permission("AI Document", "read", doc=doc_name, throw=True)
	doc = frappe.get_doc("AI Document", doc_name)

	# The document may still be queued behind a background worker. Extract its
	# text inline so the answer is not blocked on the worker, but deliberately
	# skip indexing: embedding every chunk is many model round trips and does
	# not affect the text we are about to return. Indexing stays queued.
	if (not doc.content or doc.status in ("Queued", "Extracting")) and doc.source_file:
		try:
			from ai_fr_hg.ai.ingestion import process_document

			process_document(doc.name, index=False)
			doc.reload()
		except Exception as exc:
			frappe.log_error(title="On-demand AI document extraction failed", message=str(exc))

	content = doc.content or ""

	# Extraction can legitimately yield nothing - a scanned PDF with no OCR, an
	# unsupported format, a worker that has not run yet. Say which, so the
	# model relays a cause instead of "I couldn't find the document".
	if not content.strip():
		return {
			"document": doc.name,
			"title": doc.title,
			"status": doc.status,
			"found": True,
			"content": "",
			"error": _document_text_unavailable_reason(doc),
		}

	from ai_fr_hg.ai.language import language_name, resolve_document_language

	code = resolve_document_language(doc.language, content)
	if code and code != (doc.language or "").strip():
		frappe.db.set_value("AI Document", doc.name, "language", code, update_modified=False)
	truncated = len(content) > (cint(max_chars) or 8000)

	return {
		"document": doc.name,
		"title": doc.title,
		"status": doc.status,
		"knowledge_base": doc.knowledge_base,
		"language": code or None,
		"language_name": language_name(code) if code else None,
		"summary": doc.summary,
		"content": content[: cint(max_chars) or 8000],
		"truncated": truncated,
		"total_characters": len(content),
	}


def _recent_document_choices(limit: int = 10) -> list[dict]:
	"""Recent documents the user may read, to disambiguate a failed lookup."""
	try:
		return frappe.get_list(
			"AI Document",
			fields=["name", "title", "status"],
			order_by="modified desc",
			limit_page_length=limit,
		)
	except Exception:
		return []


def _document_text_unavailable_reason(doc) -> str:
	"""Explain why a document that exists has no readable text yet."""
	if doc.status == "Failed":
		return (
			f"Document '{doc.title}' could not be processed: "
			f"{doc.error_message or 'no further detail was recorded'}."
		)
	if doc.status in ("Queued", "Extracting", "Chunking", "Embedding"):
		return f"Document '{doc.title}' is still being processed (status: {doc.status}). Ask again shortly."
	return (
		f"Document '{doc.title}' contains no extractable text. It may be a scanned "
		"image needing OCR, or an unsupported format."
	)


def translate_content(
	target_language: str | None = None,
	document: str | None = None,
	text: str | None = None,
	source_language: str | None = None,
	max_characters: int = 12000,
	**kwargs,
) -> dict:
	"""Translate an uploaded document, or a passage, between Arabic, English and Hebrew.

	The tool is deliberately read-only: it returns the translated text to the
	conversation without creating an `AI Translation` record, so an agent can
	answer "what does this contract say in Hebrew?" in one turn. Use the
	Translate action on the document itself for a stored, reviewable
	translation.
	"""
	from ai_fr_hg.ai.translation import MAX_INLINE_CHARACTERS, translate_text
	from ai_fr_hg.ai.translation_utils import is_supported, language_label

	target = (
		target_language
		or kwargs.get("target")
		or kwargs.get("language")
		or kwargs.get("to")
		or kwargs.get("to_language")
	)
	if not is_supported(target):
		return {
			"error": "Choose a supported target language: Arabic (ar), English (en) or Hebrew (he).",
			"translated": False,
		}

	source_text = text or kwargs.get("content") or kwargs.get("passage")
	title = None
	document_name = None
	knowledge_base = None

	if not source_text:
		identifier = document or kwargs.get("document_name") or kwargs.get("title") or kwargs.get("file")
		extracted = get_document_text(document=identifier, max_characters=cint(max_characters) or 12000)
		if extracted.get("error") and not extracted.get("content"):
			return {**extracted, "translated": False}
		source_text = extracted.get("content") or ""
		title = extracted.get("title")
		document_name = extracted.get("document")
		# The document was already permission-checked by `get_document_text`.
		# Its knowledge base is the only memory scope this tool may use.
		knowledge_base = extracted.get("knowledge_base")

	if not (source_text or "").strip():
		return {"error": "There is no text to translate.", "translated": False}

	limit = min(cint(max_characters) or 12000, MAX_INLINE_CHARACTERS)
	truncated = len(source_text) > limit

	outcome = translate_text(
		source_text[:limit],
		target,
		source_language,
		reference_doctype="AI Document" if document_name else None,
		reference_name=document_name,
		knowledge_base=knowledge_base,
	)
	return {
		"translated": True,
		"document": document_name,
		"title": title,
		"source_language": outcome.source_language,
		"source_language_name": language_label(outcome.source_language),
		"target_language": outcome.target_language,
		"target_language_name": language_label(outcome.target_language),
		"direction": outcome.direction,
		"quality_score": outcome.quality_score,
		"flagged_segments": outcome.flagged,
		"truncated": truncated,
		"text": outcome.text,
	}


def current_datetime(**kwargs) -> dict:
	"""Return the site's current date and time."""
	from frappe.utils import get_system_timezone

	now = now_datetime()
	return {
		"datetime": str(now),
		"date": str(now.date()),
		"time": str(now.time()),
		"timezone": get_system_timezone(),
	}
