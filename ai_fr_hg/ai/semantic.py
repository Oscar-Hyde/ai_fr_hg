# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Semantic entity and relationship intelligence (Part 1 §11).

`ai.patterns` extracts *deterministic* entities: things a regex can prove
(email, url, ip, date, money...). §11 additionally requires people,
organizations, locations, concepts, and relationships — none of which have a
deterministic surface form. This module owns that semantic layer.

Architecture decision recorded in ADR-011.

**Frappe V17 capabilities evaluated.** Frappe provides DocTypes, background
jobs, permissions, caching, and the query builder — all of which this module
uses. Frappe has no native named-entity recognition or relationship inference,
so a domain implementation is required; this does not duplicate any framework
responsibility.

**Why the model layer rather than a local NER package.** A local NER model
(spaCy and similar) would add a heavyweight dependency plus model-weight
distribution to a local-first application, and would still need its own
governance and failure contract. This platform already owns a governed model
path — `engine.run_chat` with quota reservation, rate limiting, failover, and
execution logging — and a canonical structured-output validator
(`ai.validation`, INT-02). Routing semantic extraction through it reuses every
one of those guarantees.

**The determinism carve-out.** §8 requires deterministic output "where
possible". Model inference is not deterministic, so this layer is explicitly
excluded from that clause and is instead constrained by three hard rules that
make its output auditable:

1. **Grounding.** Every entity value must appear verbatim in the source text.
   Anything the model invents is discarded before persistence — this is
   enforced mechanically, not requested in the prompt.
2. **Confidence.** Every semantic row carries a model-reported confidence and
   is filtered against a configurable floor.
3. **Evidence.** Every entity records its true character offset and a context
   quote; every relationship requires a supporting verbatim span.

These rules implement §11's "avoid unsupported assumptions" as code rather
than as an instruction the model may ignore.
"""

from __future__ import annotations

import json
import re

import frappe
from frappe import _
from frappe.utils import cint, flt

# ---------------------------------------------------------------------------
# Registries
# ---------------------------------------------------------------------------

#: Semantic entity kinds required by §11, beyond the deterministic patterns.
SEMANTIC_ENTITY_TYPES: tuple[str, ...] = ("person", "organization", "location", "concept")

#: Normalized relationship predicates. A closed vocabulary keeps the graph
#: queryable; anything unrecognized degrades to `related_to` rather than
#: silently inventing a new predicate.
RELATIONSHIP_TYPES: tuple[str, ...] = (
	"works_for",
	"located_in",
	"part_of",
	"reports_to",
	"owns",
	"partner_of",
	"mentions",
	"related_to",
)

#: Synonyms the model commonly emits, mapped onto the closed vocabulary.
_RELATIONSHIP_SYNONYMS: dict[str, str] = {
	"employed_by": "works_for",
	"employee_of": "works_for",
	"works_at": "works_for",
	"member_of": "part_of",
	"belongs_to": "part_of",
	"subsidiary_of": "part_of",
	"division_of": "part_of",
	"based_in": "located_in",
	"headquartered_in": "located_in",
	"lives_in": "located_in",
	"situated_in": "located_in",
	"manages": "reports_to",
	"reports": "reports_to",
	"supervises": "reports_to",
	"owner_of": "owns",
	"acquired": "owns",
	"partners_with": "partner_of",
	"collaborates_with": "partner_of",
	"references": "mentions",
}

#: Confidence floor below which a semantic result is discarded entirely.
DEFAULT_CONFIDENCE_FLOOR = 50.0

#: Bounds. Semantic extraction is expensive, so the text window and the
#: result count are both capped.
MAX_SEMANTIC_TEXT = 12_000
MAX_SEMANTIC_ENTITIES = 100
MAX_SEMANTIC_RELATIONSHIPS = 100
MAX_VALUE_LENGTH = 255


def normalize_relationship_type(value: str | None) -> str:
	"""Map any predicate onto the closed vocabulary without dropping the fact."""
	raw = (value or "").strip().lower().replace(" ", "_").replace("-", "_")
	if raw in RELATIONSHIP_TYPES:
		return raw
	return _RELATIONSHIP_SYNONYMS.get(raw, "related_to")


def normalize_semantic_type(value: str | None) -> str | None:
	"""Map a model-reported entity kind onto the semantic registry."""
	raw = (value or "").strip().lower()
	aliases = {
		"people": "person",
		"human": "person",
		"individual": "person",
		"org": "organization",
		"company": "organization",
		"institution": "organization",
		"place": "location",
		"geo": "location",
		"city": "location",
		"country": "location",
		"topic": "concept",
		"subject": "concept",
		"theme": "concept",
	}
	raw = aliases.get(raw, raw)
	return raw if raw in SEMANTIC_ENTITY_TYPES else None


# ---------------------------------------------------------------------------
# Grounding — the mechanism that keeps inferred output honest.
# ---------------------------------------------------------------------------


def find_grounded_offset(text: str, value: str) -> int | None:
	"""Return the character offset of `value` in `text`, or None if absent.

	Tries an exact match first, then a case-insensitive match, then a
	whitespace-tolerant match (models frequently normalize internal spacing).
	Anything that still cannot be located is treated as ungrounded and dropped
	by the caller.
	"""
	if not text or not value:
		return None
	position = text.find(value)
	if position >= 0:
		return position
	position = text.casefold().find(value.casefold())
	if position >= 0:
		return position
	# Whitespace-tolerant search, mapping back to the original offset.
	pattern = re.compile(r"\s+".join(re.escape(part) for part in value.split()), re.IGNORECASE)
	match = pattern.search(text)
	return match.start() if match else None


def context_quote(text: str, offset: int, value: str, window: int = 80) -> str:
	"""Bounded quote around a located value, for citation re-anchoring."""
	start = max(0, offset - window)
	end = min(len(text), offset + len(value) + window)
	return text[start:end].strip()[:220]


# ---------------------------------------------------------------------------
# Prompt + schema
# ---------------------------------------------------------------------------

_SCHEMA = {
	"type": "object",
	"properties": {
		"entities": {
			"type": "array",
			"items": {
				"type": "object",
				"properties": {
					"type": {"type": "string", "enum": list(SEMANTIC_ENTITY_TYPES)},
					"value": {"type": "string"},
					"confidence": {"type": "number"},
				},
				"required": ["type", "value", "confidence"],
			},
		},
		"relationships": {
			"type": "array",
			"items": {
				"type": "object",
				"properties": {
					"subject": {"type": "string"},
					"predicate": {"type": "string"},
					"object": {"type": "string"},
					"evidence": {"type": "string"},
					"confidence": {"type": "number"},
				},
				"required": ["subject", "predicate", "object", "evidence", "confidence"],
			},
		},
	},
	"required": ["entities", "relationships"],
}

_PROMPT = """You extract structured entities and relationships from documents.

