# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Pure, database-free machinery for Arabic / English / Hebrew translation.

Everything in this module is deterministic text engineering: language
resolution, structure-preserving segmentation, placeholder protection,
Arabic/Hebrew normalisation, prompt construction, batch parsing, glossary
enforcement and translation quality assessment.

It deliberately imports neither Frappe nor the AI engine so the whole
translation pipeline can be unit-tested without a database or a model runtime.
:mod:`ai_fr_hg.ai.translation` wires these helpers to the platform (models,
DocTypes, governance, audit, background jobs).

Design notes
------------
* **Nothing is guessed.** Numbers, URLs, identifiers, code, page markers and
  do-not-translate terminology are masked out of the text before the model
  sees it, and restored afterwards. A model can therefore never renumber a
  clause, convert a figure to Arabic-Indic digits or "translate" a URL.
* **Structure is a contract.** The source is split into blocks and the exact
  whitespace between them is carried through, so the translated document has
  the same headings, paragraphs, list items, table rows and page markers in
  the same order as the original.
* **Every segment is scored.** Placeholder integrity, script purity, residual
  source text, length ratio, glossary compliance, refusal/meta-commentary and
  degenerate repetition are checked locally, without a second model call.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Languages
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Language:
	"""One supported translation language."""

	code: str
	name: str
	endonym: str
	direction: str
	script: str


#: The three first-class translation languages of this platform.
LANGUAGES: dict[str, Language] = {
	"ar": Language(code="ar", name="Arabic", endonym="العربية", direction="rtl", script="arabic"),
	"en": Language(code="en", name="English", endonym="English", direction="ltr", script="latin"),
	"he": Language(code="he", name="Hebrew", endonym="עברית", direction="rtl", script="hebrew"),
}

SUPPORTED_LANGUAGES: tuple[str, ...] = ("ar", "en", "he")

#: Spellings accepted from users, stored records, pipeline configuration and
#: model output. Legacy ISO codes (`iw` for Hebrew) and locale tags are mapped.
_LANGUAGE_ALIASES: dict[str, str] = {
	"ar": "ar",
	"ara": "ar",
	"arb": "ar",
	"arabic": "ar",
	"عربي": "ar",
	"العربية": "ar",
	"عربية": "ar",
	"en": "en",
	"eng": "en",
	"english": "en",
	"he": "he",
	"heb": "he",
	"hebrew": "he",
	"iw": "he",
	"עברית": "he",
}

_AUTO_VALUES = {"", "auto", "auto detect", "autodetect", "detect", "source", "any"}


def normalise_language(value: str | None) -> str:
	"""Resolve any spelling of a supported language to its ISO 639-1 code.

	Returns ``""`` for "auto detect" or anything unsupported, so callers can
	distinguish "detect it for me" from a hard, validated choice.
	"""
	raw = (value or "").strip().lower().replace("_", "-")
	if raw in _AUTO_VALUES:
		return ""
	if raw in _LANGUAGE_ALIASES:
		return _LANGUAGE_ALIASES[raw]
	# Locale tags such as `ar-SA`, `he-IL`, `en-GB`.
	base = raw.split("-", 1)[0]
	return _LANGUAGE_ALIASES.get(base, "")


def is_supported(code: str | None) -> bool:
	return normalise_language(code) in LANGUAGES


def language_label(code: str | None) -> str:
	"""`ar` → `Arabic`. Unknown values are returned unchanged."""
	resolved = normalise_language(code)
	return LANGUAGES[resolved].name if resolved else (code or "")


def language_endonym(code: str | None) -> str:
	"""`ar` → `العربية`, the name of the language in the language itself."""
	resolved = normalise_language(code)
	return LANGUAGES[resolved].endonym if resolved else (code or "")


def text_direction(code: str | None) -> str:
	"""`rtl` for Arabic and Hebrew, `ltr` otherwise."""
	resolved = normalise_language(code)
	return LANGUAGES[resolved].direction if resolved else "ltr"


def language_pair(source: str | None, target: str | None) -> str:
	return f"{normalise_language(source) or '??'}->{normalise_language(target) or '??'}"


# ---------------------------------------------------------------------------
# Script handling and normalisation
# ---------------------------------------------------------------------------

#: Bidirectional control characters. PDF and DOCX extractors sprinkle these
#: through RTL text; they carry no meaning for a model and corrupt diffing,
#: hashing and length ratios.
BIDI_CONTROLS = "\u200e\u200f\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069\u061c"

TATWEEL = "\u0640"
ARABIC_DIACRITICS = re.compile(r"[\u064B-\u065F\u0670\u06D6-\u06ED]")
HEBREW_POINTS = re.compile(r"[\u0591-\u05BD\u05BF\u05C1\u05C2\u05C4\u05C5\u05C7]")

_ARABIC_INDIC = {ord("\u0660") + i: str(i) for i in range(10)}
_EXTENDED_ARABIC_INDIC = {ord("\u06f0") + i: str(i) for i in range(10)}
_DIGIT_TRANSLATION = {**_ARABIC_INDIC, **_EXTENDED_ARABIC_INDIC}


