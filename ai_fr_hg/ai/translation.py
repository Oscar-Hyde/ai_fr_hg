# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Offline document translation between Arabic, English and Hebrew.

This is the orchestration half of the translation feature: it turns the pure
helpers in :mod:`ai_fr_hg.ai.translation_utils` into a governed platform
operation with model resolution, translation memory, quality gating, audit and
background execution. Everything runs against the same local runtimes as the
rest of the platform - no text ever leaves the machine.

The pipeline for one document is:

1. **Normalise** the extracted text (fold Arabic/Hebrew presentation forms,
   strip bidi controls) and detect the source language when it is not given.
2. **Segment** it into structure-preserving blocks that reassemble exactly.
3. **Reuse** any segment already translated for the same language pair from
   the translation memory, which makes repeated boilerplate free and keeps
   terminology identical across a corpus.
4. **Protect** numbers, URLs, identifiers, code and glossary terms behind
   sentinels the model must copy verbatim.
5. **Translate** the remaining segments in context-sized batches at
   temperature 0, with a per-segment fallback when a batch response is
   incomplete.
6. **Score** every segment locally (placeholders, script, length, glossary,
   refusals, repetition) and **repair** the ones that fail, once, with a
   stricter prompt that names the defect.
7. Optionally **verify** a sample by back-translating it and comparing the
   result to the source with the local embedding model.
8. **Reassemble**, persist segments, and optionally index the translation as a
   searchable `AI Document` of its own.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field

import frappe
from frappe import _
from frappe.utils import cint, flt, now_datetime

from ai_fr_hg.ai.deadline import get_deadline
from ai_fr_hg.ai.engine import resolve_model, run_chat
from ai_fr_hg.ai.exceptions import DeadlineExceededError
from ai_fr_hg.ai.language import detect_languages
from ai_fr_hg.ai.translation_utils import (
	DEFAULT_SEGMENT_CHARACTERS,
	ISSUE_MESSAGES,
	REVIEW_THRESHOLD,
	SUPPORTED_LANGUAGES,
	GlossaryEntry,
	Segment,
	aggregate_score,
	applicable_glossary,
	assess_translation,
	build_batch_prompt,
	build_single_prompt,
	build_system_prompt,
	decode_separator,
	is_supported,
	language_label,
	memory_fingerprint,
	normalise_language,
	normalise_source_text,
	parse_batch_response,
	plan_batches,
	protect_placeholders,
	reassemble,
	resolve_glossary,
	restore_placeholders,
	segment_text,
	strip_model_preamble,
	summarise_issues,
	text_direction,
)
from ai_fr_hg.utils.authority import as_user, assert_valid_authority

#: A translation call needs at least this much of the remaining turn budget.
MIN_CALL_SECONDS = 8.0

#: Hard ceiling on one inline (interactive) translation request.
MAX_INLINE_CHARACTERS = 20000


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass
class TranslationOptions:
	"""Every knob of one translation run, already resolved to concrete values."""

	target_language: str
	source_language: str = ""
	model: str | None = None
	glossary: str | None = None
	tone: str = "Neutral"
	domain: str = ""
	preserve_formatting: bool = True
	segment_characters: int = DEFAULT_SEGMENT_CHARACTERS
	batch_characters: int = 4000
	batch_segments: int = 6
	quality_checks: bool = True
	repair_pass: bool = True
	use_translation_memory: bool = True
	back_translation_samples: int = 0
	knowledge_base: str | None = None
	extra_instructions: str = ""

	@classmethod
	def build(cls, target_language: str, **overrides) -> TranslationOptions:
		"""Merge caller arguments over the platform's configured defaults."""
		settings = _settings()
		target = normalise_language(target_language) or normalise_language(
			settings.get("default_target_language")
		)
		if not target:
			frappe.throw(
				_("Choose a target language: {0}.").format(
					", ".join(language_label(code) for code in SUPPORTED_LANGUAGES)
				)
			)
		if not is_supported(target):
			frappe.throw(_("Translation to {0} is not supported.").format(target_language))

		options = cls(
			target_language=target,
			model=settings.get("default_translation_model") or None,
			glossary=settings.get("default_glossary") or None,
			segment_characters=cint(settings.get("translation_segment_characters"))
			or DEFAULT_SEGMENT_CHARACTERS,
			batch_segments=cint(settings.get("translation_batch_segments")) or 6,
			quality_checks=bool(cint(settings.get("translation_quality_checks", 1))),
			repair_pass=bool(cint(settings.get("translation_repair_pass", 1))),
			use_translation_memory=bool(cint(settings.get("translation_memory_enabled", 1))),
			back_translation_samples=cint(settings.get("translation_back_translation_samples")),
		)
		for key, value in overrides.items():
			if value in (None, ""):
				continue
			if hasattr(options, key):
				setattr(options, key, value)

		options.source_language = normalise_language(options.source_language)
		options.segment_characters = cint(options.segment_characters) or DEFAULT_SEGMENT_CHARACTERS
		options.batch_characters = max(cint(options.batch_characters) or 4000, options.segment_characters)
		options.batch_segments = max(cint(options.batch_segments) or 6, 1)
		return options


