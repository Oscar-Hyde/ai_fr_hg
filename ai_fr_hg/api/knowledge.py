# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Whitelisted knowledge, document and search endpoints."""

import json

import frappe
from frappe import _
from frappe.utils import cint


@frappe.whitelist()
def upload_document(
	file_url: str,
	knowledge_base: str,
	title: str | None = None,
	extraction_schema: str | None = None,
	process_now: bool = False,
) -> dict:
	"""Ingest an uploaded file into a knowledge base."""
	from ai_fr_hg.ai.ingestion import ingest_file

	frappe.has_permission("AI Document", "create", throw=True)

	document = ingest_file(
		file_url=file_url,
		knowledge_base=knowledge_base,
		title=title,
		extraction_schema=extraction_schema,
		enqueue_job=not cint(process_now),
	)
	return {
		"document": document,
		"status": frappe.db.get_value("AI Document", document, "status"),
	}


@frappe.whitelist()
def add_text(text: str, knowledge_base: str, title: str, process_now: bool = False) -> dict:
	"""Ingest raw text into a knowledge base."""
	from ai_fr_hg.ai.ingestion import ingest_text

	frappe.has_permission("AI Document", "create", throw=True)

	document = ingest_text(
		text=text, knowledge_base=knowledge_base, title=title, enqueue_job=not cint(process_now)
	)
	return {
		"document": document,
		"status": frappe.db.get_value("AI Document", document, "status"),
	}


@frappe.whitelist()
def reprocess_document(document: str, force: bool = False) -> dict:
	"""Re-run extraction and indexing for a document."""
	doc = frappe.get_doc("AI Document", document)
	doc.check_permission("write")

	from ai_fr_hg.ai.ingestion import enqueue_processing

	if cint(force):
		frappe.db.delete("AI Document Chunk", {"document": document})

	enqueue_processing(document)
	return {"document": document, "status": "Queued"}


@frappe.whitelist()
def reindex_knowledge_base(knowledge_base: str) -> dict:
	"""Queue every document in a knowledge base for reprocessing."""
	frappe.only_for(["AI Manager", "System Manager"])

	from ai_fr_hg.ai.ingestion import enqueue_processing

	documents = frappe.get_all(
		"AI Document",
		filters={"knowledge_base": knowledge_base, "status": ["!=", "Archived"]},
		pluck="name",
	)
	for document in documents:
		enqueue_processing(document)

	frappe.db.set_value("AI Knowledge Base", knowledge_base, "index_status", "Indexing")
	return {"knowledge_base": knowledge_base, "queued": len(documents)}


@frappe.whitelist()
def search(
	query: str,
	knowledge_bases: str | list | None = None,
	top_k: int = 10,
	search_type: str | None = None,
) -> dict:
	"""Search the knowledge base and return ranked passages."""
	from ai_fr_hg.ai.knowledge import retrieve

	if isinstance(knowledge_bases, str):
		try:
			knowledge_bases = json.loads(knowledge_bases)
		except ValueError:
			knowledge_bases = [knowledge_bases]

	results = retrieve(
		query,
		knowledge_bases=knowledge_bases,
		top_k=cint(top_k) or 10,
		search_type=search_type,
	)
	return {"query": query, "count": len(results), "results": [r.as_dict() for r in results]}


@frappe.whitelist()
def ask(
	question: str,
	knowledge_bases: str | list | None = None,
	agent: str | None = None,
	model: str | None = None,
	documents: str | list | None = None,
) -> dict:
	"""One-shot grounded question answering, without creating a conversation.

	`documents` scopes the answer to specific `AI Document` records (e.g. the
	"Ask About This" button), waiting for indexing if they were just uploaded.
	"""
	from ai_fr_hg.ai.agent import run_agent_turn
	from ai_fr_hg.ai.deadline import turn_budget
	from ai_fr_hg.ai.ingestion import wait_for_indexed
	from ai_fr_hg.api.chat import _coerce_documents, _get_turn_budget

	if isinstance(knowledge_bases, str):
		try:
			knowledge_bases = json.loads(knowledge_bases)
		except ValueError:
			knowledge_bases = [knowledge_bases]

	documents = _coerce_documents(documents)

	# Interactive, so it carries the same proxy deadline as chat.
	with turn_budget(_get_turn_budget()):
		if documents:
			wait_for_indexed(documents)
		return run_agent_turn(
			question,
			agent=agent,
			knowledge_bases=knowledge_bases,
			model=model,
			include_history=False,
			save_messages=False,
			documents=documents,
		)


