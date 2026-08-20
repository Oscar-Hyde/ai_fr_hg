# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Canonical retrieval orchestration.

Indexing still lives in ``ai.knowledge``. This module is the single authority
for search: complete candidate evaluation, mixed embedding-model grouping,
per-knowledge-base policy, folder descendants, context packing and diagnostics.

Frappe v17 evaluated for this responsibility:

* ORM ``get_all`` / ``get_list`` — used for permission-aware document scope and
  keyset-paged chunk scans. Frappe has no vector index or FULLTEXT API.
* Query builder — used where filters are simple; MariaDB ``MATCH … AGAINST``
  is issued as parameterized SQL because Frappe has no FULLTEXT primitive.
* NestedSet — not applicable; embeddings are not a tree.
* MariaDB VECTOR type — would duplicate the Long Text embedding column and is
  not a Frappe field type. Phase 2 correctness is a complete brute-force scan
  with a documented latency envelope, not a second vector store.

Reranking remains intentionally unsupported (ADR-004).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import frappe
from frappe import _
from frappe.utils import cint, flt

from ai_fr_hg.ai import retrieval_utils as ru
from ai_fr_hg.ai.chunking import CHARS_PER_TOKEN, estimate_tokens
from ai_fr_hg.ai.language import language_name, resolve_document_language
from ai_fr_hg.ai.vector import decode_vector, score_pairs

#: Rows decoded per scan page. Bounds peak memory; correctness is the full walk.
SCAN_BATCH = 128
KEYWORD_PAGE = 250
FULLTEXT_POOL = 1000
DEFAULT_BRUTE_FORCE_CEILING = 10_000
CONTENT_FTS_INDEX = "content_fts"


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
	language: str | None = None
	token_count: int = 0
	embedding_model: str | None = None

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
			"language": self.language,
			"token_count": self.token_count,
			"embedding_model": self.embedding_model,
		}


@dataclass
class RetrievalDiagnostics:
	"""Observable retrieval decision record. Returned beside search results."""

	corpus_size: int = 0
	candidate_count: int = 0
	embedding_models: list[dict] = field(default_factory=list)
	retrieval_strategy: str = "brute_force_scan"
	search_type: str = "Hybrid"
	thresholds: dict = field(default_factory=dict)
	weights: dict = field(default_factory=dict)
	top_k: dict = field(default_factory=dict)
	fallback_reason: str | None = None
	degraded: bool = False
	degraded_reasons: list[str] = field(default_factory=list)
	stale_chunks_skipped: int = 0
	incompatible_chunks_skipped: int = 0
	brute_force_ceiling: int = DEFAULT_BRUTE_FORCE_CEILING
	folder: str | None = None
	mixed_embedding_models: bool = False
	keyword_backend: str = "like_scan"
	reranker: str = "unsupported"

	def as_dict(self) -> dict:
		return {
			"corpus_size": self.corpus_size,
			"candidate_count": self.candidate_count,
			"embedding_models": list(self.embedding_models),
			"retrieval_strategy": self.retrieval_strategy,
			"search_type": self.search_type,
			"thresholds": dict(self.thresholds),
			"weights": dict(self.weights),
			"top_k": dict(self.top_k),
			"fallback_reason": self.fallback_reason,
			"degraded": self.degraded,
			"degraded_reasons": list(self.degraded_reasons),
			"stale_chunks_skipped": self.stale_chunks_skipped,
			"incompatible_chunks_skipped": self.incompatible_chunks_skipped,
			"brute_force_ceiling": self.brute_force_ceiling,
			"folder": self.folder,
			"mixed_embedding_models": self.mixed_embedding_models,
			"keyword_backend": self.keyword_backend,
			"reranker": self.reranker,
		}


@dataclass
class RetrievalOutcome:
	chunks: list[RetrievedChunk] = field(default_factory=list)
	diagnostics: RetrievalDiagnostics = field(default_factory=RetrievalDiagnostics)
	total: int = 0


def _settings():
	return frappe.get_cached_doc("AI Platform Settings")


def _brute_force_ceiling(settings=None) -> int:
	settings = settings or _settings()
	return max(
		cint(getattr(settings, "retrieval_brute_force_ceiling", 0)) or DEFAULT_BRUTE_FORCE_CEILING, 200
	)