def _is_arabic_char(code: int) -> bool:
	return (
		0x0600 <= code <= 0x06FF
		or 0x0750 <= code <= 0x077F
		or 0x0870 <= code <= 0x08FF
		or 0xFB50 <= code <= 0xFDFF
		or 0xFE70 <= code <= 0xFEFF
	)


def _is_hebrew_char(code: int) -> bool:
	return 0x0590 <= code <= 0x05FF or 0xFB1D <= code <= 0xFB4F


def _is_latin_char(code: int) -> bool:
	return 0x0041 <= code <= 0x024F


def script_counts(text: str) -> dict[str, int]:
	"""Count letters per script, ignoring digits, punctuation and spacing."""
	counts = {"arabic": 0, "hebrew": 0, "latin": 0, "other": 0}
	for char in text or "":
		if not char.isalpha():
			continue
		code = ord(char)
		if _is_arabic_char(code):
			counts["arabic"] += 1
		elif _is_hebrew_char(code):
			counts["hebrew"] += 1
		elif _is_latin_char(code):
			counts["latin"] += 1
		else:
			counts["other"] += 1
	return counts


def script_ratio(text: str, code: str | None) -> float:
	"""Share of the text's letters that belong to `code`'s script (0.0 - 1.0)."""
	resolved = normalise_language(code)
	if not resolved:
		return 0.0
	counts = script_counts(text)
	total = sum(counts.values())
	if not total:
		return 0.0
	return counts[LANGUAGES[resolved].script] / total


def strip_bidi_controls(text: str) -> str:
	return (text or "").translate({ord(char): None for char in BIDI_CONTROLS})


def fold_presentation_forms(text: str) -> str:
	"""Fold Arabic/Hebrew presentation forms back to their base letters.

	PDF extraction commonly yields the FB50-FDFF / FE70-FEFF Arabic
	presentation blocks and the FB1D-FB4F Hebrew block. Those code points are
	shaping artefacts: they defeat dictionary lookups, glossary matching and
	most local models. NFKC is applied per character so the rest of the text
	(and any protected placeholder) is left byte-for-byte intact.
	"""
	if not text:
		return ""
	out: list[str] = []
	for char in text:
		code = ord(char)
		if 0xFB1D <= code <= 0xFDFF or 0xFE70 <= code <= 0xFEFF:
			out.append(unicodedata.normalize("NFKC", char))
		else:
			out.append(char)
	return "".join(out)


def normalise_digits(text: str) -> str:
	"""Map Arabic-Indic and extended Arabic-Indic digits to ASCII digits."""
	return (text or "").translate(_DIGIT_TRANSLATION)


def normalise_source_text(text: str | None) -> str:
	"""Clean extracted text before translation without changing its meaning.

	Folds presentation forms, removes bidi controls and tatweel, normalises
	non-breaking spaces and trims trailing spaces per line. Digits, casing,
	diacritics and structure are preserved.
	"""
	if not text:
		return ""
	cleaned = fold_presentation_forms(text)
	cleaned = strip_bidi_controls(cleaned)
	cleaned = cleaned.replace(TATWEEL, "")
	cleaned = cleaned.replace("\u00a0", " ").replace("\u202f", " ").replace("\ufeff", "")
	cleaned = cleaned.replace("\r\n", "\n").replace("\r", "\n")
	cleaned = "\n".join(line.rstrip() for line in cleaned.split("\n"))
	return cleaned


def comparison_key(text: str) -> str:
	"""Aggressively normalised form used for translation-memory identity."""
	folded = normalise_source_text(text).lower()
	folded = ARABIC_DIACRITICS.sub("", folded)
	folded = HEBREW_POINTS.sub("", folded)
	folded = normalise_digits(folded)
	return re.sub(r"\s+", " ", folded).strip()


def segment_fingerprint(text: str, source_language: str, target_language: str) -> str:
	"""Stable identity for a source segment within one language pair.

	Used by the translation memory so an identical clause in a later document
	is translated once and reused everywhere - which is also what keeps
	terminology consistent across a corpus.
	"""
	payload = (
		f"{normalise_language(source_language)}|{normalise_language(target_language)}|{comparison_key(text)}"
	)
	return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:40]


def memory_policy_identity(
	*,
	knowledge_base: str | None = None,
	glossary: str | None = None,
	tone: str | None = None,
	domain: str | None = None,
) -> str:
	"""Canonical policy key that must match before memory may be reused."""
	return "|".join(
		(
			(knowledge_base or "").strip(),
			(glossary or "").strip(),
			(tone or "Neutral").strip().casefold() or "neutral",
			comparison_key(domain or ""),
		)
	)


def memory_fingerprint(
	text: str,
	source_language: str,
	target_language: str,
	*,
	knowledge_base: str | None = None,
	glossary: str | None = None,
	tone: str | None = None,
	domain: str | None = None,
) -> str:
	"""Memory identity: language pair plus authorized KB and translation policy."""
	payload = (
		f"{segment_fingerprint(text, source_language, target_language)}|"
		f"{memory_policy_identity(knowledge_base=knowledge_base, glossary=glossary, tone=tone, domain=domain)}"
	)
	return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:40]