@frappe.whitelist()
def summarize_document(document: str, max_words: int = 300, save: bool = True) -> dict:
	"""Summarise a document and optionally store the summary on it."""
	from ai_fr_hg.ai.intelligence import summarize

	doc = frappe.get_doc("AI Document", document)
	doc.check_permission("read")

	if not doc.content:
		frappe.throw(_("Document {0} has no extracted text.").format(document))

	summary = summarize(
		doc.content,
		max_words=cint(max_words),
		reference_doctype="AI Document",
		reference_name=document,
	)
	if cint(save):
		doc.check_permission("write")
		doc.db_set("summary", summary)

	return {"document": document, "summary": summary}


@frappe.whitelist()
def classify_document(document: str, categories: str | list, save: bool = True) -> dict:
	"""Classify a document into one of the supplied categories."""
	from ai_fr_hg.ai.intelligence import classify

	doc = frappe.get_doc("AI Document", document)
	doc.check_permission("read")

	if isinstance(categories, str):
		try:
			categories = json.loads(categories)
		except ValueError:
			categories = [c.strip() for c in categories.split(",") if c.strip()]

	result = classify(
		doc.content or "",
		categories=categories,
		reference_doctype="AI Document",
		reference_name=document,
	)

	if cint(save) and result.get("category"):
		doc.check_permission("write")
		doc.db_set("document_type", result["category"])
		doc.db_set("confidence", result.get("confidence") or 0)
		if not any(row.tag == result["category"] for row in doc.tags):
			doc.append("tags", {"tag": result["category"], "source": "AI", "score": result.get("confidence")})
			doc.save(ignore_permissions=True)

	return {"document": document, **result}


@frappe.whitelist()
def extract_document_data(document: str, schema: str, save: bool = True) -> dict:
	"""Extract structured data from a document using an extraction schema."""
	from ai_fr_hg.ai.intelligence import extract_data

	doc = frappe.get_doc("AI Document", document)
	doc.check_permission("read")

	data = extract_data(
		doc.content or "",
		schema=schema,
		reference_doctype="AI Document",
		reference_name=document,
	)

	if cint(save):
		doc.check_permission("write")
		doc.db_set("extracted_data", frappe.as_json(data))
		doc.db_set("extraction_schema", schema)

	return {"document": document, "schema": schema, "data": data}


@frappe.whitelist()
def compare(document_a: str, document_b: str, instructions: str = "") -> dict:
	"""Compare two documents."""
	from ai_fr_hg.ai.intelligence import compare_documents

	return compare_documents(document_a, document_b, instructions=instructions)


@frappe.whitelist()
def get_document_chunks(document: str, limit: int = 100) -> list:
	"""List a document's chunks with their embedding status."""
	frappe.has_permission("AI Document", "read", doc=document, throw=True)

	return frappe.get_all(
		"AI Document Chunk",
		filters={"document": document},
		fields=[
			"name",
			"chunk_index",
			"heading",
			"content",
			"character_count",
			"token_count",
			"page_number",
			"embedding_model",
			"embedded_on",
		],
		order_by="chunk_index asc",
		limit_page_length=cint(limit) or 100,
	)


@frappe.whitelist()
def get_knowledge_overview() -> dict:
	"""Summary counters for the knowledge dashboard."""
	from ai_fr_hg.ai.knowledge import get_accessible_knowledge_bases

	accessible = get_accessible_knowledge_bases()
	bases = frappe.get_all(
		"AI Knowledge Base",
		filters={"name": ["in", accessible or [""]]},
		fields=[
			"name",
			"knowledge_base_name",
			"document_count",
			"chunk_count",
			"embedded_chunk_count",
			"total_characters",
			"index_status",
			"last_indexed_on",
			"embedding_model",
			"enabled",
		],
		order_by="knowledge_base_name asc",
	)

	return {
		"knowledge_bases": bases,
		"totals": {
			"documents": sum(cint(b.document_count) for b in bases),
			"chunks": sum(cint(b.chunk_count) for b in bases),
			"embedded": sum(cint(b.embedded_chunk_count) for b in bases),
			"characters": sum(cint(b.total_characters) for b in bases),
		},
		"recent_documents": frappe.get_list(
			"AI Document",
			filters={"knowledge_base": ["in", accessible or [""]]},
			fields=["name", "title", "status", "knowledge_base", "chunk_count", "modified"],
			order_by="modified desc",
			limit_page_length=10,
		),
		"failed_documents": frappe.get_list(
			"AI Document",
			filters={"status": "Failed", "knowledge_base": ["in", accessible or [""]]},
			fields=["name", "title", "error_message", "knowledge_base"],
			limit_page_length=10,
		),
	}


@frappe.whitelist()
def get_supported_formats() -> dict:
	"""List the file extensions the platform can ingest."""
	from ai_fr_hg.ai.readers import get_readers

	readers = get_readers()
	grouped: dict = {}
	for extension, reader_class in readers.items():
		grouped.setdefault(reader_class.label, []).append(extension)

	return {
		"extensions": sorted(readers),
		"by_reader": {label: sorted(exts) for label, exts in grouped.items()},
	}