def _mark_degraded(diagnostics: RetrievalDiagnostics, reason: str) -> None:
	if reason not in diagnostics.degraded_reasons:
		diagnostics.degraded_reasons.append(reason)
	diagnostics.degraded = True
	if not diagnostics.fallback_reason:
		diagnostics.fallback_reason = reason


def _page_chunks(filters: dict, fields: list[str], *, batch: int = SCAN_BATCH):
	"""Keyset-iterate every matching chunk. No correctness cap."""
	cursor = None
	while True:
		page_filters = dict(filters)
		if cursor:
			page_filters["name"] = [">", cursor]
		rows = frappe.get_all(
			"AI Document Chunk",
			filters=page_filters,
			fields=fields,
			order_by="name asc",
			limit_page_length=batch,
		)
		if not rows:
			return
		cursor = rows[-1].name
		yield from rows


def _count_chunks(filters: dict) -> int:
	return cint(frappe.db.count("AI Document Chunk", filters)) or 0


def _load_kb_policies(names: list[str], settings) -> dict[str, dict]:
	"""Effective embedding model, dimensions, top_k and threshold per KB."""
	if not names:
		return {}
	rows = frappe.get_all(
		"AI Knowledge Base",
		filters={"name": ["in", names]},
		fields=["name", "embedding_model", "top_k", "similarity_threshold"],
	)
	platform_model = settings.default_embedding_model
	platform_top_k = cint(settings.default_top_k) or 6
	platform_threshold = flt(settings.similarity_threshold)
	policies: dict[str, dict] = {}
	for row in rows:
		model = row.embedding_model or platform_model
		dimensions = 0
		if model:
			dimensions = cint(frappe.db.get_value("AI Model", model, "embedding_dimensions")) or 0
		policies[row.name] = {
			"embedding_model": model,
			"embedding_dimensions": dimensions,
			"top_k": cint(row.top_k) or platform_top_k,
			"similarity_threshold": (
				flt(row.similarity_threshold) if row.similarity_threshold is not None else platform_threshold
			),
		}
	return policies


def _group_kbs_by_model(policies: dict[str, dict]) -> dict[tuple[str, int], list[str]]:
	groups: dict[tuple[str, int], list[str]] = {}
	for kb, policy in policies.items():
		model = policy.get("embedding_model") or ""
		dims = cint(policy.get("embedding_dimensions")) or 0
		groups.setdefault((model, dims), []).append(kb)
	return groups


def _resolve_folder_documents(folder: str) -> list[dict]:
	"""Documents in an exact folder or its descendants. Sibling prefixes excluded."""
	from ai_fr_hg.ai.folders import folder_match_or_filters

	or_filters = folder_match_or_filters(folder, ("folder", "source_folder"))
	# get_list is the permission-aware path; folder scope must not leak
	# documents the caller cannot read.
	return frappe.get_list(
		"AI Document",
		or_filters=or_filters,
		fields=["name", "knowledge_base"],
		limit_page_length=0,
	)


def _resolve_entity_documents(entity_type: str | None, entity_value: str | None) -> list[str]:
	filters: dict = {}
	if entity_type:
		filters["entity_type"] = entity_type
	if entity_value:
		filters["normalized_value"] = entity_value
	if not filters:
		return []
	rows = frappe.get_list(
		"AI Pattern Entity",
		filters=filters,
		fields=["document"],
		limit_page_length=5000,
	)
	return list(dict.fromkeys(row.document for row in rows if row.document))


def _has_content_fts() -> bool:
	"""True when the MariaDB FULLTEXT index from patch v0_0_16 exists."""
	try:
		cached = frappe.cache().get_value("ai_fr_hg:chunk_content_fts")
	except Exception:
		cached = None
	if cached is not None:
		return bool(int(cached))
	present = False
	try:
		if getattr(frappe.db, "db_type", None) == "postgres":
			present = False
		else:
			rows = frappe.db.sql(
				"SHOW INDEX FROM `tabAI Document Chunk` WHERE Key_name=%s",
				(CONTENT_FTS_INDEX,),
			)
			present = bool(rows)
	except Exception:
		present = False
	try:
		frappe.cache().set_value("ai_fr_hg:chunk_content_fts", int(present), expires_in_sec=3600)
	except Exception:
		pass
	return present