# ---------------------------------------------------------------------------
# Placeholder protection
# ---------------------------------------------------------------------------

#: Distinctive, ASCII-only sentinel. Local models reproduce it reliably and it
#: cannot occur in real prose.
_TOKEN_TEMPLATE = "[[T{index}]]"

#: Tolerant recogniser: accepts stray spaces the model may insert and
#: Arabic-Indic digits it may "helpfully" localise the index into.
# The class of "T"s accepts the Cyrillic and Arabic look-alikes a model may emit.
_TOKEN_PATTERN = re.compile(r"\[\s*\[\s*[TtطТ]\s*([0-9\u0660-\u0669\u06F0-\u06F9]+)\s*\]\s*\]")  # noqa: RUF001

PAGE_MARKER = re.compile(r"\[Page\s+(\d+)\]", re.IGNORECASE)

_PROTECTED_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
	("code_block", re.compile(r"```.*?```", re.DOTALL)),
	("inline_code", re.compile(r"`[^`\n]+`")),
	("page_marker", PAGE_MARKER),
	("url", re.compile(r"\b(?:https?://|ftp://|www\.)[^\s<>\"'\]\)]+", re.IGNORECASE)),
	("email", re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")),
	("path", re.compile(r"(?:^|(?<=\s))(?:/[\w.-]+){2,}/?")),
	("template", re.compile(r"\{\{[^{}]{0,200}\}\}|\{[A-Za-z_][\w.]*\}|%\([A-Za-z_]\w*\)[sdif]|%[sdif]\b")),
	("tag", re.compile(r"</?[A-Za-z][\w:-]*(?:\s[^<>]{0,200})?/?>")),
	("reference", re.compile(r"\b[A-Z]{2,}[-_/][A-Za-z0-9][A-Za-z0-9-_/]{2,}\b")),
	# Numbers last: earlier patterns already claimed the numbers inside them.
	(
		"number",
		re.compile(
			r"[+-]?[0-9\u0660-\u0669\u06F0-\u06F9]+(?:[.,:/\u066B\u066C-][0-9\u0660-\u0669\u06F0-\u06F9]+)*%?"
		),
	),
)


@dataclass
class ProtectedText:
	"""Masked text plus the mapping needed to restore it."""

	text: str
	tokens: dict[str, str] = field(default_factory=dict)

	@property
	def count(self) -> int:
		return len(self.tokens)


def protect_placeholders(text: str, do_not_translate: list[str] | None = None) -> ProtectedText:
	"""Replace untranslatable spans with sentinels the model must copy verbatim.

	`do_not_translate` terms (brand names, product codes, party names from a
	glossary) are masked first so they survive even when they look like
	ordinary words. Masking is applied to the still-unmasked spans only, so a
	later pattern can never match the digits inside an existing sentinel.
	"""
	if not text:
		return ProtectedText(text="")

	tokens: dict[str, str] = {}
	# (fragment, frozen): frozen fragments are already-issued sentinels.
	parts: list[tuple[str, bool]] = [(text, False)]

	def _mask_all(pattern: re.Pattern[str]) -> None:
		nonlocal parts
		rebuilt: list[tuple[str, bool]] = []
		for fragment, frozen in parts:
			if frozen or not fragment:
				rebuilt.append((fragment, frozen))
				continue
			position = 0
			for match in pattern.finditer(fragment):
				if match.start() > position:
					rebuilt.append((fragment[position : match.start()], False))
				token = _TOKEN_TEMPLATE.format(index=len(tokens))
				tokens[token] = match.group(0)
				rebuilt.append((token, True))
				position = match.end()
			if position < len(fragment):
				rebuilt.append((fragment[position:], False))
		parts = rebuilt

	for term in sorted(
		{t.strip() for t in (do_not_translate or []) if t and t.strip()}, key=len, reverse=True
	):
		_mask_all(re.compile(rf"(?<!\w){re.escape(term)}(?!\w)", re.IGNORECASE))

	for _kind, pattern in _PROTECTED_PATTERNS:
		_mask_all(pattern)

	return ProtectedText(text="".join(fragment for fragment, _ in parts), tokens=tokens)


def restore_placeholders(text: str, tokens: dict[str, str]) -> tuple[str, list[str]]:
	"""Put the original spans back. Returns `(text, missing_tokens)`.

	A sentinel the model dropped is only counted as missing when its original
	value is absent from the output too: a model that wrote the number itself
	instead of copying the sentinel lost nothing.
	"""
	if not tokens:
		return text or "", []

	produced = text or ""

	def _replace(match: re.Match[str]) -> str:
		index = normalise_digits(match.group(1))
		token = _TOKEN_TEMPLATE.format(index=index)
		return tokens.get(token, match.group(0))

	restored = _TOKEN_PATTERN.sub(_replace, produced)
	missing = [token for token, value in tokens.items() if value not in restored]
	return restored, missing