Rules you must follow exactly:
- Only report entities whose text appears VERBATIM in the document.
- Never infer, translate, expand, or correct a name. Copy it exactly as written.
- entity type must be one of: person, organization, location, concept.
- predicate should be one of: {predicates}. Use related_to if none fits.
- For every relationship, "evidence" must be a sentence copied VERBATIM from the document.
- confidence is 0-100, reflecting how certain you are.
- If the document contains none of a category, return an empty list for it.

Return ONLY JSON matching this shape:
{{"entities": [{{"type": "...", "value": "...", "confidence": 0}}],
 "relationships": [{{"subject": "...", "predicate": "...", "object": "...", "evidence": "...", "confidence": 0}}]}}

Document:
---
{document}
---"""


def build_prompt(text: str) -> str:
	return _PROMPT.format(predicates=", ".join(RELATIONSHIP_TYPES), document=text)


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------


def parse_semantic_payload(payload: dict | None, source_text: str, confidence_floor: float) -> dict:
	"""Validate, ground, and normalize a model payload.

	Pure function: no model call, no database. This is the unit under test for
	every grounding and confidence rule, so the rules can be verified without a
	provider.
	"""
	entities: list[dict] = []
	relationships: list[dict] = []
	rejected = {"ungrounded": 0, "low_confidence": 0, "invalid": 0}

	if not isinstance(payload, dict):
		return {"entities": entities, "relationships": relationships, "rejected": rejected}

	seen_entities: set[tuple[str, str]] = set()
	for raw in payload.get("entities") or []:
		if len(entities) >= MAX_SEMANTIC_ENTITIES:
			break
		if not isinstance(raw, dict):
			rejected["invalid"] += 1
			continue
		entity_type = normalize_semantic_type(raw.get("type"))
		value = str(raw.get("value") or "").strip()[:MAX_VALUE_LENGTH]
		if not entity_type or not value:
			rejected["invalid"] += 1
			continue
		confidence = flt(raw.get("confidence"))
		if confidence < confidence_floor:
			rejected["low_confidence"] += 1
			continue
		offset = find_grounded_offset(source_text, value)
		if offset is None:
			# The model produced a value that is not in the document. This is
			# the hallucination guard, and it is not negotiable.
			rejected["ungrounded"] += 1
			continue
		key = (entity_type, value.casefold())
		if key in seen_entities:
			continue
		seen_entities.add(key)
		entities.append(
			{
				"entity_type": entity_type,
				"value": value,
				"normalized_value": value.casefold()[:500],
				"confidence": max(0.0, min(100.0, confidence)),
				"first_offset": offset,
				"context_quote": context_quote(source_text, offset, value),
				"occurrences": max(1, source_text.casefold().count(value.casefold())),
			}
		)

	seen_relationships: set[tuple[str, str, str]] = set()
	for raw in payload.get("relationships") or []:
		if len(relationships) >= MAX_SEMANTIC_RELATIONSHIPS:
			break
		if not isinstance(raw, dict):
			rejected["invalid"] += 1
			continue
		subject = str(raw.get("subject") or "").strip()[:500]
		obj = str(raw.get("object") or "").strip()[:500]
		evidence = str(raw.get("evidence") or "").strip()
		if not subject or not obj or not evidence:
			rejected["invalid"] += 1
			continue
		if subject.casefold() == obj.casefold():
			rejected["invalid"] += 1
			continue
		confidence = flt(raw.get("confidence"))
		if confidence < confidence_floor:
			rejected["low_confidence"] += 1
			continue
		# Subject, object, and the supporting sentence must all be real.
		evidence_offset = find_grounded_offset(source_text, evidence)
		if (
			evidence_offset is None
			or find_grounded_offset(source_text, subject) is None
			or find_grounded_offset(source_text, obj) is None
		):
			rejected["ungrounded"] += 1
			continue
		key = (subject.casefold(), normalize_relationship_type(raw.get("predicate")), obj.casefold())
		if key in seen_relationships:
			continue
		seen_relationships.add(key)
		relationships.append(
			{
				"subject": subject,
				"object": obj,
				"relationship_type": normalize_relationship_type(raw.get("predicate")),
				"evidence_quote": evidence[:500],
				"first_offset": evidence_offset,
				"confidence": max(0.0, min(100.0, confidence)),
			}
		)

	return {"entities": entities, "relationships": relationships, "rejected": rejected}


def semantic_enabled() -> bool:
	"""Semantic extraction is opt-in: it costs model calls."""
	return bool(frappe.db.get_single_value("AI Platform Settings", "semantic_entities_enabled"))


def confidence_floor() -> float:
	configured = flt(frappe.db.get_single_value("AI Platform Settings", "semantic_confidence_floor"))
	return configured if configured > 0 else DEFAULT_CONFIDENCE_FLOOR


def extract_semantic(text: str, *, model: str | None = None, document: str | None = None) -> dict:
	"""Run one governed model call and return grounded, filtered results."""
	from ai_fr_hg.ai.engine import run_chat
	from ai_fr_hg.ai.intelligence import parse_json_response
	from ai_fr_hg.ai.providers.base import ChatMessage

	source = (text or "")[:MAX_SEMANTIC_TEXT]
	if not source.strip():
		return {"entities": [], "relationships": [], "rejected": {}, "model": None}

	result = run_chat(
		[ChatMessage(role="user", content=build_prompt(source))],
		model=model,
		json_schema=_SCHEMA,
		operation="Semantic Extraction",
		reference_doctype="AI Document" if document else None,
		reference_name=document,
	)
	payload = parse_json_response(result.content)
	parsed = parse_semantic_payload(payload, source, confidence_floor())
	parsed["model"] = getattr(result, "model", None) or model
	return parsed


# ---------------------------------------------------------------------------
# Persistence — idempotent, mirroring `ai.patterns.scan_document`.
# ---------------------------------------------------------------------------


def scan_document_semantic(document: str, *, model: str | None = None) -> dict:
	"""Extract and sync semantic entities and relationships for one document.

	Idempotent: rows are keyed by canonical identity and rescans update in
	place. Never rewrites the document's own extracted content.
	"""
	row = frappe.db.get_value(
		"AI Document",
		document,
		["name", "content", "knowledge_base", "checksum"],
		as_dict=True,
	)
	if row is None:
		frappe.throw(_("AI Document {0} does not exist.").format(document), frappe.DoesNotExistError)

	outcome = extract_semantic(row.content or "", model=model, document=document)
	checksum = row.checksum or ""
	model_used = outcome.get("model")

	created = updated = 0
	existing_entities = {
		(item.entity_type, item.normalized_value): item.name
		for item in frappe.get_all(
			"AI Pattern Entity",
			filters={"document": document, "extraction_method": "semantic"},
			fields=["name", "entity_type", "normalized_value"],
		)
	}
	touched: set[str] = set()

	for entity in outcome["entities"]:
		key = (entity["entity_type"], entity["normalized_value"])
		patch = {
			"value": entity["value"],
			"occurrences": entity["occurrences"],
			"first_offset": entity["first_offset"],
			"context_quote": entity["context_quote"],
			"confidence": entity["confidence"],
			"model_used": model_used,
			"source_checksum": checksum,
		}
		name = existing_entities.get(key)
		if name:
			frappe.db.set_value("AI Pattern Entity", name, patch, update_modified=False)
			touched.add(name)
			updated += 1
			continue
		doc = frappe.new_doc("AI Pattern Entity")
		doc.document = document
		doc.knowledge_base = row.knowledge_base
		doc.entity_type = entity["entity_type"]
		doc.extraction_method = "semantic"
		doc.normalized_value = entity["normalized_value"]
		doc.update(patch)
		try:
			doc.insert(ignore_permissions=True)
		except frappe.DuplicateEntryError:
			name = frappe.db.get_value(
				"AI Pattern Entity",
				{
					"document": document,
					"entity_type": entity["entity_type"],
					"normalized_value": entity["normalized_value"],
				},
				"name",
			)
			if not name:
				raise
			frappe.db.set_value("AI Pattern Entity", name, patch, update_modified=False)
			touched.add(name)
			updated += 1
			continue
		touched.add(doc.name)
		created += 1

	removed = 0
	for name in existing_entities.values():
		if name not in touched:
			frappe.db.delete("AI Pattern Entity", name)
			removed += 1

	relationships_written = _sync_relationships(
		document, row.knowledge_base, outcome["relationships"], checksum, model_used
	)

	return {
		"document": document,
		"entities": len(outcome["entities"]),
		"created": created,
		"updated": updated,
		"removed": removed,
		"relationships": relationships_written,
		"rejected": outcome.get("rejected", {}),
		"model": model_used,
	}


def _sync_relationships(
	document: str, knowledge_base: str, relationships: list[dict], checksum: str, model_used
) -> int:
	"""Replace this document's relationship rows with the current extraction."""
	existing = {
		(item.subject.casefold(), item.relationship_type, item.object.casefold()): item.name
		for item in frappe.get_all(
			"AI Entity Relationship",
			filters={"document": document},
			fields=["name", "subject", "relationship_type", "object"],
		)
	}
	touched: set[str] = set()
	written = 0

	for relationship in relationships:
		key = (
			relationship["subject"].casefold(),
			relationship["relationship_type"],
			relationship["object"].casefold(),
		)
		patch = {
			"evidence_quote": relationship["evidence_quote"],
			"first_offset": relationship["first_offset"],
			"confidence": relationship["confidence"],
			"model_used": model_used,
			"source_checksum": checksum,
		}
		name = existing.get(key)
		if name:
			frappe.db.set_value("AI Entity Relationship", name, patch, update_modified=False)
			touched.add(name)
			written += 1
			continue
		doc = frappe.new_doc("AI Entity Relationship")
		doc.document = document
		doc.knowledge_base = knowledge_base
		doc.subject = relationship["subject"]
		doc.object = relationship["object"]
		doc.relationship_type = relationship["relationship_type"]
		doc.update(patch)
		doc.insert(ignore_permissions=True)
		touched.add(doc.name)
		written += 1

	for name in existing.values():
		if name not in touched:
			frappe.db.delete("AI Entity Relationship", name)

	return written