def keyword_search(
	query: str,
	knowledge_bases: list[str],
	limit: int = 50,
	documents: list[str] | None = None,
	*,
	diagnostics: RetrievalDiagnostics | None = None,
) -> dict[str, float]:
	"""Score every matching chunk. Completeness is not a 500-row cap.

	Uses MariaDB FULLTEXT when the index exists and every term is eligible.
	Otherwise pages every LIKE match. Identifiers, Arabic and Hebrew always
	take the LIKE completeness path because InnoDB FULLTEXT will drop them.
	"""
	terms = ru.tokenize_query(query)
	if not terms or not knowledge_bases:
		return {}

	filters: dict = {"knowledge_base": ["in", knowledge_bases]}
	if documents:
		filters["document"] = ["in", documents]

	pool = max(cint(limit) or 50, 1)
	if _has_content_fts() and all(ru.is_fulltext_term(term) for term in terms):
		scores = _keyword_fulltext(terms, filters, pool)
		if diagnostics is not None:
			diagnostics.keyword_backend = "fulltext"
		if scores:
			return scores
		if diagnostics is not None:
			_mark_degraded(diagnostics, "fulltext_empty_fell_back_to_like")

	if diagnostics is not None:
		diagnostics.keyword_backend = "like_scan"
	return _keyword_like_scan(terms, filters)


def _keyword_fulltext(terms: list[str], filters: dict, pool: int) -> dict[str, float]:
	"""Ranked FULLTEXT. Parameterized; identifiers in ``terms`` are already sanitized."""
	boolean = " ".join(f"+{term}*" for term in terms)
	where_clauses = ["MATCH(`content`) AGAINST (%s IN BOOLEAN MODE)"]
	where_values: list[Any] = [boolean]
	if filters.get("knowledge_base"):
		kbs = filters["knowledge_base"][1]
		placeholders = ", ".join(["%s"] * len(kbs))
		where_clauses.append(f"`knowledge_base` in ({placeholders})")
		where_values.extend(kbs)
	if filters.get("document"):
		docs = filters["document"][1]
		placeholders = ", ".join(["%s"] * len(docs))
		where_clauses.append(f"`document` in ({placeholders})")
		where_values.extend(docs)
	# SELECT MATCH, WHERE MATCH + filters, LIMIT — all values are placeholders.
	params = (boolean, *where_values, max(pool, FULLTEXT_POOL))
	rows = frappe.db.sql(  # nosemgrep
		f"""
		select `name`, MATCH(`content`) AGAINST (%s IN BOOLEAN MODE) as rel
		from `tabAI Document Chunk`
		where {" and ".join(where_clauses)}
		order by rel desc
		limit %s
		""",
		params,
		as_dict=True,
	)
	scores: dict[str, float] = {}
	for row in rows:
		rel = flt(row.rel)
		if rel > 0:
			scores[row.name] = float(rel)
	return scores


def _keyword_like_scan(terms: list[str], filters: dict) -> dict[str, float]:
	"""Page every LIKE match and score in Python. No 500-row correctness cap."""
	or_filters = [["content", "like", ru.like_pattern(term)] for term in terms]
	scores: dict[str, float] = {}
	cursor = None
	while True:
		page_filters = dict(filters)
		if cursor:
			page_filters["name"] = [">", cursor]
		rows = frappe.get_all(
			"AI Document Chunk",
			filters=page_filters,
			or_filters=or_filters,
			fields=["name", "content"],
			order_by="name asc",
			limit_page_length=KEYWORD_PAGE,
		)
		if not rows:
			break
		cursor = rows[-1].name
		for row in rows:
			score = ru.keyword_score(row.content or "", terms)
			if score:
				scores[row.name] = score
	return scores


