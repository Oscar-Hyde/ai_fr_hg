# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Detect the written language of extracted document text.

No extra packages: script counts plus a few high-signal function words.
Bulgarian is first-class because the platform is used on a BG site.
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


def language_name(code: str | None) -> str:
	"""Human label for an ISO 639-1 code, or the code itself."""
	code = (code or "").strip().lower()
	return _LANGUAGE_NAMES.get(code, code)


def resolve_document_language(stored: str | None, text: str | None) -> str:
	"""Prefer a stored ISO code; otherwise detect from extracted text."""
	code = (stored or "").strip().lower()
	if code:
		return code
	return detect_language(text)


def detect_language(text: str | None) -> str:
	"""Return an ISO 639-1 code, or empty when there is too little signal."""
	sample = (text or "")[:6000]
	if len(sample.strip()) < 20:
		return ""

	scripts = _script_counts(sample)
	letters = sum(scripts.values()) or 1

	if scripts["hangul"] / letters > 0.2:
		return "ko"
	if (scripts["cjk"] + scripts["hiragana"] + scripts["katakana"]) / letters > 0.2:
		if scripts["hiragana"] or scripts["katakana"]:
			return "ja"
		return "zh"
	if scripts["arabic"] / letters > 0.2:
		return "ar"
	if scripts["hebrew"] / letters > 0.2:
		return "he"
	if scripts["greek"] / letters > 0.2:
		return "el"

	words = {token.lower() for token in _WORD.findall(sample)}
	if not words:
		return ""

	if scripts["cyrillic"] / letters > 0.15:
		return _best_word_match(words, ("bg", "ru", "uk")) or "bg"
	if scripts["latin"] / letters > 0.15:
		return _best_word_match(words, ("en", "de", "fr", "es", "it")) or "en"
	return ""


def _best_word_match(words: set[str], candidates: tuple[str, ...]) -> str:
	best = ""
	best_hits = 0
	for code in candidates:
		hits = len(words & _WORD_HINTS[code])
		if hits > best_hits:
			best = code
			best_hits = hits
	return best if best_hits >= 2 else ""


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
		code = ord(char)
		if 0x0041 <= code <= 0x024F:
			counts["latin"] += 1
		elif 0x0400 <= code <= 0x04FF:
			counts["cyrillic"] += 1
		elif 0x0370 <= code <= 0x03FF:
			counts["greek"] += 1
		elif 0x0600 <= code <= 0x06FF:
			counts["arabic"] += 1
		elif 0x0590 <= code <= 0x05FF:
			counts["hebrew"] += 1
		elif 0x4E00 <= code <= 0x9FFF:
			counts["cjk"] += 1
		elif 0x3040 <= code <= 0x309F:
			counts["hiragana"] += 1
		elif 0x30A0 <= code <= 0x30FF:
			counts["katakana"] += 1
		elif 0xAC00 <= code <= 0xD7AF:
			counts["hangul"] += 1
	return counts