def has_unresolved_tokens(text: str) -> bool:
	return bool(_TOKEN_PATTERN.search(text or ""))


# ---------------------------------------------------------------------------
# Structure-preserving segmentation
# ---------------------------------------------------------------------------

HEADING_LINE = re.compile(r"^\s{0,3}#{1,6}\s+\S")
LIST_LINE = re.compile(r"^\s{0,6}(?:[-*•·]|\d+[.)]|[a-zA-Z][.)])\s+\S")
TABLE_LINE = re.compile(r"^\s*\|.*\|\s*$")
PAGE_MARKER_LINE = re.compile(r"^\s*\[Page\s+\d+\]\s*$", re.IGNORECASE)
RULE_LINE = re.compile(r"^\s*(?:[-=_*]{3,}|\|[\s|:-]+\|)\s*$")

#: Sentence terminators for English, Arabic and Hebrew, including the Arabic
#: question mark and full stop.
SENTENCE_BREAK = re.compile(r"(?<=[.!?؟۔:;])\s+")  # noqa: RUF001

DEFAULT_SEGMENT_CHARACTERS = 1800
MIN_SEGMENT_CHARACTERS = 200
MAX_SEGMENT_CHARACTERS = 6000


@dataclass
class Segment:
	"""One translatable (or deliberately untranslatable) block of a document."""

	index: int
	source: str
	separator: str = "\n\n"
	kind: str = "paragraph"
	heading: str | None = None
	page_number: int = 0
	translatable: bool = True
	translated: str = ""
	status: str = "Pending"
	quality_score: float = 0.0
	#: Machine-readable issue codes, for aggregation and metrics.
	issue_codes: list[str] = field(default_factory=list)
	#: The same issues as sentences, for the reviewer.
	issues: list[str] = field(default_factory=list)
	reused: bool = False

	@property
	def output(self) -> str:
		"""Translated text when available, otherwise the untouched source."""
		return self.translated or self.source

	def as_dict(self) -> dict:
		return {
			"segment_index": self.index,
			"kind": self.kind,
			"heading": self.heading,
			"page_number": self.page_number,
			"separator": encode_separator(self.separator),
			"source_text": self.source,
			"translated_text": self.output,
			"status": self.status,
			"quality_score": round(self.quality_score, 2),
			"issues": "\n".join(self.issues),
			"source_characters": len(self.source),
			"translated_characters": len(self.output),
			"reused": 1 if self.reused else 0,
		}


def _classify_block(block: str) -> str:
	stripped = block.strip()
	if not stripped:
		return "blank"
	if PAGE_MARKER_LINE.match(stripped):
		return "marker"
	if HEADING_LINE.match(stripped) and "\n" not in stripped:
		return "heading"
	if all(TABLE_LINE.match(line) for line in stripped.splitlines() if line.strip()):
		return "table"
	if all(LIST_LINE.match(line) for line in stripped.splitlines() if line.strip()):
		return "list"
	if stripped.startswith("```"):
		return "code"
	if RULE_LINE.match(stripped):
		return "rule"
	return "paragraph"


def _is_translatable(block: str, kind: str) -> bool:
	if kind in {"marker", "rule", "code", "blank"}:
		return False
	counts = script_counts(block)
	return sum(counts.values()) > 0


def _split_keeping_separators(text: str, pattern: re.Pattern[str]) -> list[tuple[str, str]]:
	"""Split `text` into `(piece, separator)` pairs that rejoin exactly."""
	pieces: list[tuple[str, str]] = []
	position = 0
	for match in pattern.finditer(text):
		piece = text[position : match.start()]
		if piece:
			pieces.append((piece, match.group(0)))
		position = match.end()
	remainder = text[position:]
	if remainder:
		pieces.append((remainder, ""))
	return pieces or [(text, "")]


def _pack_pieces(pieces: list[tuple[str, str]], max_characters: int) -> list[tuple[str, str]]:
	"""Greedily merge adjacent pieces while staying under the budget."""
	packed: list[tuple[str, str]] = []
	buffer = ""
	for piece, separator in pieces:
		candidate = buffer + piece + separator
		if buffer and len(candidate) > max_characters:
			packed.append((buffer.rstrip("\n "), _trailing_whitespace(buffer)))
			buffer = piece + separator
			continue
		buffer = candidate
	if buffer:
		packed.append((buffer.rstrip("\n "), _trailing_whitespace(buffer)))
	return packed


def _trailing_whitespace(text: str) -> str:
	stripped = text.rstrip("\n ")
	return text[len(stripped) :]


def _hard_split(text: str, max_characters: int) -> list[tuple[str, str]]:
	"""Last resort for a block with no sentence boundaries at all."""
	chunks: list[tuple[str, str]] = []
	for start in range(0, len(text), max_characters):
		chunk = text[start : start + max_characters]
		chunks.append((chunk, ""))
	return chunks or [(text, "")]