def semantic_search(
	query: str,
	knowledge_bases: list[str],
	top_k: int = 10,
	model: str | None = None,
	documents: list[str] | None = None,
	*,
	policies: dict[str, dict] | None = None,
	diagnostics: RetrievalDiagnostics | None = None,
) -> dict[str, float]:
	"""Score every compatible embedded chunk. Mixed models are never compared."""
	if not knowledge_bases:
		return {}

	settings = _settings()
	policies = policies or _load_kb_policies(knowledge_bases, settings)
	groups = _group_kbs_by_model(policies)
	if model:
		# Explicit model override: only groups whose stored model matches.
		groups = {key: kbs for key, kbs in groups.items() if key[0] == model or not key[0]}
		if not groups:
			groups = {(model, 0): list(knowledge_bases)}

	all_scores: dict[str, float] = {}
	models_used: list[dict] = []
	stale = 0
	incompatible = 0
	scanned = 0

	for (group_model, group_dims), kbs in groups.items():
		embed_model = group_model or model or settings.default_embedding_model
		if not embed_model:
			incompatible += 1
			continue
		try:
			from ai_fr_hg.ai.engine import run_embedding

			vectors = run_embedding([query], model=embed_model, operation="Embedding")
		except Exception:
			if diagnostics is not None:
				_mark_degraded(diagnostics, "semantic_embedding_failed")
			continue
		if not vectors or not vectors[0]:
			continue
		query_vector = list(vectors[0])
		expected_dim = group_dims or len(query_vector)
		if expected_dim and len(query_vector) != expected_dim:
			# Stored chunks and the query embedding are different widths.
			incompatible += 1
			continue

		filters: dict = {
			"knowledge_base": ["in", kbs],
			"embedding": ["!=", ""],
		}
		if documents:
			filters["document"] = ["in", documents]

		group_scores, group_scanned, group_stale, group_incompatible = _scan_semantic_group(
			query_vector, filters, embed_model, expected_dim
		)
		all_scores.update(group_scores)
		scanned += group_scanned
		stale += group_stale
		incompatible += group_incompatible
		models_used.append(
			{
				"model": embed_model,
				"dimensions": expected_dim,
				"knowledge_bases": list(kbs),
				"chunk_count": group_scanned,
			}
		)

	if diagnostics is not None:
		diagnostics.embedding_models = models_used
		diagnostics.stale_chunks_skipped += stale
		diagnostics.incompatible_chunks_skipped += incompatible
		diagnostics.mixed_embedding_models = len(models_used) > 1
		diagnostics.corpus_size = max(diagnostics.corpus_size, scanned)
		ceiling = diagnostics.brute_force_ceiling
		if scanned > ceiling:
			_mark_degraded(diagnostics, "corpus_exceeds_brute_force_ceiling")

	return all_scores


def _scan_semantic_group(
	query_vector: list[float],
	filters: dict,
	expected_model: str,
	expected_dim: int,
) -> tuple[dict[str, float], int, int, int]:
	"""Page every embedded row in ``filters`` and score compatible vectors."""
	scores: dict[str, float] = {}
	scanned = 0
	stale = 0
	incompatible = 0
	fields = ["name", "embedding", "embedding_model", "embedding_dimensions"]
	for row in _page_chunks(filters, fields, batch=SCAN_BATCH):
		scanned += 1
		stored_model = row.embedding_model or ""
		if stored_model and expected_model and stored_model != expected_model:
			stale += 1
			continue
		stored_dim = cint(row.embedding_dimensions)
		if expected_dim and stored_dim and stored_dim != expected_dim:
			stale += 1
			continue
		vector = decode_vector(row.embedding)
		if not vector:
			incompatible += 1
			continue
		if expected_dim and len(vector) != expected_dim:
			incompatible += 1
			continue
		scored = score_pairs(query_vector, [(row.name, vector)])
		if scored:
			scores[row.name] = scored[0][1]
		else:
			incompatible += 1
	return scores, scanned, stale, incompatible


def retrieve(
	query: str,
	knowledge_bases: list[str] | None = None,
	top_k: int | None = None,
	search_type: str | None = None,
	similarity_threshold: float | None = None,
	model: str | None = None,
	log: bool = True,
	documents: list[str] | None = None,
	folder: str | None = None,
	*,
	weights: dict[str, float] | None = None,
	entity_type: str | None = None,
	entity_value: str | None = None,
	offset: int = 0,
	with_diagnostics: bool = False,
) -> list[RetrievedChunk] | tuple[list[RetrievedChunk], dict]:
	"""Retrieve the most relevant chunks for ``query``.

	When ``with_diagnostics`` is true, returns ``(chunks, diagnostics_dict)``.
	Otherwise returns the chunk list, preserving the historical contract.
	"""
	outcome = run_retrieval(
		query,
		knowledge_bases=knowledge_bases,
		top_k=top_k,
		search_type=search_type,
		similarity_threshold=similarity_threshold,
		model=model,
		log=log,
		documents=documents,
		folder=folder,
		weights=weights,
		entity_type=entity_type,
		entity_value=entity_value,
		offset=offset,
	)
	if with_diagnostics:
		return outcome.chunks, outcome.diagnostics.as_dict()
	return outcome.chunks


