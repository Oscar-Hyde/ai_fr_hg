# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""High-precision pattern entity extraction — a pure enhancement layer.

This module ports the deterministic, ReDoS-safe pattern tokenizer from the
File Analysis reference system (``core/shared/tokenizer.py`` and the
canonicalization rules of ``core/processing/semantic_candidates.py``) onto
this platform's own reading contract.

Design contract:

* **Read-only over the existing pipeline.** The only input is ``AI Document``
  content that the platform has *already* extracted and stored. Nothing here
  re-reads source files, re-chunks, re-embeds or rewrites any existing
  document field. Extraction, normalization and storage paths of the host
  pipeline are never touched.
* **High precision only.** Every pattern is a linear (non-backtracking) regex
  with a deterministic shape: email, url, phone, ip, hash, date, identifier,
  money — plus ``custom`` as the safety bucket for hand-curated rows, exactly
  like the reference's persistable type mapping. Lexical guesses (person,
  organization, location) are deliberately *not* ported: the reference labels
  them low confidence and never promotes them to facts.
* **Idempotent persistence.** Rows live in their own ``AI Pattern Entity``
  DocType and are keyed by ``(document, entity_type, normalized_value)``, so
  rescanning updates in place instead of duplicating.
"""

from __future__ import annotations

import calendar
import re

import frappe
from frappe import _
from frappe.utils import cint

# ---------------------------------------------------------------------------
# Pattern registry — ported verbatim from the reference tokenizer. Bounded
# quantifiers keep six of the eight patterns linear; the two unbounded-class
# patterns (email, url) are wrapped in necessary-literal guards below so they
# never run over content that cannot match.
# ---------------------------------------------------------------------------

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
URL_RE = re.compile(r"https?://[^\s<>'\"]+|www\.[^\s<>'\"]+", re.IGNORECASE)
PHONE_RE = re.compile(r"(?:(?:\+|00)\d{1,3}[-.\s]?)?\(?\d{2,4}\)?[-.\s]?\d{3,4}[-.\s]?\d{3,4}\b")
IP_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
HASH_RE = re.compile(r"\b[a-fA-F0-9]{32,64}\b")
DATE_RE = re.compile(
	r"\b(?:\d{4}-\d{2}-\d{2}|\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|"
	r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2},?\s+\d{4})\b",
	re.IGNORECASE,
)
IDENTIFIER_RE = re.compile(
	r"\b(?:INV|PO|SO|WO|REF|ID|CONTRACT|AGR|CASE)[-_/][A-Z0-9][-_/A-Z0-9]{2,24}\b",
	re.IGNORECASE,
)
MONEY_RE = re.compile(
	r"(?:USD|EUR|GBP|BGN|ILS|AED|\$|€|£)\s?\d{1,3}(?:,\d{3})*(?:\.\d{2})?"
	r"|\d{1,3}(?:,\d{3})*(?:\.\d{2})?\s?(?:USD|EUR|GBP|BGN|ILS|AED)",
	re.IGNORECASE,
)

#: Ordered registry: iteration order defines the deterministic output order.
PATTERN_SPECS: tuple[tuple[re.Pattern[str], str], ...] = (
	(EMAIL_RE, "email"),
	(URL_RE, "url"),
	(PHONE_RE, "phone"),
	(IP_RE, "ip"),
	(HASH_RE, "hash"),
	(DATE_RE, "date"),
	(IDENTIFIER_RE, "identifier"),
	(MONEY_RE, "money"),
)

#: Types this layer can persist. ``custom`` mirrors the reference safety
#: bucket: an unknown type is never dropped, it lands in ``custom``.
PATTERN_ENTITY_TYPES = ("email", "url", "phone", "ip", "hash", "date", "identifier", "money", "custom")

PATTERN_ENTITY_OPTIONS = "\n".join(PATTERN_ENTITY_TYPES)

# Maximum text scanned per document (head + tail sampling beyond this),
# matching the reference bound of ~200,000 words.
MAX_SCAN_CHARS = 1_000_000
DEFAULT_MAX_PATTERN_ENTITIES = 500
MAX_VALUE_LENGTH = 255

# Necessary-literal guards. Each pattern contains at least one literal that
# must be present for any match to exist; when the literal is absent the
# regex is skipped at native string speed. Guards never change which matches
# are found — a pattern only runs when a match is possible. Without them the
# two unbounded-class patterns (email, url) degrade quadratically on long
# base64/hex/minified runs, which is the exact freeze the reference tokenizer
# guards against with bounded sampling.
_DIGIT_CHARS = frozenset("0123456789")
_HEX_CHARS = frozenset("0123456789abcdefABCDEF")
_IDENTIFIER_PREFIXES = ("inv", "po", "so", "wo", "ref", "id", "contract", "agr", "case")
_CURRENCY_TOKENS = ("usd", "eur", "gbp", "bgn", "ils", "aed")
_CURRENCY_SIGNS = ("$", "€", "£")

# Provenance quote window (chars either side of the first occurrence) and the
# hard quote cap, matching the reference evidence layer.
QUOTE_WINDOW = 60
QUOTE_LIMIT = 220

_SCAN_CACHE_TTL = 30 * 24 * 60 * 60
_SCHEDULER_BATCH = 25

_ISO_DATE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
_SLASH_DATE_RE = re.compile(r"^(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})$")
_MONTH_DATE_RE = re.compile(
	r"^(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+\d{1,2},?\s+\d{4}$",
	re.IGNORECASE,
)
_MONEY_NOISE_RE = re.compile(r"[,\s]")


# ---------------------------------------------------------------------------
# Canonical normalization — deterministic identity per type.
# ---------------------------------------------------------------------------


def canonicalize_pattern_value(entity_type: str, value: str) -> str:
	"""Deterministic identity form. Does not invent a different real-world value."""
	cleaned = " ".join((value or "").strip().casefold().split())
	kind = (entity_type or "").strip().lower()
	if kind == "date":
		return _to_iso_date(cleaned) or cleaned
	if kind == "money":
		return _MONEY_NOISE_RE.sub("", cleaned)
	if kind == "identifier":
		return cleaned.replace(" ", "").replace("_", "-")
	return cleaned


def _calendar_date(year: int, month: int, day: int) -> str | None:
	if month < 1 or month > 12 or day < 1:
		return None
	try:
		last = calendar.monthrange(year, month)[1]
	except ValueError:
		return None
	if day > last:
		return None
	return f"{year:04d}-{month:02d}-{day:02d}"


def _to_iso_date(value: str) -> str | None:
	match = _ISO_DATE_RE.match(value)
	if match:
		return _calendar_date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
	match = _SLASH_DATE_RE.match(value)
	if not match:
		return None
	left, mid, year = match.group(1), match.group(2), match.group(3)
	if len(year) == 2:
		year = f"20{year}" if int(year) < 70 else f"19{year}"
	year_i = int(year)
	# Ambiguous D/M vs M/D: if first part > 12 it is day-first; if the second
	# part > 12 it is month-first; otherwise keep the ISO-like M/D reading.
	a, b = int(left), int(mid)
	if a > 12 and b <= 12:
		return _calendar_date(year_i, b, a)
	if b > 12 and a <= 12:
		return _calendar_date(year_i, a, b)
	return _calendar_date(year_i, a, b)


def _is_valid_ipv4(value: str) -> bool:
	parts = value.split(".")
	if len(parts) != 4:
		return False
	for part in parts:
		if not part.isdigit() or len(part) > 3:
			return False
		number = int(part)
		if number > 255:
			return False
	return True


def _is_valid_money(value: str) -> bool:
	digits = re.sub(r"[^\d.]", "", value or "")
	if not digits:
		return False
	try:
		amount = float(digits)
	except ValueError:
		return False
	return 0 <= amount < 1_000_000_000_000


def _passes_semantic_check(entity_type: str, value: str) -> bool:
	"""Reject shape-only matches that are not real IP/date/money values."""
	if entity_type == "ip":
		return _is_valid_ipv4(value)
	if entity_type == "date":
		cleaned = " ".join((value or "").strip().split())
		return bool(_to_iso_date(cleaned.casefold()) or _MONTH_DATE_RE.match(cleaned))
	if entity_type == "money":
		return _is_valid_money(value)
	return True


def persistable_pattern_type(entity_type: str, method: str = "pattern") -> str:
	"""Map any extracted type onto the DocType options without dropping the fact.

	`method` selects the valid registry: deterministic rows may only carry a
	pattern type, semantic rows may additionally carry the §11 semantic kinds.
	This keeps a model from writing "person" onto a row that claims to be an
	exact regex match.
	"""
	from ai_fr_hg.ai.semantic import SEMANTIC_ENTITY_TYPES

	raw = (entity_type or "custom").strip().lower()
	allowed = PATTERN_ENTITY_TYPES + SEMANTIC_ENTITY_TYPES if method == "semantic" else PATTERN_ENTITY_TYPES
	return raw if raw in allowed else "custom"


# ---------------------------------------------------------------------------
# Extraction with provenance.
# ---------------------------------------------------------------------------


def _first_occurrence_quote(scan: str, value: str) -> tuple[int, str] | None:
	"""First literal occurrence of ``value`` plus a bounded context quote."""
	if not scan or not value or len(value) > 1024:
		return None
	pos = scan.find(value)
	if pos < 0:
		return None
	start = max(0, pos - QUOTE_WINDOW)
	end = min(len(scan), pos + len(value) + QUOTE_WINDOW)
	quote = scan[start:end].strip()
	if len(quote) > QUOTE_LIMIT:
		quote = quote[: QUOTE_LIMIT - 1].rstrip() + "…"
	return pos, quote


def _guard_passes(entity_type: str, scan_text: str, folded: str) -> bool:
	"""Necessary-literal check: skip a regex that provably cannot match."""
	if entity_type == "email":
		return "@" in scan_text
	if entity_type == "url":
		return "http" in folded or "www." in folded
	if entity_type in ("phone", "ip", "date"):
		return not _DIGIT_CHARS.isdisjoint(scan_text)
	if entity_type == "hash":
		return not _HEX_CHARS.isdisjoint(scan_text)
	if entity_type == "identifier":
		return any(prefix in folded for prefix in _IDENTIFIER_PREFIXES)
	if entity_type == "money":
		return any(token in folded for token in _CURRENCY_TOKENS) or any(
			sign in scan_text for sign in _CURRENCY_SIGNS
		)
	return True


def _scan_window(text: str):
	"""Head+tail sample with a mapper from scan offsets back to source offsets."""
	if len(text) <= MAX_SCAN_CHARS:
		return text, lambda pos: pos
	half = MAX_SCAN_CHARS // 2
	tail_start = len(text) - half
	scan = text[:half] + "\n" + text[tail_start:]

	def to_source(pos: int) -> int:
		if pos < half:
			return pos
		if pos == half:
			return half
		return tail_start + (pos - half - 1)

	return scan, to_source


def extract_pattern_entities(text: str, max_entities: int = DEFAULT_MAX_PATTERN_ENTITIES) -> list[dict]:
	"""Extract high-precision pattern entities safely, never freezing on giant inputs.

	Returns a deterministic list of ``{entity_type, value, normalized_value,
	occurrences, first_offset, context_quote}``. Surface variants that share a
	canonical identity merge into one entry; ``value`` keeps the first casing
	seen. ``first_offset`` is relative to the scanned window.
	"""
	text = text or ""
	if not text:
		return []

	scan_text, to_source = _scan_window(text)

	folded = scan_text.casefold()
	found: dict[tuple[str, str], dict] = {}

	def add(entity_type: str, value: str) -> None:
		val = value.strip().strip(".,;:)]}")
		if not val or len(val) > MAX_VALUE_LENGTH:
			return
		if not _passes_semantic_check(entity_type, val):
			return
		normalized = canonicalize_pattern_value(entity_type, val)
		key = (entity_type, normalized)
		if key not in found:
			if len(found) >= max_entities:
				return
			entry: dict = {
				"entity_type": entity_type,
				"value": val,
				"normalized_value": normalized,
				"occurrences": 0,
			}
			located = _first_occurrence_quote(scan_text, val)
			if located is not None:
				entry["first_offset"] = to_source(located[0])
				entry["context_quote"] = located[1]
			found[key] = entry
		found[key]["occurrences"] = int(found[key]["occurrences"]) + 1

	for rx, entity_type in PATTERN_SPECS:
		if not _guard_passes(entity_type, scan_text, folded):
			continue
		for match in rx.finditer(scan_text):
			add(entity_type, match.group(0))
			if len(found) >= max_entities:
				break

	return list(found.values())


# ---------------------------------------------------------------------------
# Persistence — an idempotent, document-scoped sync into ``AI Pattern Entity``.
# ---------------------------------------------------------------------------


def max_pattern_entities() -> int:
	"""Bounded scan size from platform settings, defaulting to the reference bound."""
	configured = cint(frappe.db.get_single_value("AI Platform Settings", "max_pattern_entities"))
	return max(1, configured or DEFAULT_MAX_PATTERN_ENTITIES)


def scan_document(document: str, *, max_entities: int | None = None) -> dict:
	"""Extract pattern entities from a document's stored content and sync rows.

	The document itself is never written: this reads the already-extracted
	``content`` field, upserts ``AI Pattern Entity`` rows by canonical identity
	``(document, entity_type, normalized_value)`` and prunes rows that the
	current extraction no longer contains. Rescans are therefore idempotent
	and deterministic.
	"""
	row = frappe.db.get_value(
		"AI Document",
		document,
		["name", "content", "knowledge_base", "checksum", "pattern_scan_checksum"],
		as_dict=True,
	)
	if row is None:
		frappe.throw(_("AI Document {0} does not exist.").format(document), frappe.DoesNotExistError)

	entities = extract_pattern_entities(
		row.content or "", max_entities=max_entities or max_pattern_entities()
	)
	checksum = row.checksum or ""

	existing: dict[tuple[str, str], str] = {
		(candidate.entity_type, candidate.normalized_value): candidate.name
		for candidate in frappe.get_all(
			"AI Pattern Entity",
			filters={"document": document},
			fields=["name", "entity_type", "normalized_value"],
		)
	}

	created = 0
	updated = 0
	touched: set[str] = set()

	for entity in entities:
		entity_type = persistable_pattern_type(str(entity["entity_type"]))
		value = str(entity["value"] or "")[:500].strip()
		normalized = str(entity["normalized_value"] or value)[:500]
		if not value:
			continue
		key = (entity_type, normalized)
		patch = {
			"value": value,
			"occurrences": max(1, cint(entity["occurrences"])),
			"first_offset": entity.get("first_offset"),
			"context_quote": entity.get("context_quote"),
			"source_checksum": checksum,
		}
		name = existing.get(key)
		if name:
			frappe.db.set_value("AI Pattern Entity", name, patch, update_modified=False)
			touched.add(name)
			updated += 1
			continue
		entity_doc = frappe.new_doc("AI Pattern Entity")
		entity_doc.document = document
		entity_doc.knowledge_base = row.knowledge_base
		entity_doc.entity_type = entity_type
		entity_doc.value = value
		entity_doc.normalized_value = normalized
		entity_doc.occurrences = patch["occurrences"]
		entity_doc.first_offset = patch["first_offset"]
		entity_doc.context_quote = patch["context_quote"]
		entity_doc.source_checksum = checksum
		try:
			entity_doc.insert(ignore_permissions=True)
		except frappe.DuplicateEntryError:
			# A concurrent scan inserted the identity first; adopt that row.
			name = frappe.db.get_value(
				"AI Pattern Entity",
				{"document": document, "entity_type": entity_type, "normalized_value": normalized},
				"name",
			)
			if not name:
				raise
			frappe.db.set_value("AI Pattern Entity", name, patch, update_modified=False)
			touched.add(name)
			updated += 1
			continue
		touched.add(entity_doc.name)
		created += 1

	removed = 0
	stale = [name for name in existing.values() if name not in touched]
	if stale:
		removed = len(stale)
		for name in stale:
			frappe.db.delete("AI Pattern Entity", name)

	_mark_scanned(document, checksum)

	by_type: dict[str, int] = {}
	for entity in entities:
		by_type[str(entity["entity_type"])] = by_type.get(str(entity["entity_type"]), 0) + 1

	return {
		"document": document,
		"total": len(entities),
		"created": created,
		"updated": updated,
		"removed": removed,
		"by_type": by_type,
	}


def handle_document_trashed(doc, method: str | None = None) -> None:
	"""Doc-event cascade: remove pattern rows with their document.

	Registered for ``AI Document`` ``on_trash``, which Frappe runs *before*
	link validation, so trashing a document can never be blocked by the rows
	this layer owns.
	"""
	if doc and doc.name:
		frappe.db.delete("AI Pattern Entity", {"document": doc.name})


# ---------------------------------------------------------------------------
# Background backfill (opt-in through AI Platform Settings).
# ---------------------------------------------------------------------------


def _scan_cache_key(document: str) -> str:
	return f"ai_fr_hg:pattern_scan:{document}"


def _mark_scanned(document: str, checksum: str) -> None:
	"""Persist scan identity on the document so empty results are not rescanned."""
	value = checksum or ""
	try:
		frappe.db.set_value("AI Document", document, "pattern_scan_checksum", value, update_modified=False)
	except Exception:
		pass
	try:
		frappe.cache.set_value(_scan_cache_key(document), value, expires_in_sec=_SCAN_CACHE_TTL)
	except Exception:
		pass


def _was_scanned(document: str, checksum: str) -> bool:
	value = checksum or ""
	try:
		stored = frappe.db.get_value("AI Document", document, "pattern_scan_checksum")
		if stored == value:
			return True
	except Exception:
		pass
	try:
		return frappe.cache.get_value(_scan_cache_key(document)) == value
	except Exception:
		return False


def list_pattern_entities(
	*,
	knowledge_base: str | None = None,
	entity_type: str | None = None,
	document: str | None = None,
	limit: int = 50,
	offset: int = 0,
) -> dict:
	"""Permission-aware explorer listing. Uses Frappe get_list row filters."""
	filters: dict = {}
	if knowledge_base:
		filters["knowledge_base"] = knowledge_base
	if entity_type:
		filters["entity_type"] = entity_type
	if document:
		filters["document"] = document
	page = max(1, min(cint(limit) or 50, 200))
	start = max(0, cint(offset))
	rows = frappe.get_list(
		"AI Pattern Entity",
		filters=filters,
		fields=[
			"name",
			"document",
			"knowledge_base",
			"entity_type",
			"value",
			"normalized_value",
			"occurrences",
			"first_offset",
			"context_quote",
			"extraction_method",
			"confidence",
			"model_used",
		],
		order_by="occurrences desc, modified desc",
		limit=page,
		start=start,
	)
	counts = frappe.get_list(
		"AI Pattern Entity",
		filters=filters,
		fields=["entity_type", {"COUNT": "*", "as": "total"}],
		group_by="entity_type",
		order_by="total desc",
	)
	return {
		"entities": rows,
		"offset": start,
		"limit": page,
		"count": len(rows),
		"entity_counts": {row.entity_type: cint(row.total) for row in counts},
	}


def scan_pending_documents(limit: int = _SCHEDULER_BATCH) -> list[dict]:
	"""Scan indexed documents whose stored content has not been scanned yet.

	Only ever reads already-extracted content and writes this layer's own
	DocType; failures are logged and never abort the batch.
	"""
	candidates = frappe.db.sql(
		"""
		select doc.name, doc.checksum
		from `tabAI Document` doc
		where doc.status = 'Indexed'
		  and ifnull(doc.content, '') != ''
		  and ifnull(doc.pattern_scan_checksum, '') != ifnull(doc.checksum, '')
		  and not exists (
			select 1 from `tabAI Pattern Entity` pe
			where pe.document = doc.name and pe.source_checksum = ifnull(doc.checksum, '')
		  )
		order by doc.modified desc
		limit %(limit)s
		""",
		{"limit": max(1, cint(limit))},
		as_dict=True,
	)

	results = []
	for candidate in candidates:
		if _was_scanned(candidate.name, candidate.checksum or ""):
			continue
		try:
			results.append(scan_document(candidate.name))
		except Exception:
			frappe.log_error(
				title=f"AI pattern scan failed: {candidate.name}",
				message=frappe.get_traceback(),
			)
	return results
