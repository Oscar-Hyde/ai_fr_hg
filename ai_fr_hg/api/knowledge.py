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
	folder: str | None = None,
	file_record: str | None = None,
) -> dict:
	"""Ingest an uploaded file into a knowledge base (folder-aware)."""
	from ai_fr_hg.ai.ingestion import ingest_file

	frappe.has_permission("AI Document", "create", throw=True)

	document = ingest_file(
		file_url=file_url,
		knowledge_base=knowledge_base,
		title=title,
		extraction_schema=extraction_schema,
		enqueue_job=not cint(process_now),
		folder=folder,
		file_record=file_record,
	)
	return {
		"document": document,
		"status": frappe.db.get_value("AI Document", document, "status"),
		"folder": frappe.db.get_value("AI Document", document, "folder"),
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
	"""Re-run extraction and indexing through the document's governed action."""
	# `force` is retained for API compatibility; reprocessing always rebuilds
	# derived chunks so stale embeddings cannot survive a source change.
	return frappe.get_doc("AI Document", document).reprocess()


@frappe.whitelist()
def reindex_knowledge_base(knowledge_base: str) -> dict:
	"""Queue every document in a knowledge base for reprocessing."""
	frappe.only_for(["AI Manager", "System Manager"])

	documents = frappe.get_all(
		"AI Document",
		filters={
			"knowledge_base": knowledge_base,
			"status": ["in", ["Draft", "Failed", "Indexed"]],
		},
		pluck="name",
	)
	queued = 0
	for document in documents:
		frappe.get_doc("AI Document", document).reprocess()
		queued += 1

	frappe.db.set_value("AI Knowledge Base", knowledge_base, "index_status", "Indexing")
	from ai_fr_hg.ai.logging import write_audit_log

	write_audit_log(
		action="Knowledge Base Reindex Queued",
		category="Execution",
		message=_("Queued {0} documents for reindexing in {1}.").format(queued, knowledge_base),
		details={"queued_documents": queued},
		reference_doctype="AI Knowledge Base",
		reference_name=knowledge_base,
		raise_on_error=True,
	)
	return {"knowledge_base": knowledge_base, "queued": queued}


@frappe.whitelist()
def search(
	query: str,
	knowledge_bases: str | list | None = None,
	top_k: int = 10,
	search_type: str | None = None,
	folder: str | None = None,
) -> dict:
	"""Search the knowledge base and return ranked passages (folder-scoped if provided)."""
	from ai_fr_hg.ai.knowledge import retrieve
	from ai_fr_hg.utils import api_validation

	query = api_validation.bounded_text(query, label=_("Query"), max_length=4_000, required=True)
	knowledge_bases = api_validation.bounded_list(
		knowledge_bases, label=_("Knowledge bases"), max_items=api_validation.MAX_KNOWLEDGE_BASES_PER_REQUEST
	)
	top_k = api_validation.bounded_integer(
		top_k, label=_("top_k"), default=10, maximum=api_validation.MAX_TOP_K
	)
	search_type = api_validation.enum_choice(
		search_type, allowed=("Hybrid", "Semantic", "Keyword"), label=_("Search type")
	)
	folder = api_validation.valid_identifier(folder, label=_("Folder")) if folder else None

	results = retrieve(
		query,
		knowledge_bases=knowledge_bases,
		top_k=top_k,
		search_type=search_type,
		folder=folder,
	)
	return {
		"query": query,
		"count": len(results),
		"results": [r.as_dict() for r in results],
		"folder": folder,
	}


@frappe.whitelist()
def ask(
	question: str,
	knowledge_bases: str | list | None = None,
	agent: str | None = None,
	model: str | None = None,
	documents: str | list | None = None,
	folder: str | None = None,
) -> dict:
	"""One-shot grounded question answering, without creating a conversation.

	`documents` scopes the answer to specific `AI Document` records (e.g. the
	"Ask About This" button). Fresh uploads are extracted inline so the answer
	does not wait for background embedding. `folder` scopes retrieval to a
	folder subtree.
	"""
	from ai_fr_hg.ai.agent import run_agent_turn
	from ai_fr_hg.ai.deadline import turn_budget
	from ai_fr_hg.ai.ingestion import prepare_documents_for_turn
	from ai_fr_hg.api.chat import _coerce_documents, _get_turn_budget
	from ai_fr_hg.utils import api_validation

	question = api_validation.bounded_text(question, label=_("Question"), max_length=32_000, required=True)
	knowledge_bases = api_validation.bounded_list(
		knowledge_bases, label=_("Knowledge bases"), max_items=api_validation.MAX_KNOWLEDGE_BASES_PER_REQUEST
	)
	documents = api_validation.bounded_list(
		documents, label=_("Documents"), max_items=api_validation.MAX_DOCUMENTS_PER_TURN
	)
	documents = _coerce_documents(documents)
	folder = api_validation.valid_identifier(folder, label=_("Folder")) if folder else None

	# Folder-scoped ask: if folder is provided and no explicit documents, resolve folder documents
	folder_docs = None
	if folder and not documents:
		try:
			from ai_fr_hg.ai.folders import _normalize_folder_path

			norm = _normalize_folder_path(folder)
			folder_docs = frappe.get_all(
				"AI Document", filters={"folder": ["like", f"{norm}%"]}, pluck="name"
			)
			if not folder_docs:
				folder_docs = frappe.get_all(
					"AI Document", filters={"source_folder": ["like", f"{norm}%"]}, pluck="name"
				)
			if folder_docs:
				documents = folder_docs
		except Exception:
			pass

	# Interactive, so it honours the same optional turn budget as chat.
	with turn_budget(_get_turn_budget()):
		extra_context = None
		if documents:
			documents, extra_context = prepare_documents_for_turn(documents)
		return run_agent_turn(
			question,
			agent=agent,
			knowledge_bases=knowledge_bases,
			model=model,
			include_history=False,
			save_messages=False,
			documents=documents or None,
			extra_context=extra_context or None,
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
	from ai_fr_hg.utils import api_validation

	document = api_validation.valid_identifier(document, label=_("Document"), required=True)
	limit = api_validation.bounded_integer(
		limit, label=_("limit"), default=100, maximum=api_validation.MAX_CHUNK_ENTITY_PAGE
	)
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
def scan_pattern_entities(document: str) -> dict:
	"""Extract high-precision pattern entities from a document's stored content.

	An enhancement layer over the existing pipeline: it only reads the
	document's already-extracted ``content`` and writes its own
	``AI Pattern Entity`` rows. Like the other document intelligence actions,
	it requires write access to the document.
	"""
	from ai_fr_hg.ai.patterns import scan_document

	doc = frappe.get_doc("AI Document", document)
	doc.check_permission("read")
	doc.check_permission("write")

	if not (doc.content or "").strip():
		frappe.throw(_("Document {0} has no extracted content to scan.").format(document))

	return scan_document(document)


@frappe.whitelist()
def get_pattern_entities(document: str, entity_type: str | None = None, limit: int = 200) -> dict:
	"""List a document's pattern entities, grouped and counted by type."""
	from ai_fr_hg.utils import api_validation

	document = api_validation.valid_identifier(document, label=_("Document"), required=True)
	limit = api_validation.bounded_integer(
		limit, label=_("limit"), default=200, maximum=api_validation.MAX_CHUNK_ENTITY_PAGE
	)
	frappe.has_permission("AI Document", "read", doc=document, throw=True)

	filters = {"document": document}
	if entity_type:
		filters["entity_type"] = entity_type

	entities = frappe.get_all(
		"AI Pattern Entity",
		filters=filters,
		fields=[
			"name",
			"entity_type",
			"value",
			"normalized_value",
			"occurrences",
			"first_offset",
			"context_quote",
		],
		order_by="occurrences desc, entity_type asc",
		limit_page_length=max(1, cint(limit) or 200),
	)

	# Aggregates must use the dict form: SQL function strings are rejected by
	# the query engine's field validation.
	counts = frappe.get_all(
		"AI Pattern Entity",
		filters={"document": document},
		fields=["entity_type", {"COUNT": "*", "as": "total"}],
		group_by="entity_type",
		order_by="total desc",
	)

	return {
		"document": document,
		"entities": entities,
		"entity_counts": {row.entity_type: cint(row.total) for row in counts},
	}


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