def run_retrieval(
	query: str,
	knowledge_bases: list[str] | None = None,
	top_k: int | None = None,
	search_type: str | None = None,
	similarity_threshold: float | None = None,
	model: str | None = None,
	log: bool = True,
	documents: list[str] | None = None,
	folder: str | None = None,
	*,
	weights: dict[str, float] | None = None,
	entity_type: str | None = None,
	entity_value: str | None = None,
	offset: int = 0,
) -> RetrievalOutcome:
	"""Full retrieval with diagnostics. Public APIs should prefer this."""
	from ai_fr_hg.ai.knowledge import get_accessible_knowledge_bases

	started = time.monotonic()
	settings = _settings()
	diagnostics = RetrievalDiagnostics(brute_force_ceiling=_brute_force_ceiling(settings))
	outcome = RetrievalOutcome(diagnostics=diagnostics)

	if not (query or "").strip():
		return outcome

	accessible = set(get_accessible_knowledge_bases())
	scoped_documents = list(documents or [])

	if entity_type or entity_value:
		entity_docs = _resolve_entity_documents(entity_type, entity_value)
		if not entity_docs:
			return outcome
		scoped_documents = (
			[name for name in scoped_documents if name in set(entity_docs)]
			if scoped_documents
			else entity_docs
		)
		if not scoped_documents:
			return outcome

	if folder and not scoped_documents:
		try:
			from ai_fr_hg.ai.folders import _normalize_folder_path

			diagnostics.folder = _normalize_folder_path(folder)
			folder_docs = _resolve_folder_documents(folder)
		except Exception:
			frappe.log_error(title="Folder-scoped retrieval failed", message=frappe.get_traceback())
			_mark_degraded(diagnostics, "folder_scope_failed")
			return outcome
		if not folder_docs:
			return outcome
		scoped_documents = [row.name for row in folder_docs]
		doc_kbs = {row.knowledge_base for row in folder_docs}
		targets = [kb for kb in doc_kbs if kb in accessible]
	elif scoped_documents:
		doc_kbs = {
			row.knowledge_base
			for row in frappe.get_all(
				"AI Document", filters={"name": ["in", scoped_documents]}, fields=["knowledge_base"]
			)
		}
		targets = [kb for kb in doc_kbs if kb in accessible]
	else:
		if knowledge_bases:
			targets = [kb for kb in knowledge_bases if kb in accessible]
		else:
			targets = list(accessible)

	if not targets:
		return outcome

	populated_filters: dict = {"knowledge_base": ["in", targets]}
	if scoped_documents:
		populated_filters["document"] = ["in", scoped_documents]
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
		return outcome

	# Precedence (RET-04):
	# 1. explicit API/agent override for the *request* top_k / threshold
	# 2. knowledge-base policy per result group
	# 3. platform default
	request_threshold_explicit = similarity_threshold is not None
	final_top_k = cint(top_k) or cint(settings.default_top_k) or 6
	search_type = search_type or ("Hybrid" if settings.enable_hybrid_search else "Semantic")
	platform_threshold = flt(settings.similarity_threshold)
	override_threshold = flt(similarity_threshold) if request_threshold_explicit else None

	policies = _load_kb_policies(targets, settings)
	kb_top_k = {kb: policy["top_k"] for kb, policy in policies.items()}
	kb_thresholds = {kb: policy["similarity_threshold"] for kb, policy in policies.items()}
	if request_threshold_explicit:
		effective_thresholds = {kb: override_threshold for kb in targets}
		threshold_record = {"override": override_threshold}
	else:
		effective_thresholds = kb_thresholds
		threshold_record = dict(kb_thresholds)

	diagnostics.search_type = search_type
	diagnostics.thresholds = threshold_record
	diagnostics.weights = dict(weights or {})
	diagnostics.top_k = {"request": final_top_k, "per_knowledge_base": dict(kb_top_k)}
	diagnostics.retrieval_strategy = {
		"Semantic": "brute_force_scan",
		"Keyword": "keyword",
		"Hybrid": "hybrid_brute_force",
	}.get(search_type, "hybrid_brute_force")

	corpus_filters = {"knowledge_base": ["in", targets]}
	if scoped_documents:
		corpus_filters["document"] = ["in", scoped_documents]
	diagnostics.corpus_size = _count_chunks(corpus_filters)
	if diagnostics.corpus_size > diagnostics.brute_force_ceiling:
		_mark_degraded(diagnostics, "corpus_exceeds_brute_force_ceiling")

	semantic: dict[str, float] = {}
	keyword: dict[str, float] = {}
	kb_of: dict[str, str] = {}

	if search_type in ("Semantic", "Hybrid"):
		try:
			semantic = semantic_search(
				query,
				targets,
				top_k=final_top_k,
				model=model,
				documents=scoped_documents or None,
				policies=policies,
				diagnostics=diagnostics,
			)
		except Exception as exc:
			frappe.log_error(title="AI semantic search failed", message=str(exc))
			_mark_degraded(diagnostics, "semantic_embedding_failed")
			if search_type == "Semantic":
				raise
	if search_type in ("Keyword", "Hybrid"):
		keyword = keyword_search(
			query,
			targets,
			limit=max(final_top_k * 10, 50),
			documents=scoped_documents or None,
			diagnostics=diagnostics,
		)

	if semantic or keyword:
		kb_of.update(_knowledge_bases_for(list(semantic) + list(keyword)))

	# Per-KB threshold before fusion (attached documents skip the threshold).
	if semantic and search_type != "Keyword" and not scoped_documents:
		if request_threshold_explicit:
			semantic = ru.apply_threshold(semantic, override_threshold or 0.0)
		else:
			semantic = ru.apply_group_thresholds(
				semantic, kb_of, effective_thresholds, default_threshold=platform_threshold
			)

	semantic = ru.take_top_per_group(semantic, kb_of, kb_top_k, default_limit=final_top_k)
	keyword = ru.take_top_per_group(keyword, kb_of, kb_top_k, default_limit=final_top_k)

	if search_type == "Semantic":
		fused = dict(semantic)
	elif search_type == "Keyword":
		fused = dict(keyword)
	else:
		fused = ru.fuse_rrf([semantic, keyword])
	fused = ru.apply_identity_weights(fused, weights, kb_of)

	if not fused and scoped_documents:
		rows = frappe.get_all(
			"AI Document Chunk",
			filters={"document": ["in", scoped_documents], "knowledge_base": ["in", targets]},
			fields=["name"],
			order_by="chunk_index asc",
			limit_page_length=final_top_k,
		)
		fused = {row.name: 1.0 for row in rows}
		diagnostics.fallback_reason = diagnostics.fallback_reason or "attached_document_excerpt"
	if not fused:
		if log:
			_log_search(query, targets, search_type, [], started)
		return outcome

	ordered = ru.ordered_names(fused)
	diagnostics.candidate_count = len(ordered)
	page = ru.slice_page(ordered, offset=offset, limit=final_top_k)
	results = _hydrate(page, semantic, keyword)
	outcome.chunks = results
	outcome.total = diagnostics.candidate_count

	if log:
		_log_search(query, targets, search_type, results, started)
	return outcome


