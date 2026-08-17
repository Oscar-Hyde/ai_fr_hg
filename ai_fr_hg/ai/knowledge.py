# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Knowledge indexing and retrieval.

Indexing turns extracted document text into embedded chunks. Retrieval combines
dense vector similarity with keyword matching (reciprocal rank fusion), which
is materially more reliable than either signal alone for enterprise content
full of identifiers, part numbers and proper nouns.
"""

import re
import time
from dataclasses import dataclass

import frappe
from frappe import _
from frappe.utils import cint, flt, now_datetime

from ai_fr_hg.ai.chunking import chunk_text
from ai_fr_hg.ai.engine import run_embedding
from ai_fr_hg.ai.vector import decode_vector, encode_vector, normalize, rank
from ai_fr_hg.utils.db import safe_set_value

WORD = re.compile(r"[\w\-/.]{2,}")
#: Embedding batch size, kept modest so local runtimes stay responsive.
EMBED_BATCH_SIZE = 16


@dataclass
class RetrievedChunk:
	"""A chunk returned by retrieval, with provenance for citation."""

	chunk: str
	document: str
	document_title: str
	knowledge_base: str
	content: str
	score: float = 0.0
	semantic_score: float = 0.0
	keyword_score: float = 0.0
	heading: str | None = None
	page_number: int = 0

	def as_dict(self) -> dict:
		return {
			"chunk": self.chunk,
			"document": self.document,
			"document_title": self.document_title,
			"knowledge_base": self.knowledge_base,
			"content": self.content,
			"score": round(self.score, 4),
			"semantic_score": round(self.semantic_score, 4),
			"keyword_score": round(self.keyword_score, 4),
			"heading": self.heading,
			"page_number": self.page_number,
		}


# ---------------------------------------------------------------------------
# Indexing
# ---------------------------------------------------------------------------


def index_document(document: str, force: bool = False) -> dict:
	"""Chunk and embed an `AI Document`. Returns a summary of the work done."""
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

	existing = {
		row.checksum: row.name
		for row in frappe.get_all(
			"AI Document Chunk", filters={"document": document}, fields=["name", "checksum"]
		)
	}
	incoming = {chunk.checksum for chunk in chunks}

	# Remove chunks that no longer exist in the re-processed document.
	for checksum, name in existing.items():
		if force or checksum not in incoming:
			frappe.delete_doc("AI Document Chunk", name, force=True, ignore_permissions=True)
	if force:
		existing = {}

	model = kb.embedding_model or settings.default_embedding_model
	doc.db_set("status", "Embedding", update_modified=False)

	created = 0
	embedded = 0
	pending: list = []

	for chunk in chunks:
		if chunk.checksum in existing:
			continue  # unchanged, keep its existing embedding
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
		pending.append(row)

	if model and pending:
		embedded = embed_chunks([row.name for row in pending], model=model)

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
	"""Embed the given chunks in batches. Returns how many were embedded."""
	if not chunk_names:
		return 0

	embedded = 0
	for start in range(0, len(chunk_names), EMBED_BATCH_SIZE):
		batch = chunk_names[start : start + EMBED_BATCH_SIZE]
		rows = frappe.get_all(
			"AI Document Chunk", filters={"name": ["in", batch]}, fields=["name", "content"]
		)
		rows = [row for row in rows if (row.content or "").strip()]
		if not rows:
			continue

		try:
			vectors = run_embedding([row.content for row in rows], model=model, operation="Embedding")
		except Exception as exc:
			frappe.log_error(
				title="AI embedding batch failed",
				message=f"{exc}\n\nChunks: {[row.name for row in rows]}",
			)
			continue

		embedding_model_name = model or frappe.db.get_single_value(
			"AI Platform Settings", "default_embedding_model"
		)
		for row, vector in zip(rows, vectors, strict=False):
			if not vector:
				continue
			unit = normalize(vector)
			safe_set_value(
				"AI Document Chunk",
				row.name,
				{
					"embedding": encode_vector(unit),
					"embedding_model": embedding_model_name,
					"embedding_dimensions": len(unit),
					"embedding_norm": 1.0,
					"embedding_format": "Base64 Float32",
					"embedded_on": now_datetime(),
				},
				update_modified=False,
			)
			embedded += 1

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
	if "System Manager" in frappe.get_roles(user) or user == "Administrator":
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


def keyword_search(
	query: str, knowledge_bases: list[str], limit: int = 50, documents: list[str] | None = None
) -> dict[str, float]:
	"""Score chunks by keyword overlap using a LIKE-based match.

	Deliberately dependency-free so the platform works on any MariaDB or
	Postgres install without a full-text index. When `documents` is given, only
	chunks belonging to those documents are considered.
	"""
	terms = [term.lower() for term in WORD.findall(query or "")][:8]
	if not terms or not knowledge_bases:
		return {}

	conditions = " or ".join(["lower(content) like %s"] * len(terms))
	placeholders = ", ".join(["%s"] * len(knowledge_bases))
	values = [f"%{term}%" for term in terms] + list(knowledge_bases)

	doc_clause = ""
	if documents:
		doc_placeholders = ", ".join(["%s"] * len(documents))
		doc_clause = f" and document in ({doc_placeholders})"
		values += list(documents)

	rows = frappe.db.sql(
		f"""
		select name, content
		from `tabAI Document Chunk`
		where ({conditions}) and knowledge_base in ({placeholders}){doc_clause}
		limit {cint(limit)}
		""",  # nosemgrep: frappe-manual-commit
		values,
		as_dict=True,
	)

	scores: dict[str, float] = {}
	for row in rows:
		content = (row.content or "").lower()
		hits = sum(content.count(term) for term in terms)
		distinct = sum(1 for term in terms if term in content)
		if hits:
			# Reward covering many distinct query terms over repeating one.
			scores[row.name] = (distinct / len(terms)) * 0.7 + min(hits / 20, 1.0) * 0.3
	return scores


def semantic_search(
	query: str,
	knowledge_bases: list[str],
	top_k: int = 10,
	model: str | None = None,
	documents: list[str] | None = None,
) -> dict[str, float]:
	"""Score chunks by cosine similarity against the query embedding."""
	if not knowledge_bases:
		return {}

	vectors = run_embedding([query], model=model, operation="Embedding")
	if not vectors or not vectors[0]:
		return {}
	query_vector = vectors[0]

	filters: dict = {"knowledge_base": ["in", knowledge_bases], "embedding": ["!=", ""]}
	if documents:
		filters["document"] = ["in", documents]

	rows = frappe.get_all(
		"AI Document Chunk",
		filters=filters,
		fields=["name", "embedding"],
		limit_page_length=max(top_k * 20, 200),
	)
	candidates = [(row.name, decode_vector(row.embedding)) for row in rows]
	candidates = [(name, vector) for name, vector in candidates if vector]

	return dict(rank(query_vector, candidates, top_k=top_k * 4))


def retrieve(
	query: str,
	knowledge_bases: list[str] | None = None,
	top_k: int | None = None,
	search_type: str | None = None,
	similarity_threshold: float | None = None,
	model: str | None = None,
	log: bool = True,
	documents: list[str] | None = None,
) -> list[RetrievedChunk]:
	"""Retrieve the most relevant chunks for `query`.

	Hybrid mode fuses dense and keyword rankings with reciprocal rank fusion,
	so a chunk that ranks well on either signal surfaces.

	When `documents` is supplied, retrieval is scoped to those `AI Document`
	records: only their chunks are ranked and only their knowledge bases are
	considered. This is how "answer from the file I just uploaded" is grounded
	solely in the new upload instead of the whole knowledge base.
	"""
	started = time.monotonic()
	settings = frappe.get_cached_doc("AI Platform Settings")

	accessible = set(get_accessible_knowledge_bases())

	if documents:
		# The uploaded files are the source of truth; restrict targets to the
		# knowledge bases they actually live in (and the caller can read).
		doc_kbs = {
			row.knowledge_base
			for row in frappe.get_all(
				"AI Document", filters={"name": ["in", documents]}, fields=["knowledge_base"]
			)
		}
		targets = [kb for kb in doc_kbs if kb in accessible]
	else:
		if knowledge_bases:
			targets = [kb for kb in knowledge_bases if kb in accessible]
		else:
			targets = list(accessible)

	if not targets or not (query or "").strip():
		return []

	# Do not pay an embedding round-trip for empty knowledge bases.
	populated_filters: dict = {"knowledge_base": ["in", targets]}
	if documents:
		populated_filters["document"] = ["in", documents]
	populated = [
		row.knowledge_base
		for row in frappe.get_all(
			"AI Document Chunk",
			filters=populated_filters,
			fields=["knowledge_base"],
			distinct=True,
			limit_page_length=len(targets),
		)
	]
	targets = [kb for kb in targets if kb in set(populated)]
	if not targets:
		return []

	top_k = cint(top_k) or cint(settings.default_top_k) or 6
	search_type = search_type or ("Hybrid" if settings.enable_hybrid_search else "Semantic")
	threshold = (
		flt(similarity_threshold) if similarity_threshold is not None else flt(settings.similarity_threshold)
	)

	semantic: dict[str, float] = {}
	keyword: dict[str, float] = {}

	if search_type in ("Semantic", "Hybrid"):
		try:
			semantic = semantic_search(query, targets, top_k=top_k, model=model, documents=documents)
		except Exception as exc:
			frappe.log_error(title="AI semantic search failed", message=str(exc))
			if search_type == "Semantic":
				raise
	if search_type in ("Keyword", "Hybrid"):
		keyword = keyword_search(query, targets, documents=documents)

	fused = _fuse(semantic, keyword, search_type)
	if not fused:
		return []

	ordered = sorted(fused.items(), key=lambda row: row[1], reverse=True)
	if search_type != "Keyword":
		ordered = [(name, score) for name, score in ordered if semantic.get(name, 1.0) >= threshold]
	ordered = ordered[:top_k]

	results = _hydrate(ordered, semantic, keyword)

	# Search telemetry is useful for administrators, but it writes a row on
	# every chat turn. Queue it so retrieval latency is not dominated by an
	# insert and local models stay responsive.
	if log:
		_log_search(query, targets, search_type, results, started)
	return results


def _fuse(semantic: dict, keyword: dict, search_type: str) -> dict[str, float]:
	"""Reciprocal rank fusion of the two ranked lists."""
	if search_type == "Semantic":
		return dict(semantic)
	if search_type == "Keyword":
		return dict(keyword)

	K = 60  # RRF damping constant
	fused: dict[str, float] = {}

	for ranked in (semantic, keyword):
		ordered = sorted(ranked.items(), key=lambda row: row[1], reverse=True)
		for position, (name, _score) in enumerate(ordered):
			fused[name] = fused.get(name, 0.0) + 1 / (K + position + 1)
	return fused


def _hydrate(ordered, semantic, keyword) -> list[RetrievedChunk]:
	"""Load chunk bodies and document titles for the selected results."""
	names = [name for name, _ in ordered]
	if not names:
		return []

	rows = {
		row.name: row
		for row in frappe.get_all(
			"AI Document Chunk",
			filters={"name": ["in", names]},
			fields=[
				"name",
				"content",
				"document",
				"knowledge_base",
				"heading",
				"page_number",
			],
			limit_page_length=0,
		)
	}
	titles = {
		row.name: row.title
		for row in frappe.get_all(
			"AI Document",
			filters={"name": ["in", list({r.document for r in rows.values()})]},
			fields=["name", "title"],
		)
	}

	results = []
	for name, score in ordered:
		row = rows.get(name)
		if not row:
			continue
		results.append(
			RetrievedChunk(
				chunk=name,
				document=row.document,
				document_title=titles.get(row.document) or row.document,
				knowledge_base=row.knowledge_base,
				content=row.content,
				score=score,
				semantic_score=semantic.get(name, 0.0),
				keyword_score=keyword.get(name, 0.0),
				heading=row.heading,
				page_number=cint(row.page_number),
			)
		)
	return results


def _log_search(query, targets, search_type, results, started) -> None:
	try:
		frappe.enqueue(
			"ai_fr_hg.ai.knowledge._log_search_job",
			queue="short",
			timeout=120,
			job_id=f"ai_search_log:{frappe.generate_hash(length=10)}",
			query=query,
			targets=targets,
			search_type=search_type,
			results=[r.as_dict() for r in results[:10]],
			result_count=len(results),
			top_score=results[0].score if results else 0,
			duration_ms=int((time.monotonic() - started) * 1000),
			user=frappe.session.user,
		)
	except Exception:
		frappe.log_error(title="AI Search Query log failed", message=frappe.get_traceback())


def _log_search_job(query, targets, search_type, results, result_count, top_score, duration_ms, user) -> None:
	try:
		frappe.set_user(user)
		doc = frappe.new_doc("AI Search Query")
		doc.update(
			{
				"query": query[:1000],
				"knowledge_base": targets[0] if len(targets) == 1 else None,
				"user": frappe.session.user,
				"search_type": search_type,
				"result_count": result_count,
				"top_score": top_score,
				"duration_ms": duration_ms,
				"results": frappe.as_json(results),
			}
		)
		doc.flags.ignore_permissions = True
		doc.insert(ignore_permissions=True)
	except Exception:
		frappe.log_error(title="AI Search Query log failed", message=frappe.get_traceback())


def build_context(results: list[RetrievedChunk], max_characters: int | None = None) -> str:
	"""Render retrieved chunks into a numbered context block for the prompt."""
	if not results:
		return ""

	limit = (
		cint(max_characters)
		or cint(frappe.db.get_single_value("AI Platform Settings", "max_context_characters"))
		or 12000
	)

	parts: list[str] = []
	used = 0
	for position, result in enumerate(results, start=1):
		header = f"[{position}] {result.document_title}"
		if result.heading:
			header += f" - {result.heading}"
		if result.page_number:
			header += f" (page {result.page_number})"

		block = f"{header}\n{result.content}"
		if used + len(block) > limit:
			break
		parts.append(block)
		used += len(block)

	return "\n\n---\n\n".join(parts)