def _settings() -> dict:
	"""Translation-relevant platform settings, tolerant of an un-migrated site."""
	try:
		doc = frappe.get_cached_doc("AI Platform Settings")
	except Exception:
		return {}
	keys = (
		"default_target_language",
		"default_translation_model",
		"default_glossary",
		"translation_segment_characters",
		"translation_batch_segments",
		"translation_quality_checks",
		"translation_repair_pass",
		"translation_memory_enabled",
		"translation_back_translation_samples",
		"translation_index_output",
		"translation_enabled",
	)
	return {key: doc.get(key) for key in keys if doc.get(key) is not None}


def translation_enabled() -> bool:
	value = _settings().get("translation_enabled")
	return True if value is None else bool(cint(value))


def supported_languages() -> list[dict]:
	"""The languages this platform can translate between, for UI and tools."""
	from ai_fr_hg.ai.translation_utils import LANGUAGES

	return [
		{
			"code": code,
			"name": LANGUAGES[code].name,
			"endonym": LANGUAGES[code].endonym,
			"direction": LANGUAGES[code].direction,
		}
		for code in SUPPORTED_LANGUAGES
	]


def detect_source_language(text: str, fallback: str = "") -> str:
	"""Pick the dominant supported language of `text`.

	The platform's detector returns every language present in a mixed
	document, ordered by how much of the sample each script occupies; the
	first supported one is the language we are translating *from*.
	"""
	for code in detect_languages(text):
		if is_supported(code):
			return normalise_language(code)
	return normalise_language(fallback)


# ---------------------------------------------------------------------------
# Glossary
# ---------------------------------------------------------------------------


def load_glossary(glossary: str | None, source_language: str, target_language: str) -> list[GlossaryEntry]:
	"""Read a stored glossary and resolve it for one direction."""
	if not glossary:
		return []
	try:
		doc = frappe.get_cached_doc("AI Translation Glossary", glossary)
	except frappe.DoesNotExistError:
		return []
	if not doc.enabled:
		return []
	from ai_fr_hg.utils.permissions import has_document_permission

	if not has_document_permission(doc, "read", user=frappe.session.user):
		frappe.throw(
			_("You cannot use glossary {0}.").format(glossary),
			frappe.PermissionError,
		)
	rows = [
		{
			"term_en": row.term_en,
			"term_ar": row.term_ar,
			"term_he": row.term_he,
			"do_not_translate": row.do_not_translate,
			"case_sensitive": row.case_sensitive,
			"notes": row.notes,
		}
		for row in doc.get("terms") or []
	]
	return resolve_glossary(rows, source_language, target_language)


# ---------------------------------------------------------------------------
# Translation memory
# ---------------------------------------------------------------------------


def authorized_memory_scope(knowledge_base: str | None) -> str | None:
	"""Return a KB name the current user may use for translation memory.

	No knowledge base is never global memory: it is no memory.
	"""
	scope = (knowledge_base or "").strip()
	if not scope:
		return None
	from ai_fr_hg.utils.permissions import _knowledge_base_access

	if not _knowledge_base_access(scope, frappe.session.user, write=False):
		return None
	return scope


def _memory_lookup(fingerprints: list[str], knowledge_base: str | None) -> dict[str, str]:
	"""Previously approved translations for these exact source segments.

	Requires an authorized knowledge-base scope. An empty or unauthorized
	scope returns no memory, never every corpus.
	"""
	scope = authorized_memory_scope(knowledge_base)
	if not scope or not fingerprints:
		return {}
	filters: dict = {
		"fingerprint": ["in", fingerprints],
		"translated_text": ["!=", ""],
		"status": ["in", ["Translated", "Reviewed", "Reused"]],
	}
	parents = frappe.get_all(
		"AI Translation",
		filters={"knowledge_base": scope, "status": ["in", ["Completed", "Needs Review"]]},
		pluck="name",
		limit_page_length=500,
		order_by="modified desc",
	)
	if not parents:
		return {}
	filters["parent"] = ["in", parents]

	rows = frappe.get_all(
		"AI Translation Segment",
		filters=filters,
		fields=["fingerprint", "translated_text", "quality_score"],
		order_by="quality_score desc, modified desc",
		limit_page_length=len(fingerprints) * 4,
	)
	memory: dict[str, str] = {}
	for row in rows:
		if row.fingerprint not in memory and (row.translated_text or "").strip():
			memory[row.fingerprint] = row.translated_text
	return memory


