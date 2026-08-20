# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Pure retrieval algorithms with no Frappe or database dependency.

Tokenization, keyword scoring, reciprocal-rank fusion, overlap detection and
context packing live here so they can be tested without a bench. The Frappe
orchestration (permissions, paging, embeddings, diagnostics) stays in
``ai.retrieval``.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence

# Unicode-aware: Arabic, Hebrew, identifiers, part numbers and dotted paths.
WORD = re.compile(r"[\w\-/.]{2,}", re.UNICODE)
LATIN_TERM = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\-/]{1,}$")

#: RRF damping constant. Unchanged from the original hybrid retriever so
#: existing small-corpus rankings stay comparable.
RRF_K = 60

#: Minimum excerpt kept when the first context block exceeds the budget.
MIN_CONTEXT_EXCERPT = 240

#: Jaccard overlap at which two same-document chunks are treated as duplicates.
OVERLAP_JACCARD = 0.85


def tokenize_query(query: str, *, limit: int = 12) -> list[str]:
	"""Split a query into bounded retrieval terms.

	Keeps identifiers (`INV-2024`, `SKUs/AA`), dotted paths and CJK/Arabic/
	Hebrew words. Punctuation-only tokens are dropped. Order is preserved and
	duplicates are removed.
	"""
	seen: set[str] = set()
	terms: list[str] = []
	for raw in WORD.findall(query or ""):
		term = raw.strip(".-/")
		if len(term) < 2:
			continue
		key = term.lower()
		if key in seen:
			continue
		seen.add(key)
		terms.append(key)
		if len(terms) >= limit:
			break
	return terms


def is_fulltext_term(term: str) -> bool:
	"""MariaDB InnoDB FULLTEXT ignores tokens shorter than 3 latin characters."""
	if not term or len(term) < 3:
		return False
	return bool(LATIN_TERM.match(term))


def escape_like(value: str) -> str:
	"""Escape SQL LIKE metacharacters. MariaDB's default escape is backslash."""
	return (value or "").replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def like_pattern(term: str) -> str:
	"""Bounded substring pattern for one query term."""
	return f"%{escape_like(term)}%"


def keyword_score(content: str, terms: Sequence[str]) -> float:
	"""Score a passage by distinct-term coverage plus bounded hit frequency.

	Covering many query terms outranks repeating one term, which is the right
	bias for identifiers and proper nouns.
	"""
	if not terms or not content:
		return 0.0
	haystack = content.lower()
	hits = 0
	distinct = 0
	for term in terms:
		count = haystack.count(term)
		if count:
			hits += count
			distinct += 1
	if not hits:
		return 0.0
	return (distinct / len(terms)) * 0.7 + min(hits / 20.0, 1.0) * 0.3


def fuse_rrf(
	ranked_lists: Sequence[dict[str, float]],
	*,
	k: int = RRF_K,
	weights: dict[str, float] | None = None,
	identity_weights: dict[str, float] | None = None,
) -> dict[str, float]:
	"""Reciprocal rank fusion of one or more ``name → score`` maps.

	``weights`` is applied per identity (typically a knowledge-base weight
	looked up through ``identity_weights`` which maps result name → KB). When
	``identity_weights`` is omitted, ``weights`` is treated as name → weight.
	A missing or non-positive weight excludes the result.
	"""
	fused: dict[str, float] = {}
	for ranked in ranked_lists:
		ordered = sorted(ranked.items(), key=lambda row: row[1], reverse=True)
		for position, (name, _score) in enumerate(ordered):
			fused[name] = fused.get(name, 0.0) + 1.0 / (k + position + 1)

	if not weights:
		return fused

	weighted: dict[str, float] = {}
	for name, score in fused.items():
		key = identity_weights.get(name, name) if identity_weights else name
		weight = float(weights.get(key, 1.0) or 0.0)
		if weight <= 0:
			continue
		weighted[name] = score * weight
	return weighted


def take_top_per_group(
	scores: dict[str, float],
	group_of: dict[str, str],
	limits: dict[str, int],
	*,
	default_limit: int,
) -> dict[str, float]:
	"""Keep at most ``limits[group]`` results from each group, best first."""
	buckets: dict[str, list[tuple[str, float]]] = {}
	for name, score in scores.items():
		buckets.setdefault(group_of.get(name, ""), []).append((name, score))
	kept: dict[str, float] = {}
	for group, items in buckets.items():
		limit = max(int(limits.get(group) or default_limit or 1), 1)
		items.sort(key=lambda row: row[1], reverse=True)
		for name, score in items[:limit]:
			kept[name] = score
	return kept


def apply_threshold(scores: dict[str, float], threshold: float) -> dict[str, float]:
	"""Drop scores strictly below ``threshold``. ``threshold <= 0`` keeps all."""
	if threshold <= 0:
		return dict(scores)
	return {name: score for name, score in scores.items() if score >= threshold}