def segment_text(text: str | None, max_characters: int = DEFAULT_SEGMENT_CHARACTERS) -> list[Segment]:
	"""Split a document into translation segments that reassemble exactly.

	Blocks are separated by blank lines; headings, page markers, tables, lists
	and horizontal rules are recognised so the translated document keeps the
	original's shape. Over-long blocks are divided on sentence boundaries and
	the exact separating whitespace is preserved on every segment.
	"""
	source = normalise_source_text(text)
	if not source.strip():
		return []

	budget = max(
		MIN_SEGMENT_CHARACTERS, min(int(max_characters or DEFAULT_SEGMENT_CHARACTERS), MAX_SEGMENT_CHARACTERS)
	)

	blocks = _split_keeping_separators(source, re.compile(r"\n[ \t]*\n[\s]*"))
	segments: list[Segment] = []
	current_heading: str | None = None
	current_page = 0
	index = 0

	for block, separator in blocks:
		if not block.strip():
			continue

		kind = _classify_block(block)
		if kind == "heading":
			current_heading = block.strip().lstrip("#").strip()
		if kind == "marker":
			if match := PAGE_MARKER.search(block):
				current_page = int(match.group(1))
		elif match := PAGE_MARKER.search(block):
			current_page = int(match.group(1))

		if len(block) <= budget:
			pieces = [(block, separator)]
		else:
			inner = _split_keeping_separators(block, SENTENCE_BREAK)
			pieces = _pack_pieces(inner, budget)
			if any(len(piece) > budget for piece, _ in pieces):
				expanded: list[tuple[str, str]] = []
				for piece, piece_separator in pieces:
					if len(piece) <= budget:
						expanded.append((piece, piece_separator))
						continue
					parts = _hard_split(piece, budget)
					for position, (part, _) in enumerate(parts):
						is_last = position == len(parts) - 1
						expanded.append((part, piece_separator if is_last else ""))
				pieces = expanded
			if pieces:
				# The block separator belongs to the final piece only.
				pieces[-1] = (pieces[-1][0], separator)

		for piece, piece_separator in pieces:
			if not piece:
				continue
			segments.append(
				Segment(
					index=index,
					source=piece,
					separator=piece_separator or "",
					kind=kind,
					heading=current_heading if kind != "heading" else None,
					page_number=current_page,
					translatable=_is_translatable(piece, kind),
				)
			)
			index += 1

	return segments


def encode_separator(separator: str) -> str:
	"""Escape a whitespace separator so it survives a `Data` round trip.

	The separator carries the document's layout. Stored raw it would be at the
	mercy of any layer that trims a stored string; stored escaped it is exact.
	"""
	return (
		(separator or "").replace("\\", "\\\\").replace("\n", "\\n").replace("\t", "\\t").replace(" ", "\\s")
	)


def decode_separator(value: str | None) -> str:
	"""Inverse of :func:`encode_separator`."""
	out: list[str] = []
	iterator = iter(range(len(value or "")))
	text = value or ""
	for index in iterator:
		char = text[index]
		if char != "\\" or index + 1 >= len(text):
			out.append(char)
			continue
		following = text[index + 1]
		out.append({"n": "\n", "t": "\t", "s": " ", "\\": "\\"}.get(following, following))
		next(iterator, None)
	return "".join(out)


def reassemble(segments: list[Segment]) -> str:
	"""Rebuild a document from its segments, preserving original spacing."""
	return "".join(f"{segment.output}{segment.separator}" for segment in segments).rstrip() + "\n"


# ---------------------------------------------------------------------------
# Glossary
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GlossaryEntry:
	"""One terminology rule, resolved for a single language pair."""

	source_term: str
	target_term: str
	do_not_translate: bool = False
	case_sensitive: bool = False
	notes: str = ""


def resolve_glossary(rows: list[dict], source_language: str, target_language: str) -> list[GlossaryEntry]:
	"""Turn stored trilingual glossary rows into directional entries.

	Each row carries the term in English, Arabic and Hebrew; the pair being
	translated decides which column is the source and which is the target.
	Rows flagged `do_not_translate` apply in every direction.
	"""
	source = normalise_language(source_language)
	target = normalise_language(target_language)
	entries: list[GlossaryEntry] = []
	for row in rows or []:
		fields = {code: (row.get(f"term_{code}") or "").strip() for code in SUPPORTED_LANGUAGES}
		keep = bool(row.get("do_not_translate"))
		source_term = fields.get(source, "")
		target_term = fields.get(target, "")
		if keep:
			for term in {value for value in fields.values() if value}:
				entries.append(
					GlossaryEntry(
						source_term=term,
						target_term=term,
						do_not_translate=True,
						case_sensitive=bool(row.get("case_sensitive")),
						notes=(row.get("notes") or "").strip(),
					)
				)
			continue
		if source_term and target_term:
			entries.append(
				GlossaryEntry(
					source_term=source_term,
					target_term=target_term,
					case_sensitive=bool(row.get("case_sensitive")),
					notes=(row.get("notes") or "").strip(),
				)
			)
	return entries


