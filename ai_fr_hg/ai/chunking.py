# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Structure-aware text chunking.

Retrieval quality depends far more on chunk boundaries than on the embedding
model. This splitter respects document structure: it prefers to break on
Markdown headings, then paragraphs, then sentences, and only falls back to a
hard character cut when a single sentence exceeds the window.
"""

import hashlib
import re
from dataclasses import dataclass, field

HEADING = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)
PAGE_MARKER = re.compile(r"^\[Page (\d+)\]$", re.MULTILINE)
# The second alternative handles CJK full-width terminators.
SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z\"'(\[])|(?<=[。！？])\s*")  # noqa: RUF001

#: Rough characters-per-token ratio, adequate for budgeting without a tokenizer.
CHARS_PER_TOKEN = 4


@dataclass
class Chunk:
	"""A retrievable slice of a document."""

	content: str
	index: int = 0
	heading: str | None = None
	page_number: int = 0

	@property
	def character_count(self) -> int:
		return len(self.content)

	@property
	def token_count(self) -> int:
		return estimate_tokens(self.content)

	@property
	def checksum(self) -> str:
		return hashlib.sha256(self.content.encode("utf-8")).hexdigest()[:32]


def estimate_tokens(text: str) -> int:
	"""Approximate the token count of `text` without loading a tokenizer."""
	if not text:
		return 0
	return max(1, len(text) // CHARS_PER_TOKEN)


def split_sentences(text: str) -> list[str]:
	parts = [part.strip() for part in SENTENCE_END.split(text) if part and part.strip()]
	return parts or ([text.strip()] if text.strip() else [])


def _hard_split(text: str, size: int, overlap: int = 0) -> list[str]:
	"""Split text that has no usable sentence boundaries into overlapping windows.

	The overlap matters even here: a fact split across a boundary would
	otherwise be unretrievable from either side.
	"""
	if size <= 0:
		return [text]

	step = max(size - overlap, 1)
	windows = []
	position = 0
	while position < len(text):
		windows.append(text[position : position + size])
		if position + size >= len(text):
			break
		position += step
	return windows


def _pack(pieces: list[str], size: int, overlap: int, separator: str = " ") -> list[str]:
	"""Greedily pack pieces into windows of `size`, carrying `overlap` forward."""
	windows: list[str] = []
	current = ""

	for piece in pieces:
		if len(piece) > size:
			if current:
				windows.append(current)
				current = ""
			windows.extend(_hard_split(piece, size, overlap))
			continue

		candidate = f"{current}{separator}{piece}" if current else piece
		if len(candidate) <= size:
			current = candidate
			continue

		windows.append(current)
		if overlap and len(current) > overlap:
			tail = current[-overlap:]
			# Resume from a word boundary so the overlap stays readable.
			if " " in tail:
				tail = tail[tail.index(" ") + 1 :]
			current = f"{tail}{separator}{piece}"
		else:
			current = piece

	if current:
		windows.append(current)
	return windows


def chunk_text(
	text: str,
	chunk_size: int = 1200,
	chunk_overlap: int = 150,
	respect_headings: bool = True,
) -> list[Chunk]:
	"""Split `text` into overlapping, structure-aware chunks."""
	if not text or not text.strip():
		return []

	chunk_size = max(int(chunk_size or 1200), 100)
	chunk_overlap = max(min(int(chunk_overlap or 0), chunk_size // 2), 0)

	sections = _split_sections(text) if respect_headings else [(None, text)]
	chunks: list[Chunk] = []
	index = 0

	for heading, body in sections:
		body = body.strip()
		if not body:
			continue

		page_number = 0
		if match := PAGE_MARKER.search(body):
			page_number = int(match.group(1))

		if len(body) <= chunk_size:
			windows = [body]
		else:
			paragraphs = [p.strip() for p in body.split("\n\n") if p.strip()]
			oversized = [p for p in paragraphs if len(p) > chunk_size]
			if oversized:
				pieces: list[str] = []
				for paragraph in paragraphs:
					if len(paragraph) > chunk_size:
						pieces.extend(split_sentences(paragraph))
					else:
						pieces.append(paragraph)
				windows = _pack(pieces, chunk_size, chunk_overlap)
			else:
				windows = _pack(paragraphs, chunk_size, chunk_overlap, separator="\n\n")

		for window in windows:
			content = window.strip()
			if not content:
				continue
			# Prefix the heading so each chunk carries its own context.
			if heading and not content.startswith(heading):
				content = f"{heading}\n\n{content}"
			chunks.append(Chunk(content=content, index=index, heading=heading, page_number=page_number))
			index += 1

	return chunks


def _split_sections(text: str) -> list[tuple[str | None, str]]:
	"""Split Markdown-ish text into `(heading, body)` sections."""
	matches = list(HEADING.finditer(text))
	if not matches:
		return [(None, text)]

	sections: list[tuple[str | None, str]] = []
	if preamble := text[: matches[0].start()].strip():
		sections.append((None, preamble))

	for position, match in enumerate(matches):
		heading = match.group(2).strip()
		start = match.end()
		end = matches[position + 1].start() if position + 1 < len(matches) else len(text)
		sections.append((heading, text[start:end]))

	return sections