# ---------------------------------------------------------------------------
# Core translation
# ---------------------------------------------------------------------------


@dataclass
class TranslationOutcome:
	"""Everything one translation run produced."""

	text: str = ""
	source_language: str = ""
	target_language: str = ""
	direction: str = "ltr"
	segments: list[Segment] = field(default_factory=list)
	quality_score: float = 0.0
	issues: dict[str, int] = field(default_factory=dict)
	model: str | None = None
	duration_ms: int = 0
	total_tokens: int = 0
	memory_hits: int = 0
	flagged: int = 0
	partial: bool = False
	verification: dict = field(default_factory=dict)

	def as_dict(self) -> dict:
		return {
			"text": self.text,
			"source_language": self.source_language,
			"target_language": self.target_language,
			"direction": self.direction,
			"quality_score": self.quality_score,
			"issues": self.issues,
			"model": self.model,
			"duration_ms": self.duration_ms,
			"total_tokens": self.total_tokens,
			"memory_hits": self.memory_hits,
			"flagged": self.flagged,
			"segment_count": len(self.segments),
			"partial": self.partial,
			"verification": self.verification,
		}


@dataclass
class _Prepared:
	"""A segment with its masked text and the terminology that applies to it."""

	segment: Segment
	masked: str
	tokens: dict[str, str]
	glossary: list[GlossaryEntry]
	missing: list[str] = field(default_factory=list)


def translate_text(
	text: str,
	target_language: str,
	source_language: str | None = None,
	*,
	reference_doctype: str | None = None,
	reference_name: str | None = None,
	progress: Callable[[int, int], None] | None = None,
	**overrides,
) -> TranslationOutcome:
	"""Translate arbitrary text, segment by segment, with quality gating."""
	if not translation_enabled():
		frappe.throw(_("Translation is disabled in AI Platform Settings."))

	options = TranslationOptions.build(target_language, source_language=source_language or "", **overrides)
	started = time.monotonic()

	source = normalise_source_text(text)
	if not source.strip():
		return TranslationOutcome(target_language=options.target_language)

	detected = options.source_language or detect_source_language(source)
	if not detected:
		frappe.throw(_("Could not detect the source language. Choose Arabic, English or Hebrew explicitly."))
	if detected == options.target_language:
		frappe.throw(_("The text is already in {0}.").format(language_label(options.target_language)))
	options.source_language = detected

	model_doc = resolve_model(options.model, "Chat")
	glossary = load_glossary(options.glossary, detected, options.target_language)

	segments = segment_text(source, max_characters=options.segment_characters)
	pending = [segment for segment in segments if segment.translatable]
	for segment in segments:
		if not segment.translatable:
			segment.status = "Copied"
			segment.quality_score = 100.0

	memory_hits = 0
	if options.use_translation_memory and pending:
		fingerprints = {
			segment.index: memory_fingerprint(
				segment.source,
				detected,
				options.target_language,
				knowledge_base=options.knowledge_base,
				glossary=options.glossary,
				tone=options.tone,
				domain=options.domain,
			)
			for segment in pending
		}
		memory = _memory_lookup(sorted(set(fingerprints.values())), options.knowledge_base)
		for segment in list(pending):
			cached = memory.get(fingerprints[segment.index])
			if not cached:
				continue
			segment.translated = cached
			segment.status = "Reused"
			segment.reused = True
			segment.quality_score = 100.0
			pending.remove(segment)
			memory_hits += 1

	prepared = [
		_Prepared(
			segment=segment,
			**_mask(segment, glossary),
		)
		for segment in pending
	]

	tokens_used = 0
	partial = False
	deadline = get_deadline()

	lookup = {item.segment.index: item for item in prepared}
	for batch in plan_batches(
		[item.segment for item in prepared],
		max_characters=options.batch_characters,
		max_segments=options.batch_segments,
	):
		if deadline and not deadline.allows(MIN_CALL_SECONDS):
			partial = True
			break
		items = [lookup[segment.index] for segment in batch]
		try:
			tokens_used += _translate_batch(items, options, model_doc, reference_doctype, reference_name)
		except DeadlineExceededError:
			partial = True
			break
		if progress:
			done = sum(1 for segment in segments if segment.translated)
			progress(done, len(segments))

	if options.quality_checks:
		_score(prepared, options)
		if options.repair_pass:
			tokens_used += _repair(prepared, options, model_doc, reference_doctype, reference_name)

	verification = {}
	if options.back_translation_samples and not partial:
		verification = verify_by_back_translation(
			[item.segment for item in prepared],
			options,
			model_doc,
			samples=options.back_translation_samples,
		)

	flagged = sum(
		1
		for segment in segments
		if segment.translatable and segment.status in {"Flagged", "Failed", "Pending"}
	)

	return TranslationOutcome(
		text=reassemble(segments),
		source_language=detected,
		target_language=options.target_language,
		direction=text_direction(options.target_language),
		segments=segments,
		quality_score=aggregate_score(segments),
		issues=summarise_issues(segments),
		model=model_doc.name,
		duration_ms=int((time.monotonic() - started) * 1000),
		total_tokens=tokens_used,
		memory_hits=memory_hits,
		flagged=flagged,
		partial=partial,
		verification=verification,
	)