def applicable_glossary(entries: list[GlossaryEntry], text: str) -> list[GlossaryEntry]:
	"""Only the entries whose source term actually occurs in this segment.

	Sending an entire corporate termbase with every segment wastes context and
	measurably degrades small local models; sending the three terms that are
	present does not.
	"""
	if not entries or not text:
		return []
	haystack = text if any(entry.case_sensitive for entry in entries) else text.lower()
	present: list[GlossaryEntry] = []
	for entry in entries:
		needle = entry.source_term if entry.case_sensitive else entry.source_term.lower()
		if needle and needle in haystack:
			present.append(entry)
	return present


def glossary_violations(entries: list[GlossaryEntry], source: str, translated: str) -> list[str]:
	"""Terms that were required in the output but are missing from it."""
	violations: list[str] = []
	for entry in entries:
		if entry.do_not_translate:
			continue
		flags = 0 if entry.case_sensitive else re.IGNORECASE
		if not re.search(rf"(?<!\w){re.escape(entry.source_term)}(?!\w)", source, flags):
			continue
		if not re.search(re.escape(entry.target_term), translated, flags):
			violations.append(f"{entry.source_term} → {entry.target_term}")
	return violations


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

SEGMENT_OPEN = "<<<SEG {index}>>>"
SEGMENT_PATTERN = re.compile(r"<<<\s*SEG\s*([0-9]+)\s*>>>")

TONE_GUIDANCE = {
	"Neutral": "Use clear, neutral register.",
	"Formal": "Use formal, professional register suitable for official correspondence.",
	"Informal": "Use natural, conversational register.",
	"Technical": "Use precise technical register; keep technical terms exact and consistent.",
	"Legal": (
		"Use formal legal register. Translate obligations, conditions and defined terms literally; "
		"never soften, summarise or reinterpret a clause."
	),
}

#: Pair-specific guidance. Small local models benefit disproportionately from
#: being told the concrete traps of a given direction.
PAIR_GUIDANCE: dict[str, str] = {
	"en->ar": (
		"Produce Modern Standard Arabic (فصحى). Use correct Arabic punctuation (، ؛ ؟). "
		"Do not transliterate words that have an established Arabic equivalent."
	),
	"ar->en": (
		"Produce natural English. Resolve Arabic sentence chaining (و) into properly punctuated English sentences "
		"instead of one long run-on."
	),
	"en->he": (
		"Produce modern standard Hebrew without niqqud. Use standard Hebrew punctuation and spacing, "
		"and prefer established Hebrew terminology over transliteration."
	),
	"he->en": (
		"Produce natural English. Expand Hebrew acronyms (ראשי תיבות) into their full English meaning "
		"when the meaning is unambiguous."
	),
	"ar->he": (
		"Translate directly from Arabic into modern Hebrew without pivoting through English. "
		"Keep proper nouns in their standard Hebrew spelling."
	),
	"he->ar": (
		"Translate directly from Hebrew into Modern Standard Arabic without pivoting through English. "
		"Keep proper nouns in their standard Arabic spelling."
	),
}


def build_system_prompt(
	source_language: str,
	target_language: str,
	*,
	tone: str = "Neutral",
	domain: str = "",
	preserve_formatting: bool = True,
	glossary: list[GlossaryEntry] | None = None,
	extra_instructions: str = "",
) -> str:
	"""The translator persona and the hard rules the output must satisfy."""
	source = normalise_language(source_language)
	target = normalise_language(target_language)
	source_name = language_label(source) or "the source language"
	target_name = language_label(target)
	target_native = language_endonym(target)

	rules = [
		f"You are a professional {source_name}-to-{target_name} translator working offline.",
		f"Translate the user's text into {target_name} ({target_native}).",
		"Return ONLY the translation. No preface, no explanation, no notes, no quotes around the output.",
		"Translate everything: never leave a sentence in the source language and never summarise or omit content.",
		"Never add content, opinions or clarifications that are not in the source.",
		(
			"Text in double square brackets such as [[T0]] is a protected placeholder. "
			"Copy every placeholder through unchanged, in the position the meaning requires. "
			"Never translate, renumber, reorder the digits of, or delete a placeholder."
		),
	]
	if preserve_formatting:
		rules.append(
			"Preserve the layout exactly: keep Markdown headings, bullet and numbered list markers, "
			"table pipes, indentation and line breaks in the same structure as the input."
		)
	if pair := PAIR_GUIDANCE.get(f"{source}->{target}"):
		rules.append(pair)
	if text_direction(target) == "rtl":
		rules.append(
			"The target script is right-to-left. Write natural right-to-left text and do not insert "
			"directional control characters."
		)
	rules.append(TONE_GUIDANCE.get(tone, TONE_GUIDANCE["Neutral"]))
	if domain:
		rules.append(f"Subject domain: {domain}. Use the standard terminology of that domain.")

	if glossary:
		terms = []
		for entry in glossary[:60]:
			if entry.do_not_translate:
				terms.append(f'- keep "{entry.source_term}" exactly as written')
			else:
				terms.append(f'- "{entry.source_term}" must be translated as "{entry.target_term}"')
		rules.append("Mandatory terminology:\n" + "\n".join(terms))

	if extra_instructions:
		rules.append(extra_instructions.strip())

	return "\n".join(rules)


