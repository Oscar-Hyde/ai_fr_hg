# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Knowledge indexing.

Indexing turns extracted document text into embedded chunks. Retrieval
orchestration lives in ``ai.retrieval`` and is re-exported here so existing
``ai.knowledge.retrieve`` imports keep working.
"""

import math
import time
from numbers import Real

import frappe
from frappe import _
from frappe.utils import cint, now_datetime

from ai_fr_hg.ai.chunking import chunk_text
from ai_fr_hg.ai.engine import resolve_model, run_embedding
from ai_fr_hg.ai.exceptions import DocumentProcessingError
from ai_fr_hg.ai.vector import encode_vector, normalize
from ai_fr_hg.utils.db import safe_set_value

#: Embedding batch size, kept modest so local runtimes stay responsive.
EMBED_BATCH_SIZE = 16


# ---------------------------------------------------------------------------
# Indexing
# ---------------------------------------------------------------------------


def index_document(document: str, force: bool = False, embed: bool = True) -> dict:
	"""Build an `AI Document` chunk index and optionally embed new chunks."""
	doc = frappe.get_doc("AI Document", document)
	if not doc.content or not doc.content.strip():
		frappe.throw(_("Document {0} has no extracted text to index.").format(document))

	kb = frappe.get_cached_doc("AI Knowledge Base", doc.knowledge_base)
	settings = frappe.get_cached_doc("AI Platform Settings")

	chunk_size = cint(kb.chunk_size) or cint(settings.default_chunk_size) or 1200
	chunk_overlap = cint(kb.chunk_overlap) or cint(settings.default_chunk_overlap) or 150

	started = time.monotonic()
	doc.db_set("status", "Chunking", update_modified=False)

	chunks = chunk_text(doc.content, chunk_size=chunk_size, chunk_overlap=chunk_overlap)
	if not chunks:
		doc.db_set("status", "Failed", update_modified=False)
		doc.db_set("error_message", "Chunking produced no content.", update_modified=False)
		return {"document": document, "chunks": 0, "embedded": 0}

	existing_rows = frappe.get_all(
		"AI Document Chunk",
		filters={"document": document},
		fields=[
			"name",
			"checksum",
			"chunk_index",
			"embedding",
			"embedding_model",
			"embedding_dimensions",
			"embedding_format",
		],
	)
	# A document can legitimately contain identical chunk text at different
	# positions. Chunk index is therefore part of the identity; checksum alone
	# would silently collapse repeated passages and leave them unembedded.
	existing = {(row.checksum, cint(row.chunk_index)): row for row in existing_rows}
	incoming = {(chunk.checksum, chunk.index) for chunk in chunks}

	# Remove chunks that no longer exist in the re-processed document.
	for key, row in existing.items():
		if force or key not in incoming:
			frappe.delete_doc("AI Document Chunk", row.name, force=True, ignore_permissions=True)
	if force:
		existing = {}

	model = None
	expected_embedding_dimensions = 0
	if embed:
		model_doc = resolve_model(kb.embedding_model or settings.default_embedding_model, "Embedding")
		model = model_doc.name
		expected_embedding_dimensions = cint(model_doc.embedding_dimensions)
		doc.db_set("status", "Embedding", update_modified=False)

	created = 0
	embedded = 0
	pending_names = [
		row.name
		for key, row in existing.items()
		if embed
		and key in incoming
		and (
			not row.embedding
			or (bool(model) and row.embedding_model != model)
			or not row.embedding_format
			or (
				expected_embedding_dimensions
				and cint(row.embedding_dimensions) != expected_embedding_dimensions
			)
		)
	]

	for chunk in chunks:
		chunk_key = (chunk.checksum, chunk.index)
		if chunk_key in existing:
			continue  # unchanged; stale/missing embeddings are queued above
		row = frappe.new_doc("AI Document Chunk")
		row.update(
			{
				"document": document,
				"knowledge_base": doc.knowledge_base,
				"chunk_index": chunk.index,
				"heading": (chunk.heading or "")[:140] or None,
				"content": chunk.content,
				"character_count": chunk.character_count,
				"token_count": chunk.token_count,
				"page_number": chunk.page_number,
				"checksum": chunk.checksum,
			}
		)
		row.flags.ignore_permissions = True
		row.insert(ignore_permissions=True)
		created += 1
		pending_names.append(row.name)

	if embed and pending_names:
		embedded = embed_chunks(pending_names, model=model)

	total_chunks = frappe.db.count("AI Document Chunk", {"document": document})
	total_embedded = frappe.db.count("AI Document Chunk", {"document": document, "embedding": ["!=", ""]})

	doc.db_set(
		{
			"status": "Indexed",
			"chunk_count": total_chunks,
			"embedded_chunk_count": total_embedded,
			"indexed_on": now_datetime(),
			"processing_duration_ms": int((time.monotonic() - started) * 1000),
			"error_message": None,
		},
		update_modified=False,
	)
	update_knowledge_base_stats(doc.knowledge_base)

	return {
		"document": document,
		"chunks": total_chunks,
		"created": created,
		"embedded": embedded,
	}


def embed_chunks(chunk_names: list[str], model: str | None = None) -> int:
	"""Embed every requested readable chunk or raise a typed, traceable error."""
	if not chunk_names:
		return 0
	model_doc = resolve_model(model, "Embedding")
	model = model_doc.name
	expected_dimensions = cint(model_doc.embedding_dimensions)

	# Preserve caller order so provider vectors can be paired deterministically.
	requested = list(dict.fromkeys(chunk_names))
	embedded = 0
	knowledge_bases: set[str] = set()
	documents: set[str] = set()
	for start in range(0, len(requested), EMBED_BATCH_SIZE):
		batch = requested[start : start + EMBED_BATCH_SIZE]
		found = frappe.get_all(
			"AI Document Chunk",
			filters={"name": ["in", batch]},
			fields=["name", "content", "document", "knowledge_base"],
		)
		by_name = {row.name: row for row in found}
		missing = [name for name in batch if name not in by_name]
		if missing:
			raise DocumentProcessingError(
				_("Embedding was requested for missing chunks: {0}").format(", ".join(missing))
			)
		rows = [by_name[name] for name in batch]
		if any(not (row.content or "").strip() for row in rows):
			raise DocumentProcessingError(_("One or more requested chunks contain no readable text."))

		try:
			vectors = run_embedding(
				[row.content for row in rows],
				model=model,
				operation="Embedding",
				reference_doctype="AI Document",
				reference_name=rows[0].document if len({row.document for row in rows}) == 1 else None,
			)
		except Exception as exc:
			frappe.log_error(
				title="AI embedding batch failed",
				message=f"{exc}\n\nChunks: {[row.name for row in rows]}",
			)
			raise

		if not isinstance(vectors, (list, tuple)):
			raise DocumentProcessingError(_("The embedding provider returned a malformed response."))
		if len(vectors) != len(rows):
			raise DocumentProcessingError(
				_("The embedding provider returned {0} vectors for {1} chunks.").format(
					len(vectors), len(rows)
				)
			)
		validated: list[tuple[object, list[float]]] = []
		for row, vector in zip(rows, vectors, strict=True):
			if not isinstance(vector, (list, tuple)) or not vector:
				raise DocumentProcessingError(
					_("The embedding provider returned an empty vector for chunk {0}.").format(row.name)
				)
			if any(isinstance(value, bool) or not isinstance(value, Real) for value in vector):
				raise DocumentProcessingError(
					_("The embedding provider returned a non-numeric vector for chunk {0}.").format(row.name)
				)
			numeric = [float(value) for value in vector]
			if not all(math.isfinite(value) for value in numeric) or not any(numeric):
				raise DocumentProcessingError(
					_("The embedding provider returned an invalid vector for chunk {0}.").format(row.name)
				)
			if expected_dimensions and len(numeric) != expected_dimensions:
				raise DocumentProcessingError(
					_("The embedding provider returned {0} dimensions for chunk {1}; expected {2}.").format(
						len(numeric), row.name, expected_dimensions
					)
				)
			unit = normalize(numeric)
			if not unit or not all(math.isfinite(value) for value in unit) or not any(unit):
				raise DocumentProcessingError(
					_("The embedding provider returned an unnormalizable vector for chunk {0}.").format(
						row.name
					)
				)
			expected_dimensions = expected_dimensions or len(unit)
			validated.append((row, unit))

		# Persist only after the entire provider batch has passed validation, so a
		# malformed later vector cannot leave a partially embedded batch behind.
		for row, unit in validated:
			safe_set_value(
				"AI Document Chunk",
				row.name,
				{
					"embedding": encode_vector(unit),
					"embedding_model": model,
					"embedding_dimensions": len(unit),
					"embedding_norm": 1.0,
					"embedding_format": "Base64 Float32",
					"embedded_on": now_datetime(),
				},
				update_modified=False,
			)
			knowledge_bases.add(row.knowledge_base)
			documents.add(row.document)
			embedded += 1

	for document in documents:
		frappe.db.set_value(
			"AI Document",
			document,
			"embedded_chunk_count",
			frappe.db.count("AI Document Chunk", {"document": document, "embedding": ["!=", ""]}),
			update_modified=False,
		)
	for knowledge_base in knowledge_bases:
		update_knowledge_base_stats(knowledge_base)
	return embedded


def update_knowledge_base_stats(knowledge_base: str) -> None:
	"""Refresh the denormalised counters shown on the knowledge base record."""
	stats = frappe.db.sql(
		"""
		select
			count(*) as chunk_count,
			coalesce(sum(case when embedding is not null and embedding != '' then 1 else 0 end), 0)
				as embedded_count,
			coalesce(sum(character_count), 0) as characters
		from `tabAI Document Chunk`
		where knowledge_base = %s
		""",
		(knowledge_base,),
		as_dict=True,
	)[0]

	document_count = frappe.db.count(
		"AI Document", {"knowledge_base": knowledge_base, "status": ["!=", "Archived"]}
	)

	frappe.db.set_value(
		"AI Knowledge Base",
		knowledge_base,
		{
			"document_count": document_count,
			"chunk_count": cint(stats.chunk_count),
			"embedded_chunk_count": cint(stats.embedded_count),
			"total_characters": cint(stats.characters),
			"last_indexed_on": now_datetime(),
			"index_status": "Idle" if cint(stats.chunk_count) == cint(stats.embedded_count) else "Stale",
		},
		update_modified=False,
	)


# ---------------------------------------------------------------------------
# Retrieval
# ---------------------------------------------------------------------------


def get_accessible_knowledge_bases(user: str | None = None) -> list[str]:
	"""Knowledge bases the user may read, honouring role restrictions."""
	user = user or frappe.session.user
	if set(frappe.get_roles(user)).intersection({"System Manager", "AI Manager"}) or user == "Administrator":
		return frappe.get_all("AI Knowledge Base", filters={"enabled": 1}, pluck="name")

	roles = set(frappe.get_roles(user))
	accessible = []
	for kb in frappe.get_all("AI Knowledge Base", filters={"enabled": 1}, fields=["name", "is_public"]):
		if kb.is_public:
			accessible.append(kb.name)
			continue
		allowed = frappe.get_all("AI Knowledge Base Role", filters={"parent": kb.name}, pluck="role")
		if roles.intersection(allowed):
			accessible.append(kb.name)
	return accessible


def _log_search_job(*args, **kwargs):
	"""Queued-job alias so in-flight search telemetry jobs keep resolving."""
	from ai_fr_hg.ai.retrieval import _log_search_job as impl

	return impl(*args, **kwargs)