def _mask(segment: Segment, glossary: list[GlossaryEntry]) -> dict:
	"""Protect untranslatable spans and pick the terminology for this segment."""
	applicable = applicable_glossary(glossary, segment.source)
	keep = [entry.source_term for entry in applicable if entry.do_not_translate]
	protected = protect_placeholders(segment.source, do_not_translate=keep)
	return {
		"masked": protected.text,
		"tokens": protected.tokens,
		"glossary": [entry for entry in applicable if not entry.do_not_translate],
	}


def _system_prompt(options: TranslationOptions, glossary: list[GlossaryEntry], extra: str = "") -> str:
	return build_system_prompt(
		options.source_language,
		options.target_language,
		tone=options.tone,
		domain=options.domain,
		preserve_formatting=options.preserve_formatting,
		glossary=glossary,
		extra_instructions=" ".join(part for part in (options.extra_instructions, extra) if part),
	)


def _call_model(
	system_prompt: str,
	user_prompt: str,
	model_doc,
	reference_doctype: str | None,
	reference_name: str | None,
) -> tuple[str, int]:
	"""One deterministic translation call through the platform engine."""
	result = run_chat(
		[
			{"role": "system", "content": system_prompt},
			{"role": "user", "content": user_prompt},
		],
		model=model_doc.name,
		options={"temperature": 0, "top_p": 1},
		operation="Translate",
		reference_doctype=reference_doctype,
		reference_name=reference_name,
	)
	return result.content or "", cint(result.total_tokens)


def _translate_batch(
	items: list[_Prepared],
	options: TranslationOptions,
	model_doc,
	reference_doctype: str | None,
	reference_name: str | None,
) -> int:
	"""Translate one batch, falling back to single calls for anything missed."""
	glossary: list[GlossaryEntry] = []
	seen: set[tuple[str, str]] = set()
	for item in items:
		for entry in item.glossary:
			key = (entry.source_term, entry.target_term)
			if key not in seen:
				seen.add(key)
				glossary.append(entry)

	tokens = 0
	system_prompt = _system_prompt(options, glossary)

	if len(items) > 1:
		masked_segments = [Segment(index=item.segment.index, source=item.masked) for item in items]
		content, used = _call_model(
			system_prompt,
			build_batch_prompt(masked_segments),
			model_doc,
			reference_doctype,
			reference_name,
		)
		tokens += used
		parsed = parse_batch_response(content, [item.segment.index for item in items])
		for item in items:
			if translated := parsed.get(item.segment.index):
				_store(item, translated)

	for item in items:
		if item.segment.translated:
			continue
		content, used = _call_model(
			_system_prompt(options, item.glossary),
			build_single_prompt(item.masked),
			model_doc,
			reference_doctype,
			reference_name,
		)
		tokens += used
		_store(item, content)

	return tokens


def _store(item: _Prepared, raw: str) -> None:
	"""Clean, unmask and attach a model response to its segment."""
	cleaned = strip_model_preamble(raw)
	restored, missing = restore_placeholders(cleaned, item.tokens)
	item.segment.translated = restored
	item.segment.status = "Translated" if restored.strip() else "Failed"
	item.segment.issue_codes = []
	item.segment.issues = []
	item.segment.quality_score = 0.0
	item.missing = missing