def build_single_prompt(text: str) -> str:
	"""User message for a one-segment translation."""
	return f"Translate the following text.\n\n<TEXT>\n{text}\n</TEXT>"


def build_batch_prompt(segments: list[Segment]) -> str:
	"""User message carrying several segments in one call.

	Batching small blocks cuts round trips dramatically on local hardware and
	gives the model neighbouring context, which improves pronoun and
	terminology consistency. The markers make the response machine-splittable.
	"""
	parts = [
		"Translate every numbered section below.",
		"Reproduce each <<<SEG n>>> marker exactly once, on its own line, immediately before that "
		"section's translation, and keep the sections in the same order.",
		"Do not merge, split, renumber or omit sections.",
		"",
	]
	for segment in segments:
		parts.append(SEGMENT_OPEN.format(index=segment.index))
		parts.append(segment.source)
		parts.append("")
	return "\n".join(parts).rstrip() + "\n"


_PREAMBLE_PATTERNS = (
	re.compile(
		r"^\s*(?:here (?:is|'s)|the following is|sure[,!]?|certainly[,!]?)[^\n:]{0,80}:\s*", re.IGNORECASE
	),
	re.compile(r"^\s*(?:translation|translated text|الترجمة|תרגום)\s*[:：]\s*", re.IGNORECASE),  # noqa: RUF001
)

_FENCE = re.compile(r"^\s*```[a-zA-Z]*\s*\n(.*?)\n?\s*```\s*$", re.DOTALL)


def strip_model_preamble(text: str) -> str:
	"""Remove the chatty wrapper small models like to add around output."""
	cleaned = (text or "").strip()
	if match := _FENCE.match(cleaned):
		cleaned = match.group(1).strip()
	for pattern in _PREAMBLE_PATTERNS:
		cleaned = pattern.sub("", cleaned, count=1)
	if len(cleaned) > 1 and cleaned[0] == cleaned[-1] and cleaned[0] in "\"'«»“”":
		inner = cleaned[1:-1].strip()
		if inner and '"' not in inner:
			cleaned = inner
	return cleaned.strip()


def parse_batch_response(response: str, expected: list[int]) -> dict[int, str]:
	"""Split a batched response back into `{segment index: translation}`.

	Only indices that were actually requested are accepted, so a hallucinated
	marker can never inject text into an unrelated segment.
	"""
	if not response:
		return {}

	wanted = set(expected)
	matches = list(SEGMENT_PATTERN.finditer(response))
	if not matches:
		return {}

	found: dict[int, str] = {}
	for position, match in enumerate(matches):
		index = int(match.group(1))
		end = matches[position + 1].start() if position + 1 < len(matches) else len(response)
		body = response[match.end() : end].strip("\n")
		if index in wanted and index not in found:
			found[index] = strip_model_preamble(body)
	return found


# ---------------------------------------------------------------------------
# Quality assessment
# ---------------------------------------------------------------------------

#: Plausible output/input character-length ratios per direction. Arabic and
#: Hebrew are morphologically denser than English, so the bounds are asymmetric.
LENGTH_BOUNDS: dict[str, tuple[float, float]] = {
	"en->ar": (0.55, 1.80),
	"ar->en": (0.60, 2.10),
	"en->he": (0.45, 1.60),
	"he->en": (0.70, 2.40),
	"ar->he": (0.50, 1.80),
	"he->ar": (0.55, 2.00),
}
DEFAULT_LENGTH_BOUNDS = (0.45, 2.50)

_REFUSAL = re.compile(
	r"\b(?:as an ai|i (?:can(?:not|'t)|am unable|do not have)|i'm sorry|language model|"
	r"cannot (?:translate|comply)|no puedo)\b",
	re.IGNORECASE,
)
_META = re.compile(
	r"^\s*(?:note|disclaimer|explanation|ملاحظة|הערה)\s*[:：]",  # noqa: RUF001
	re.IGNORECASE | re.MULTILINE,
)

#: Issue code → penalty applied to a 100-point segment score.
ISSUE_PENALTIES: dict[str, float] = {
	"empty": 100.0,
	# A lost figure, date or identifier is the most damaging silent failure a
	# translation can have, so one is enough to send the segment to review.
	"placeholder_lost": 35.0,
	"placeholder_unresolved": 32.0,
	"untranslated": 45.0,
	"source_residue": 20.0,
	"wrong_script": 35.0,
	"length_short": 18.0,
	"length_long": 12.0,
	"glossary": 12.0,
	"refusal": 40.0,
	"meta_commentary": 10.0,
	"repetition": 25.0,
}

#: Below this a segment is flagged for review (and retried once).
REVIEW_THRESHOLD = 70.0


@dataclass
class QualityReport:
	"""Per-segment verdict produced without calling a model."""

	score: float
	issues: list[str] = field(default_factory=list)
	details: dict = field(default_factory=dict)

	@property
	def ok(self) -> bool:
		return self.score >= REVIEW_THRESHOLD

	def describe(self) -> list[str]:
		return [ISSUE_MESSAGES.get(issue, issue) for issue in self.issues]