def apply_group_thresholds(
	scores: dict[str, float],
	group_of: dict[str, str],
	thresholds: dict[str, float],
	*,
	default_threshold: float,
) -> dict[str, float]:
	"""Apply a per-group similarity threshold before fusion."""
	kept: dict[str, float] = {}
	for name, score in scores.items():
		threshold = thresholds.get(group_of.get(name, ""), default_threshold)
		if threshold <= 0 or score >= threshold:
			kept[name] = score
	return kept


def apply_identity_weights(
	scores: dict[str, float],
	weights: dict[str, float] | None,
	identity_of: dict[str, str] | None = None,
) -> dict[str, float]:
	"""Multiply each score by its identity weight. Non-positive weights drop the row."""
	if not weights:
		return dict(scores)
	kept: dict[str, float] = {}
	for name, score in scores.items():
		key = identity_of.get(name, name) if identity_of else name
		weight = float(weights.get(key, 1.0) or 0.0)
		if weight <= 0:
			continue
		kept[name] = score * weight
	return kept


def term_jaccard(left: str, right: str) -> float:
	"""Word-set Jaccard similarity of two passages."""
	a = set(tokenize_query(left, limit=80))
	b = set(tokenize_query(right, limit=80))
	if not a or not b:
		return 0.0
	return len(a & b) / len(a | b)


def chunks_overlap(left_document: str, left_content: str, right_document: str, right_content: str) -> bool:
	"""True when two passages from the same document are near-duplicates."""
	if left_document != right_document:
		return False
	a = (left_content or "").strip()
	b = (right_content or "").strip()
	if not a or not b:
		return False
	if a in b or b in a:
		return True
	return term_jaccard(a, b) >= OVERLAP_JACCARD


def truncate_to_budget(text: str, budget: int, *, ellipsis: str = "…") -> str:
	"""Cut ``text`` to ``budget`` characters on a word boundary when possible."""
	if budget <= 0:
		return ""
	if len(text) <= budget:
		return text
	if budget <= len(ellipsis):
		return ellipsis[:budget]
	cut = text[: budget - len(ellipsis)]
	if " " in cut and len(cut) > 24:
		cut = cut.rsplit(" ", 1)[0]
	return cut.rstrip() + ellipsis


def fit_block(header: str, content: str, remaining: int, *, force: bool = False) -> str | None:
	"""Fit one numbered context block into ``remaining`` characters.

	When ``force`` is set (the first useful block), the content is truncated
	rather than skipped so an oversized first passage cannot yield empty
	context.
	"""
	separator = "\n"
	overhead = len(header) + len(separator)
	if remaining <= overhead:
		if not force:
			return None
		# Still emit a header-only block if that is all that fits.
		if remaining >= len(header):
			return header[:remaining]
		return None
	available = remaining - overhead
	if len(content) <= available:
		return f"{header}{separator}{content}"
	if not force and available < MIN_CONTEXT_EXCERPT:
		return None
	excerpt_budget = available if force else max(MIN_CONTEXT_EXCERPT, available)
	excerpt_budget = min(excerpt_budget, available)
	excerpt = truncate_to_budget(content, excerpt_budget)
	if not excerpt:
		return None
	return f"{header}{separator}{excerpt}"


def pack_context_blocks(
	blocks: Sequence[tuple[object, str, str]],
	*,
	limit: int,
	separator: str = "\n\n---\n\n",
) -> tuple[list[object], str]:
	"""Pack ``(result, header, content)`` tuples into a character budget.

	Duplicate overlapping passages are skipped. The first kept block is always
	truncated to fit when it would otherwise overflow. Returns the kept result
	objects (citation mapping) and the packed prompt string.
	"""
	if limit <= 0 or not blocks:
		return [], ""

	kept: list[object] = []
	rendered: list[str] = []
	used = 0
	kept_meta: list[tuple[str, str]] = []  # (document, content) for overlap

	for result, header, content in blocks:
		document = getattr(result, "document", "") or ""
		if any(
			chunks_overlap(document, content, prev_doc, prev_content) for prev_doc, prev_content in kept_meta
		):
			continue
		sep_cost = len(separator) if rendered else 0
		remaining = limit - used - sep_cost
		block = fit_block(header, content, remaining, force=not rendered)
		if not block:
			if not rendered:
				continue
			break
		rendered.append(block)
		kept.append(result)
		kept_meta.append((document, content))
		used += sep_cost + len(block)
		if used >= limit:
			break
	return kept, separator.join(rendered)


def ordered_names(scores: dict[str, float]) -> list[tuple[str, float]]:
	"""Stable best-first ordering of a score map."""
	return sorted(scores.items(), key=lambda row: (-row[1], row[0]))


def slice_page(ordered: Iterable[tuple[str, float]], *, offset: int, limit: int) -> list[tuple[str, float]]:
	"""Apply offset/limit to an already ranked list without re-ranking."""
	start = max(int(offset or 0), 0)
	size = max(int(limit or 0), 0)
	if not size:
		return []
	items = list(ordered)
	return items[start : start + size]