def _score(prepared: list[_Prepared], options: TranslationOptions) -> None:
	"""Assess every translated segment and mark the doubtful ones."""
	for item in prepared:
		segment = item.segment
		if not segment.translated:
			segment.status = "Failed"
			segment.issue_codes = ["empty"]
			segment.issues = [ISSUE_MESSAGES["empty"]]
			segment.quality_score = 0.0
			continue
		report = assess_translation(
			segment.source,
			segment.translated,
			options.source_language,
			options.target_language,
			missing_tokens=item.missing,
			glossary=item.glossary,
		)
		segment.quality_score = report.score
		segment.issue_codes = report.issues
		segment.issues = report.describe()
		segment.status = "Translated" if report.ok else "Flagged"


def _repair(
	prepared: list[_Prepared],
	options: TranslationOptions,
	model_doc,
	reference_doctype: str | None,
	reference_name: str | None,
) -> int:
	"""Retry flagged segments once, telling the model exactly what went wrong.

	The retry is only kept when it scores better than the first attempt, so a
	repair can never make a document worse.
	"""
	tokens = 0
	deadline = get_deadline()
	for item in prepared:
		segment = item.segment
		if segment.status not in {"Flagged", "Failed"}:
			continue
		if deadline and not deadline.allows(MIN_CALL_SECONDS):
			break

		complaint = " ".join(segment.issues) or _("The previous attempt was rejected by quality checks.")
		extra = (
			f"A previous attempt failed review: {complaint} "
			"Translate the whole text again, fixing that defect. Output only the translation."
		)
		try:
			content, used = _call_model(
				_system_prompt(options, item.glossary, extra),
				build_single_prompt(item.masked),
				model_doc,
				reference_doctype,
				reference_name,
			)
		except Exception as error:
			frappe.log_error(title="AI translation repair", message=str(error))
			continue
		tokens += used

		previous = (
			segment.translated,
			segment.quality_score,
			segment.issue_codes,
			segment.issues,
			segment.status,
		)
		_store(item, content)
		_score([item], options)
		if segment.quality_score <= previous[1]:
			(
				segment.translated,
				segment.quality_score,
				segment.issue_codes,
				segment.issues,
				segment.status,
			) = previous
	return tokens


def verify_by_back_translation(
	segments: list[Segment],
	options: TranslationOptions,
	model_doc,
	samples: int = 3,
) -> dict:
	"""Translate a sample back and compare it to the source semantically.

	The comparison uses the platform's local embedding model, so verification
	stays entirely offline. A low similarity does not fail the run; it flags
	the segment for a human to look at.
	"""
	from ai_fr_hg.ai.engine import run_embedding
	from ai_fr_hg.ai.vector import cosine_similarity

	candidates = [segment for segment in segments if segment.translated and segment.translatable]
	if not candidates or samples <= 0:
		return {}

	# Longest segments first: they carry the most risk and the most meaning.
	candidates.sort(key=lambda segment: len(segment.source), reverse=True)
	sample = candidates[: max(1, min(cint(samples), len(candidates)))]

	reverse_prompt = build_system_prompt(
		options.target_language,
		options.source_language,
		tone=options.tone,
		domain=options.domain,
		preserve_formatting=False,
	)

	checked: list[dict] = []
	tokens_used = 0
	for segment in sample:
		try:
			content, used = _call_model(
				reverse_prompt, build_single_prompt(segment.translated), model_doc, None, None
			)
			tokens_used += used
			vectors = run_embedding([segment.source, strip_model_preamble(content)], operation="Embedding")
			if len(vectors) != 2:
				continue
			similarity = round(cosine_similarity(vectors[0], vectors[1]), 4)
		except Exception as error:
			frappe.log_error(title="AI translation verification", message=str(error))
			continue

		checked.append({"segment": segment.index, "similarity": similarity, "tokens": used})
		if similarity < 0.75:
			segment.status = "Flagged"
			message = _("Back-translation similarity is only {0}.").format(similarity)
			if message not in segment.issues:
				segment.issues.append(message)

	if not checked:
		return {
			"sampled": 0,
			"tokens": tokens_used,
			"issues": ["verification produced no comparable samples"],
		}
	return {
		"sampled": len(checked),
		"mean_similarity": round(sum(item["similarity"] for item in checked) / len(checked), 4),
		"segments": checked,
		"tokens": tokens_used,
	}


# ---------------------------------------------------------------------------
# Document translation
# ---------------------------------------------------------------------------