def _knowledge_bases_for(names: list[str]) -> dict[str, str]:
	if not names:
		return {}
	unique = list(dict.fromkeys(names))
	mapping: dict[str, str] = {}
	for start in range(0, len(unique), 500):
		batch = unique[start : start + 500]
		for row in frappe.get_all(
			"AI Document Chunk",
			filters={"name": ["in", batch]},
			fields=["name", "knowledge_base"],
			limit_page_length=len(batch),
		):
			mapping[row.name] = row.knowledge_base
	return mapping


def _hydrate(ordered, semantic, keyword) -> list[RetrievedChunk]:
	"""Load chunk bodies and document titles for the selected results."""
	names = [name for name, _score in ordered]
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
				"token_count",
				"embedding_model",
			],
			limit_page_length=0,
		)
	}
	titles = {
		row.name: row
		for row in frappe.get_all(
			"AI Document",
			filters={"name": ["in", list({r.document for r in rows.values()})]},
			fields=["name", "title", "language"],
		)
	}

	results = []
	for name, score in ordered:
		row = rows.get(name)
		if not row:
			continue
		meta = titles.get(row.document)
		results.append(
			RetrievedChunk(
				chunk=name,
				document=row.document,
				document_title=(meta.title if meta and meta.title else None) or row.document,
				knowledge_base=row.knowledge_base,
				content=row.content,
				score=score,
				semantic_score=semantic.get(name, 0.0),
				keyword_score=keyword.get(name, 0.0),
				heading=row.heading,
				page_number=cint(row.page_number),
				language=resolve_document_language(meta.language if meta else None, row.content) or None,
				token_count=cint(row.token_count) or estimate_tokens(row.content or ""),
				embedding_model=row.embedding_model,
			)
		)
	return results


