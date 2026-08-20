# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

# Non-Latin confusables are intentional language vocabulary.
# ruff: noqa: RUF001

"""Detect the written language(s) of extracted document text.

No extra packages: script counts plus a few high-signal function words.

English, Arabic and Hebrew are first-class, including mixed documents
that combine any two or all three. PDF extractors often emit Arabic and
Hebrew presentation-form code points; those ranges are counted too.
"""

from __future__ import annotations

import re

_WORD = re.compile(r"[\w]+", re.UNICODE)

# Distinctive function words. Overlap between close languages is intentional;
# the highest hit count wins after a script gate.
_WORD_HINTS: dict[str, frozenset[str]] = {
	"bg": frozenset(
		{
			"и",
			"на",
			"за",
			"от",
			"с",
			"се",
			"да",
			"е",
			"не",
			"това",
			"като",
			"който",
			"която",
			"които",
			"със",
			"към",
			"при",
			"или",
			"ще",
			"са",
			"българия",
			"документ",
		}
	),
	"ru": frozenset(
		{
			"и",
			"в",
			"не",
			"на",
			"что",
			"это",
			"он",
			"как",
			"из",
			"его",
			"для",
			"от",
			"был",
			"она",
			"они",
		}
	),
	"uk": frozenset({"та", "що", "як", "це", "він", "вона", "для", "від", "або", "був"}),
	"en": frozenset(
		{
			"the",
			"and",
			"of",
			"to",
			"in",
			"for",
			"is",
			"that",
			"on",
			"with",
			"as",
			"by",
			"this",
			"from",
			"are",
		}
	),
	"de": frozenset({"und", "der", "die", "das", "den", "dem", "von", "mit", "ist", "nicht", "ein", "eine"}),
	"fr": frozenset({"le", "la", "les", "de", "des", "et", "un", "une", "est", "dans", "pour", "que"}),
	"es": frozenset({"el", "la", "los", "las", "de", "y", "en", "que", "un", "una", "por", "con"}),
	"it": frozenset({"il", "lo", "la", "di", "e", "che", "un", "una", "per", "con", "non", "sono"}),
	"ar": frozenset(
		{
			"في",
			"من",
			"على",
			"إلى",
			"ان",
			"أن",
			"هذا",
			"هذه",
			"التي",
			"الذي",
			"كان",
			"لا",
			"ما",
			"عن",
			"مع",
			"هو",
			"هي",
		}
	),
	"he": frozenset({"של", "את", "על", "לא", "כי", "זה", "זאת", "עם", "מן", "הוא", "היא", "הם", "או", "גם"}),
}

_LANGUAGE_NAMES = {
	"bg": "Bulgarian",
	"ru": "Russian",
	"uk": "Ukrainian",
	"en": "English",
	"de": "German",
	"fr": "French",
	"es": "Spanish",
	"it": "Italian",
	"el": "Greek",
	"ar": "Arabic",
	"he": "Hebrew",
	"zh": "Chinese",
	"ja": "Japanese",
	"ko": "Korean",
}

_NAME_TO_CODE = {name.lower(): code for code, name in _LANGUAGE_NAMES.items()}

# English / Arabic / Hebrew stay listed first when counts tie.
_FOCUS_ORDER = {"en": 0, "ar": 1, "he": 2}

_MIN_SAMPLE_CHARS = 12
_SCRIPT_ABS_MIN = 10
_SCRIPT_SHARE_MIN = 6
_SCRIPT_SHARE = 0.06


def parse_language_codes(value: str | None) -> list[str]:
	"""Split a stored `en,ar,he` (or `English + Arabic`) value into ISO codes."""
	raw = (value or "").strip().lower().replace("+", ",").replace(";", ",").replace("|", ",")
	if not raw:
		return []
	codes: list[str] = []
	for part in raw.split(","):
		part = part.strip()
		if not part:
			continue
		mapped = part if part in _LANGUAGE_NAMES else _NAME_TO_CODE.get(part, part)
		if mapped and mapped not in codes:
			codes.append(mapped)
	return codes


def language_name(code: str | None) -> str:
	"""Human label for one or more ISO 639-1 codes (`en,ar` → `English + Arabic`)."""
	codes = parse_language_codes(code)
	if not codes:
		return (code or "").strip()
	return " + ".join(_LANGUAGE_NAMES.get(item, item) for item in codes)