def create_translation(
	document: str,
	target_language: str,
	*,
	source_language: str | None = None,
	model: str | None = None,
	glossary: str | None = None,
	tone: str = "Neutral",
	domain: str = "",
	preserve_formatting: bool = True,
	index_output: bool | None = None,
) -> str:
	"""Create a queued `AI Translation` for an extracted document."""
	doc = frappe.get_doc("AI Document", document)
	doc.check_permission("read")

	if not (doc.content or "").strip():
		frappe.throw(
			_("Document {0} has no extracted text yet. Process it before translating.").format(document)
		)

	target = normalise_language(target_language)
	if not is_supported(target):
		frappe.throw(_("Translation to {0} is not supported.").format(target_language))

	translation = frappe.new_doc("AI Translation")
	translation.update(
		{
			"source_document": doc.name,
			"knowledge_base": doc.knowledge_base,
			"title": f"{doc.title} → {language_label(target)}",
			"source_language": normalise_language(source_language) or "",
			"target_language": target,
			"model": model,
			"glossary": glossary,
			"tone": tone,
			"domain": domain,
			"preserve_formatting": 1 if preserve_formatting else 0,
			"index_output": 1
			if (index_output if index_output is not None else _settings().get("translation_index_output"))
			else 0,
			"status": "Draft",
			"source_text": doc.content,
		}
	)
	translation.insert()
	return translation.name


def enqueue_translation(translation: str, queue: str | None = None) -> dict:
	"""Run a translation on a background worker under the requesting user."""
	doc = frappe.get_doc("AI Translation", translation)
	doc.check_permission("write")

	if doc.status in {"Queued", "Translating"}:
		return {"translation": doc.name, "status": doc.status, "job_id": doc.job_id}

	job_id = f"ai-translation::{doc.name}"
	resolved_queue = queue or frappe.db.get_single_value("AI Platform Settings", "processing_queue") or "long"
	doc.db_set(
		{
			"status": "Queued",
			"requested_by": frappe.session.user,
			"requested_on": now_datetime(),
			"job_id": job_id,
			"error_message": None,
		},
		update_modified=False,
	)
	try:
		frappe.enqueue(
			"ai_fr_hg.ai.translation.run_translation",
			queue=resolved_queue,
			timeout=3600,
			job_id=job_id,
			deduplicate=True,
			enqueue_after_commit=True,
			translation=doc.name,
			requested_by=frappe.session.user,
		)
	except Exception as error:
		doc.db_set(
			{"status": "Failed", "error_message": str(error)[:1000]},
			update_modified=False,
		)
		raise
	return {"translation": doc.name, "status": "Queued", "job_id": job_id}


def _owns_worker_transaction(translation: str) -> bool:
	"""Whether this call is the background job that owns the transaction.

	Only then may it commit: a foreground call (a form action, an API request
	or a test) must leave transaction control to its caller.
	"""
	job = getattr(frappe.local, "job", None)
	return bool(
		job
		and job.get("method") == "ai_fr_hg.ai.translation.run_translation"
		and (job.get("kwargs") or {}).get("translation") == translation
	)


def _translation_user(user: str):
	"""Canonical worker identity: same ``as_user`` as ingestion and pipelines."""
	return as_user(user)


def _assert_not_cancelled(translation: str) -> None:
	if frappe.db.get_value("AI Translation", translation, "cancel_requested"):
		frappe.throw(_("Translation was cancelled."), frappe.ValidationError)


def cancel_translation(translation: str, requested_by: str | None = None) -> dict:
	"""Cancel a queued or in-flight translation under requester authority."""
	user = assert_valid_authority(requested_by or frappe.session.user)
	with as_user(user):
		doc = frappe.get_doc("AI Translation", translation)
		doc.check_permission("write")
		if doc.status in {"Completed", "Needs Review", "Failed", "Cancelled"}:
			return {"translation": translation, "status": doc.status, "cancelled": False}
		frappe.db.set_value(
			"AI Translation",
			translation,
			{
				"cancel_requested": 1,
				"status": "Cancelled",
				"processing_message": "Cancellation requested",
				"completed_on": now_datetime(),
			},
			update_modified=False,
		)
		frappe.publish_realtime(
			"ai_translation_progress",
			{"translation": translation, "status": "Cancelled", "message": "Cancellation requested"},
			user=user,
		)
	return {"translation": translation, "status": "Cancelled", "cancelled": True}