def _log_search(query, targets, search_type, results, started) -> None:
	try:
		telemetry = []
		for result in results[:10]:
			payload = result.as_dict()
			payload["content"] = (payload.get("content") or "")[:1000]
			payload["document_title"] = (payload.get("document_title") or "")[:200]
			telemetry.append(payload)
		frappe.enqueue(
			"ai_fr_hg.ai.retrieval._log_search_job",
			queue="short",
			timeout=120,
			job_id=f"ai_search_log:{frappe.generate_hash(length=10)}",
			query=(query or "")[:1000],
			targets=targets,
			search_type=search_type,
			results=telemetry,
			result_count=len(results),
			top_score=results[0].score if results else 0,
			duration_ms=int((time.monotonic() - started) * 1000),
			user=frappe.session.user,
		)
	except Exception:
		frappe.log_error(title="AI Search Query log failed", message=frappe.get_traceback())


def _log_search_job(query, targets, search_type, results, result_count, top_score, duration_ms, user) -> None:
	"""Persist search telemetry: redacted, bounded, and policy-controlled."""
	try:
		enabled = frappe.db.get_single_value("AI Platform Settings", "log_search_queries", cache=False)
		if enabled is not None and not cint(enabled):
			return

		from ai_fr_hg.ai.logging import redact

		telemetry = [
			{
				"chunk": item.get("chunk"),
				"document": item.get("document"),
				"title": redact(str(item.get("document_title") or ""))[:200],
				"score": item.get("score"),
				"snippet": redact(str(item.get("content") or ""))[:200],
			}
			for item in (results or [])[:10]
		]
		doc = frappe.new_doc("AI Search Query")
		doc.update(
			{
				"query": redact(str(query or ""))[:1000],
				"knowledge_base": targets[0] if len(targets) == 1 else None,
				"user": user,
				"search_type": search_type,
				"result_count": result_count,
				"top_score": top_score,
				"duration_ms": duration_ms,
				"results": frappe.as_json(telemetry),
			}
		)
		doc.flags.ignore_permissions = True
		doc.insert(ignore_permissions=True)
	except Exception:
		frappe.log_error(title="AI Search Query log failed", message=frappe.get_traceback())


def build_context(
	results: list[RetrievedChunk],
	max_characters: int | None = None,
	*,
	packed: list | None = None,
	context_window: int | None = None,
	reserve_tokens: int | None = None,
	model: str | None = None,
) -> str:
	"""Render retrieved chunks into a numbered context block for the prompt.

	An oversized first passage is truncated rather than skipped (RET-06).
	Near-duplicate passages from the same document are dropped. Citation
	numbers follow the packed list written into ``packed`` when supplied.
	"""
	if not results:
		return ""

	limit = cint(max_characters) if max_characters is not None else 0
	window = cint(context_window)
	reserve = cint(reserve_tokens)
	# Settings are only required when the caller did not pass an explicit
	# character budget, or when a model context window must be applied.
	if not limit or window or model:
		settings = _settings()
		if not limit:
			limit = cint(settings.max_context_characters) or 12000
		if not window and model:
			window = cint(frappe.db.get_value("AI Model", model, "context_window"))
		if not window:
			window = cint(getattr(settings, "default_context_window", 0))
		if not reserve:
			reserve = cint(getattr(settings, "default_max_tokens", 0)) or 512
	if window:
		# Leave room for the system prompt, the user turn, and generation.
		token_budget = max(256, window - reserve - 512)
		limit = min(limit or token_budget * CHARS_PER_TOKEN, token_budget * CHARS_PER_TOKEN)

	prepared: list[tuple[RetrievedChunk, str, str]] = []
	for position, result in enumerate(results, start=1):
		header = f"[{position}] {result.document_title}"
		if result.heading:
			header += f" - {result.heading}"
		if result.page_number:
			header += f" (page {result.page_number})"
		code = resolve_document_language(result.language, result.content)
		if code:
			header += f" [language={language_name(code)}]"
		prepared.append((result, header, result.content or ""))

	kept, text = ru.pack_context_blocks(prepared, limit=limit)
	if packed is not None:
		packed.extend(kept)
	return text
