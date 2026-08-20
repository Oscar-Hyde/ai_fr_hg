# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Reader contract shared by every document format handler."""

import re
from dataclasses import dataclass, field

WHITESPACE = re.compile(r"[ \t\f\v]+")
BLANK_LINES = re.compile(r"\n{3,}")


@dataclass
class ReadResult:
	"""Text and metadata extracted from a source document."""

	text: str = ""
	metadata: dict = field(default_factory=dict)
	page_count: int = 0
	pages: list[str] = field(default_factory=list)
	warnings: list[str] = field(default_factory=list)

	@property
	def character_count(self) -> int:
		return len(self.text)


import json as _json
from datetime import datetime, timezone


@dataclass
class StructuredWarning:
	"""Canonical warning contract for ING-05.
	category/type, severity, source_file, reader, location, message, details, timestamp, stage
	"""

	code: str  # e.g. truncated, encrypted, missing_dependency, archive_member, parse_partial
	category: str  # e.g. truncation, encryption, format, archive, nested_content
	severity: str  # info | warning | error
	reader: str
	source_file: str
	location: str | None  # page/sheet/slide/member/index
	message: str  # human-readable
	details: dict  # machine-readable
	timestamp: str = field(default_factory=lambda: datetime.now(datetime.UTC).isoformat())
	stage: str = "extraction"  # extraction | chunking | embedding

	def as_dict(self) -> dict:
		return {k: v for k, v in self.__dict__.items() if v is not None}


def coerce_warnings(
	raw: list, reader: str = "", source_file: str = "", stage: str = "extraction"
) -> list[dict]:
	"""Coerce legacy string warnings into structured objects (backward compat)."""
	out = []
	for w in raw or []:
		if isinstance(w, dict):
			out.append(w)
		else:
			out.append(
				StructuredWarning(
					code="legacy",
					category="general",
					severity="warning",
					reader=reader,
					source_file=source_file,
					location=None,
					message=str(w),
					details={},
					stage=stage,
				).as_dict()
			)
	return out

	@property
	def word_count(self) -> int:
		return len(self.text.split())


class MissingDependency(Exception):
	"""Raised when the library backing a reader is not installed."""


class BaseReader:
	"""Turn raw bytes into clean text.

	Subclasses implement :meth:`read`. Everything else is shared behaviour.
	"""

	#: Human readable name shown on the AI Document record.
	label: str = "Generic"
	#: Optional pip package required by this reader.
	requires: str | None = None

	def read(self, content: bytes, filename: str) -> ReadResult:
		raise NotImplementedError

	# -- helpers ---------------------------------------------------------

	@staticmethod
	def decode(content: bytes) -> str:
		"""Decode bytes to text, trying the most likely encodings in order."""
		for encoding in ("utf-8", "utf-8-sig", "utf-16", "latin-1"):
			try:
				return content.decode(encoding)
			except (UnicodeDecodeError, LookupError):
				continue
		return content.decode("utf-8", errors="replace")

	@staticmethod
	def clean(text: str) -> str:
		"""Collapse redundant whitespace while preserving paragraph breaks."""
		if not text:
			return ""
		text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")
		text = WHITESPACE.sub(" ", text)
		text = "\n".join(line.rstrip() for line in text.split("\n"))
		return BLANK_LINES.sub("\n\n", text).strip()

	def require(self, module: str, package: str | None = None):
		"""Import an optional dependency, or raise a clear, actionable error."""
		import importlib

		try:
			return importlib.import_module(module)
		except ImportError as exc:
			raise MissingDependency(
				f"{self.label} documents need the '{package or self.requires or module}' package. "
				f"Install it in your bench environment: ./env/bin/pip install {package or self.requires or module}"
			) from exc