def run_translation(translation: str, requested_by: str | None = None) -> dict:
	"""Execute a stored translation under its requester and restore authority."""
	authority = frappe.db.get_value("AI Translation", translation, ["requested_by", "owner"], as_dict=True)
	if not authority:
		frappe.throw(_("Translation {0} does not exist.").format(translation), frappe.DoesNotExistError)
	user = assert_valid_authority(requested_by or authority.requested_by or authority.owner)
	with as_user(user):
		doc = frappe.get_doc("AI Translation", translation)
		doc.check_permission("write")
		_assert_not_cancelled(translation)
		return _run_translation(doc, durable=_owns_worker_transaction(translation))


def _run_translation(doc, *, durable: bool) -> dict:
	"""Persist one translation while the caller owns the requester context."""
	from ai_fr_hg.ai.logging import write_audit_log

	doc.db_set(
		{
			"status": "Translating",
			"error_message": None,
			"processing_message": "Translating",
			"processing_progress": 1,
		},
		update_modified=False,
	)
	source_text = doc.source_text or frappe.db.get_value("AI Document", doc.source_document, "content")

	def _progress(done: int, total: int) -> None:
		_assert_not_cancelled(doc.name)
		pct = 5 + int(90 * done / max(total, 1))
		frappe.db.set_value(
			"AI Translation",
			doc.name,
			{"processing_progress": pct, "processing_message": f"{done}/{total} segments"},
			update_modified=False,
		)
		frappe.publish_realtime(
			"ai_translation_progress",
			{"translation": doc.name, "progress": pct, "done": done, "total": total},
			user=frappe.session.user,
		)

	try:
		_assert_not_cancelled(doc.name)
		outcome = translate_text(
			source_text,
			doc.target_language,
			doc.source_language,
			model=doc.model,
			glossary=doc.glossary,
			tone=doc.tone or "Neutral",
			domain=doc.domain or "",
			preserve_formatting=bool(doc.preserve_formatting),
			knowledge_base=doc.knowledge_base,
			reference_doctype="AI Translation",
			reference_name=doc.name,
			progress=_progress,
		)
	except Exception as error:
		if durable:
			frappe.db.rollback()
		doc = frappe.get_doc("AI Translation", doc.name)
		doc.db_set(
			{"status": "Failed", "error_message": str(error)[:1000], "completed_on": now_datetime()},
			update_modified=False,
		)
		frappe.log_error(title="AI translation failed", message=frappe.get_traceback())
		write_audit_log(
			action="Translation Failed",
			category="Execution",
			severity="Warning",
			message=str(error)[:500],
			reference_doctype="AI Translation",
			reference_name=doc.name,
		)
		if durable:
			frappe.db.commit()  # nosemgrep: frappe-manual-commit
		return {"translation": doc.name, "status": "Failed", "error": str(error)}

	_persist_outcome(doc, outcome)

	if doc.index_output and outcome.text.strip():
		try:
			index_translation(doc.name)
		except Exception:
			frappe.log_error(title="AI translation indexing", message=frappe.get_traceback())

	write_audit_log(
		action="Document Translated",
		category="Data",
		message=_("Translated {0} into {1}.").format(
			doc.source_document or _("text"), language_label(outcome.target_language)
		),
		details={
			"segments": len(outcome.segments),
			"quality_score": outcome.quality_score,
			"memory_hits": outcome.memory_hits,
			"flagged": outcome.flagged,
			"model": outcome.model,
		},
		reference_doctype="AI Translation",
		reference_name=doc.name,
	)
	if durable:
		# This worker owns the transaction, so the finished translation and its
		# audit entry become visible as soon as the job ends.
		frappe.db.commit()  # nosemgrep: frappe-manual-commit
	return {
		"translation": doc.name,
		"status": doc.status,
		"quality_score": outcome.quality_score,
		"flagged": outcome.flagged,
	}


def _persist_outcome(doc, outcome: TranslationOutcome) -> None:
	"""Write segments and metrics back onto the `AI Translation` record."""
	doc.set("segments", [])
	for segment in outcome.segments:
		row = segment.as_dict()
		row["fingerprint"] = memory_fingerprint(
			segment.source,
			outcome.source_language,
			outcome.target_language,
			knowledge_base=doc.knowledge_base,
			glossary=doc.glossary,
			tone=doc.tone,
			domain=doc.domain,
		)
		doc.append("segments", row)

	status = "Completed"
	if outcome.partial:
		status = "Needs Review"
	elif outcome.flagged:
		status = "Needs Review"
	elif outcome.quality_score < REVIEW_THRESHOLD:
		status = "Needs Review"

	doc.update(
		{
			"status": status,
			"processing_progress": 100,
			"processing_message": status,
			"cancel_requested": 0,
			"translated_text": outcome.text,
			"source_language": outcome.source_language,
			"direction": outcome.direction,
			"quality_score": outcome.quality_score,
			"segment_count": len(outcome.segments),
			"flagged_segments": outcome.flagged,
			"memory_hits": outcome.memory_hits,
			"model_used": outcome.model,
			"duration_ms": outcome.duration_ms,
			"total_tokens": outcome.total_tokens,
			"character_count": len(outcome.text),
			"issue_summary": frappe.as_json({"issues": outcome.issues, "verification": outcome.verification}),
			"completed_on": now_datetime(),
		}
	)
	doc.flags.ignore_permissions = True
	doc.save(ignore_permissions=True)