def resolve_document_language(stored: str | None, text: str | None) -> str:
	"""Prefer a stored ISO code list; otherwise detect from extracted text."""
	codes = parse_language_codes(stored)
	if codes:
		return ",".join(codes)
	return detect_language(text)


def detect_language(text: str | None) -> str:
	"""Return comma-separated ISO 639-1 codes, or empty when there is too little signal.

	Mixed English / Arabic / Hebrew documents keep every language that is
	present, ordered by how much of the sample each script occupies.
	"""
	return ",".join(detect_languages(text))


def detect_languages(text: str | None) -> list[str]:
	"""Return every language with enough signal, dominant script first."""
	sample = (text or "")[:8000]
	if len(sample.strip()) < _MIN_SAMPLE_CHARS:
		return []

	scripts = _script_counts(sample)
	letters = sum(scripts.values())
	if letters < 8:
		return []

	words = {token.lower() for token in _WORD.findall(sample)}
	found: list[tuple[int, str]] = []

	if _script_present(scripts["arabic"], letters):
		found.append((scripts["arabic"], "ar"))
	if _script_present(scripts["hebrew"], letters):
		found.append((scripts["hebrew"], "he"))
	if _script_present(scripts["latin"], letters):
		found.append((scripts["latin"], _best_word_match(words, ("en", "de", "fr", "es", "it")) or "en"))
	if _script_present(scripts["cyrillic"], letters):
		found.append((scripts["cyrillic"], _best_word_match(words, ("bg", "ru", "uk")) or "bg"))
	if _script_present(scripts["greek"], letters):
		found.append((scripts["greek"], "el"))

	hangul = scripts["hangul"]
	cjk = scripts["cjk"] + scripts["hiragana"] + scripts["katakana"]
	if _script_present(hangul, letters):
		found.append((hangul, "ko"))
	elif _script_present(cjk, letters):
		found.append((cjk, "ja" if scripts["hiragana"] or scripts["katakana"] else "zh"))

	found.sort(key=lambda item: (-item[0], _FOCUS_ORDER.get(item[1], 99)))
	codes: list[str] = []
	for _count, code in found:
		if code not in codes:
			codes.append(code)
	return codes


def _script_present(count: int, letters: int) -> bool:
	if count >= _SCRIPT_ABS_MIN:
		return True
	return count >= _SCRIPT_SHARE_MIN and count / max(letters, 1) >= _SCRIPT_SHARE


def _best_word_match(words: set[str], candidates: tuple[str, ...]) -> str:
	best = ""
	best_hits = 0
	for code in candidates:
		hits = len(words & _WORD_HINTS[code])
		if hits > best_hits:
			best = code
			best_hits = hits
	return best if best_hits >= 2 else ""


def _script_bucket(code: int) -> str | None:
	if 0x0041 <= code <= 0x024F:
		return "latin"
	if 0x0400 <= code <= 0x04FF:
		return "cyrillic"
	if 0x0370 <= code <= 0x03FF:
		return "greek"
	if _is_arabic(code):
		return "arabic"
	if _is_hebrew(code):
		return "hebrew"
	if 0x4E00 <= code <= 0x9FFF:
		return "cjk"
	if 0x3040 <= code <= 0x309F:
		return "hiragana"
	if 0x30A0 <= code <= 0x30FF:
		return "katakana"
	if 0xAC00 <= code <= 0xD7AF:
		return "hangul"
	return None


def _is_arabic(code: int) -> bool:
	return (
		0x0600 <= code <= 0x06FF
		or 0x0750 <= code <= 0x077F
		or 0x0870 <= code <= 0x08FF
		or 0xFB50 <= code <= 0xFDFF
		or 0xFE70 <= code <= 0xFEFF
	)


def _is_hebrew(code: int) -> bool:
	return 0x0590 <= code <= 0x05FF or 0xFB1D <= code <= 0xFB4F


def _script_counts(text: str) -> dict[str, int]:
	counts = {
		"latin": 0,
		"cyrillic": 0,
		"greek": 0,
		"arabic": 0,
		"hebrew": 0,
		"cjk": 0,
		"hiragana": 0,
		"katakana": 0,
		"hangul": 0,
	}
	for char in text:
		bucket = _script_bucket(ord(char))
		if bucket:
			counts[bucket] += 1
	return counts