def handle_document_trashed(doc, method: str | None = None) -> None:
	"""Doc-event cascade: remove relationship rows with their document."""
	if doc and doc.name:
		frappe.db.delete("AI Entity Relationship", {"document": doc.name})


# ---------------------------------------------------------------------------
# Background backfill (doubly opt-in: platform enabled + semantic enabled).
# ---------------------------------------------------------------------------

#: Small batch: every document in a batch costs at least one model call.
_SCHEDULER_BATCH = 20


def scan_pending_documents_semantic(limit: int = _SCHEDULER_BATCH) -> list[dict]:
	"""Scan indexed documents whose content has no current semantic rows.

	A document is considered current when it already has semantic rows written
	at its present checksum, so re-running the scheduler is cheap and does not
	re-spend model quota. Failures are logged per document and never abort the
	batch.
	"""
	candidates = frappe.db.sql(
		"""
		select doc.name, doc.checksum
		from `tabAI Document` doc
		where doc.status = 'Indexed'
		  and ifnull(doc.content, '') != ''
		  and not exists (
			select 1 from `tabAI Pattern Entity` pe
			where pe.document = doc.name
			  and pe.extraction_method = 'semantic'
			  and pe.source_checksum = ifnull(doc.checksum, '')
		  )
		order by doc.modified desc
		limit %(limit)s
		""",
		{"limit": max(1, cint(limit))},
		as_dict=True,
	)

	results = []
	for candidate in candidates:
		try:
			results.append(scan_document_semantic(candidate.name))
		except Exception:
			frappe.log_error(
				title=f"AI semantic scan failed: {candidate.name}",
				message=frappe.get_traceback(),
			)
	return results