def retranslate_segment(translation: str, segment_index: int, instructions: str = "") -> dict:
	"""Re-run one segment, optionally with a human hint, and rescore it."""
	doc = frappe.get_doc("AI Translation", translation)
	doc.check_permission("write")

	row = next(
		(item for item in doc.get("segments") or [] if cint(item.segment_index) == cint(segment_index)),
		None,
	)
	if not row:
		frappe.throw(_("Segment {0} does not exist in this translation.").format(segment_index))

	options = TranslationOptions.build(
		doc.target_language,
		source_language=doc.source_language,
		model=doc.model,
		glossary=doc.glossary,
		tone=doc.tone or "Neutral",
		domain=doc.domain or "",
		preserve_formatting=bool(doc.preserve_formatting),
		knowledge_base=doc.knowledge_base,
		extra_instructions=instructions or "",
		use_translation_memory=False,
	)
	model_doc = resolve_model(options.model, "Chat")
	glossary = load_glossary(options.glossary, options.source_language, options.target_language)

	segment = Segment(index=cint(row.segment_index), source=row.source_text, kind=row.kind or "paragraph")
	item = _Prepared(segment=segment, **_mask(segment, glossary))
	content, tokens = _call_model(
		_system_prompt(options, item.glossary),
		build_single_prompt(item.masked),
		model_doc,
		"AI Translation",
		doc.name,
	)
	_store(item, content)
	_score([item], options)

	row.translated_text = segment.output
	row.status = segment.status
	row.quality_score = segment.quality_score
	row.issues = "\n".join(segment.issues)
	row.translated_characters = len(segment.output)
	row.reviewed = 0

	_refresh_document_text(doc)
	doc.save()
	return {
		"translation": doc.name,
		"segment_index": cint(segment_index),
		"translated_text": row.translated_text,
		"quality_score": row.quality_score,
		"issues": row.issues,
		"tokens": tokens,
	}


def _refresh_document_text(doc) -> None:
	"""Rebuild the full translated text and metrics from the segment rows."""
	segments = [
		Segment(
			index=cint(row.segment_index),
			source=row.source_text or "",
			separator=decode_separator(row.separator),
			kind=row.kind or "paragraph",
			translatable=bool(row.source_text),
			translated=row.translated_text or "",
			status=row.status or "Pending",
			quality_score=flt(row.quality_score),
			issues=[line for line in (row.issues or "").splitlines() if line],
		)
		for row in sorted(doc.get("segments") or [], key=lambda item: cint(item.segment_index))
	]
	doc.translated_text = reassemble(segments)
	doc.quality_score = aggregate_score(segments)
	doc.character_count = len(doc.translated_text)
	doc.flagged_segments = sum(1 for segment in segments if segment.status in {"Flagged", "Failed"})
	if not doc.flagged_segments and doc.status == "Needs Review":
		doc.status = "Completed"


def index_translation(translation: str) -> str:
	"""Store a completed translation as its own searchable `AI Document`."""
	from ai_fr_hg.ai.ingestion import ingest_text

	doc = frappe.get_doc("AI Translation", translation)
	doc.check_permission("read")

	if not (doc.translated_text or "").strip():
		frappe.throw(_("This translation has no text to index yet."))
	if doc.translated_document and frappe.db.exists("AI Document", doc.translated_document):
		return doc.translated_document

	source = (
		frappe.get_doc("AI Document", doc.source_document)
		if doc.source_document and frappe.db.exists("AI Document", doc.source_document)
		else None
	)
	title = f"{(source.title if source else doc.title)} [{language_label(doc.target_language)}]"

	document = ingest_text(
		text=doc.translated_text,
		knowledge_base=doc.knowledge_base or (source.knowledge_base if source else None),
		title=title[:140],
		folder=source.folder if source else None,
		language=doc.target_language,
	)
	frappe.db.set_value("AI Translation", doc.name, "translated_document", document, update_modified=False)
	return document