ISSUE_MESSAGES = {
	"empty": "The model returned no text for this segment.",
	"placeholder_lost": "One or more protected values (numbers, URLs, codes) are missing from the translation.",
	"placeholder_unresolved": "A protected placeholder was left in the output.",
	"untranslated": "The output is identical to the source; the segment was not translated.",
	"source_residue": "A significant amount of source-language text remains in the translation.",
	"wrong_script": "The translation is not written in the target script.",
	"length_short": "The translation is much shorter than the source; content may be missing.",
	"length_long": "The translation is much longer than the source; content may have been added.",
	"glossary": "Required glossary terminology is missing from the translation.",
	"refusal": "The model refused or answered instead of translating.",
	"meta_commentary": "The output contains commentary or notes that are not part of the translation.",
	"repetition": "The output contains a degenerate repeated passage.",
}


def _detect_repetition(text: str, minimum_length: int = 25, repeats: int = 4) -> bool:
	"""Catch the classic small-model failure of looping one phrase forever."""
	stripped = re.sub(r"\s+", " ", text or "").strip()
	if len(stripped) < minimum_length * repeats:
		return False
	window = stripped[:minimum_length]
	if stripped.count(window) >= repeats:
		return True
	tail = stripped[-minimum_length:]
	return stripped.count(tail) >= repeats


def assess_translation(
	source: str,
	translated: str,
	source_language: str,
	target_language: str,
	*,
	missing_tokens: list[str] | None = None,
	glossary: list[GlossaryEntry] | None = None,
) -> QualityReport:
	"""Score one translated segment and list everything wrong with it."""
	issues: list[str] = []
	details: dict = {}

	source_text = source or ""
	output = translated or ""

	if not output.strip():
		return QualityReport(score=0.0, issues=["empty"], details={"length_ratio": 0.0})

	source_code = normalise_language(source_language)
	target_code = normalise_language(target_language)

	if missing_tokens:
		issues.append("placeholder_lost")
		details["missing_placeholders"] = list(missing_tokens)
	if has_unresolved_tokens(output):
		issues.append("placeholder_unresolved")

	if comparison_key(output) == comparison_key(source_text) and script_counts(source_text)["other"] == 0:
		issues.append("untranslated")

	ratio = len(output) / max(len(source_text), 1)
	details["length_ratio"] = round(ratio, 3)
	low, high = LENGTH_BOUNDS.get(f"{source_code}->{target_code}", DEFAULT_LENGTH_BOUNDS)
	if ratio < low:
		issues.append("length_short")
	elif ratio > high:
		issues.append("length_long")

	target_share = script_ratio(output, target_code)
	details["target_script_ratio"] = round(target_share, 3)
	letters = sum(script_counts(output).values())
	if letters >= 8 and target_share < 0.5:
		issues.append("wrong_script")

	if source_code and source_code != target_code:
		residue = script_ratio(output, source_code)
		details["source_script_ratio"] = round(residue, 3)
		if letters >= 12 and residue > 0.35 and "wrong_script" not in issues:
			issues.append("source_residue")

	if _REFUSAL.search(output):
		issues.append("refusal")
	if _META.search(output):
		issues.append("meta_commentary")
	if _detect_repetition(output):
		issues.append("repetition")

	if violations := glossary_violations(glossary or [], source_text, output):
		issues.append("glossary")
		details["glossary_violations"] = violations

	score = 100.0
	for issue in issues:
		score -= ISSUE_PENALTIES.get(issue, 10.0)
	score = max(0.0, min(100.0, score))

	return QualityReport(score=score, issues=issues, details=details)


def aggregate_score(segments: list[Segment]) -> float:
	"""Character-weighted quality across a document.

	Weighting by length stops a document of one thousand good paragraphs from
	being dragged down by a two-word heading, and stops a broken twenty-page
	section from hiding behind clean headings.
	"""
	translated = [segment for segment in segments if segment.translatable and segment.translated]
	if not translated:
		return 0.0
	weight = sum(max(len(segment.source), 1) for segment in translated)
	total = sum(segment.quality_score * max(len(segment.source), 1) for segment in translated)
	return round(total / weight, 2) if weight else 0.0


def summarise_issues(segments: list[Segment]) -> dict[str, int]:
	"""Count each distinct issue code across a document, most frequent first."""
	counts: dict[str, int] = {}
	for segment in segments:
		for issue in segment.issue_codes:
			counts[issue] = counts.get(issue, 0) + 1
	return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0])))


def plan_batches(
	segments: list[Segment],
	*,
	max_characters: int = 4000,
	max_segments: int = 8,
) -> list[list[Segment]]:
	"""Group pending segments into model calls that fit the context budget."""
	batches: list[list[Segment]] = []
	current: list[Segment] = []
	size = 0
	for segment in segments:
		length = len(segment.source)
		if current and (size + length > max_characters or len(current) >= max_segments):
			batches.append(current)
			current = []
			size = 0
		current.append(segment)
		size += length
	if current:
		batches.append(current)
	return batches
