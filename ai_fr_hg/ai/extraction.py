# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Canonical file-intelligence extraction owner.

Frappe File stores bytes. This module detects format, resolves a reader,
normalizes the ReadResult, and builds durable extraction evidence. Ingestion
persists the outcome; it does not re-implement detection or archive policy.
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime

from ai_fr_hg.ai.readers.archive import (
	ARCHIVE_EXTENSIONS,
	ZIP_EXTENSIONS,
	ArchiveCorruptError,
	ArchiveLimitError,
	extension_of,
	validate_zip_container,
	zip_kind,
)
from ai_fr_hg.ai.readers.base import BaseReader, ReadResult
from ai_fr_hg.ai.readers.code import LANGUAGES as _CODE_LANGUAGES

MAGIC_PDF = b"%PDF"
MAGIC_ZIP = b"PK\x03\x04"
MAGIC_ZIP_EMPTY = b"PK\x05\x06"
MAGIC_PNG = b"\x89PNG"
MAGIC_JPEG = b"\xff\xd8\xff"
MAGIC_GIF = b"GIF8"
MAGIC_BMP = b"BM"
MAGIC_TIFF_LE = b"II*\x00"
MAGIC_TIFF_BE = b"MM\x00*"
MAGIC_UTF8_BOM = b"\xef\xbb\xbf"

IMAGE_FAMILIES = frozenset({"png", "jpeg", "gif", "bmp", "tiff", "webp"})
ZIP_FAMILIES = frozenset({"zip", "docx", "xlsx", "pptx", "odt", "ods", "odp", "odf", "openxml"})
UNRESOLVED_ZIP_MAGICS = frozenset({"zip", "odf", "openxml"})

EXTENSION_FAMILY = {
	"pdf": "pdf",
	"docx": "docx",
	"xlsx": "xlsx",
	"xlsm": "xlsx",
	"pptx": "pptx",
	"odt": "odt",
	"ods": "ods",
	"odp": "odp",
	"png": "png",
	"jpg": "jpeg",
	"jpeg": "jpeg",
	"gif": "gif",
	"bmp": "bmp",
	"tif": "tiff",
	"tiff": "tiff",
	"webp": "webp",
	"json": "json",
	"xml": "xml",
	"html": "html",
	"htm": "html",
	"eml": "email",
	"md": "markdown",
	"markdown": "markdown",
	"csv": "csv",
	"tsv": "csv",
	"txt": "text",
	"log": "text",
	"rst": "text",
	"yaml": "text",
	"yml": "text",
	"ini": "text",
	"cfg": "text",
	"toml": "text",
}

# Source-code extensions all resolve to the single `code` family so detection
# never guesses a binary format for a text source file.
EXTENSION_FAMILY.update({extension: "code" for extension in _CODE_LANGUAGES})


@dataclass(frozen=True)
class FormatIdentity:
	"""Resolved format after magic-byte detection and extension comparison."""

	extension: str
	magic: str | None
	family: str | None
	mismatch: bool
	reason: str


@dataclass
class ExtractionEvidence:
	"""Durable, bounded evidence for one extraction run.

	Satisfies the six-element extraction result contract:
	source identity (``provenance.checksum_sha256``), processing timestamp
	(``extracted_on``), extractor identity (``reader``), version information
	(``versions``), extracted content (persisted separately on the document),
	and evidence references (``structure`` / ``embedded_objects``).
	"""

	detector: dict
	reader: str
	embedded_objects: list[dict] = field(default_factory=list)
	structure: dict = field(default_factory=dict)
	provenance: dict = field(default_factory=dict)
	#: UTC ISO-8601 timestamp of when this extraction ran.
	extracted_on: str = ""
	#: Versions that produced this result: application, reader, and the
	#: parsing library actually used. Lets operators identify precisely which
	#: documents were produced by a superseded extractor after a reader fix.
	versions: dict = field(default_factory=dict)

	def as_dict(self) -> dict:
		return asdict(self)


@dataclass
class ExtractionOutcome:
	"""Detection + parse + evidence from one extraction run."""

	identity: FormatIdentity
	result: ReadResult
	evidence: ExtractionEvidence
	reader: BaseReader


def detect_format(content: bytes, filename: str = "") -> FormatIdentity:
	"""Detect format from magic bytes and compare with the filename extension."""
	extension = extension_of(filename)
	magic = _magic_family(content)
	ext_family = EXTENSION_FAMILY.get(extension)
	mismatch = bool(magic and ext_family and not _families_compatible(ext_family, magic))
	if mismatch:
		reason = "extension_magic_mismatch"
	elif magic and not extension:
		reason = "magic_only"
	elif extension and not magic:
		reason = "extension_only"
	else:
		reason = "aligned"
	family = ext_family or magic
	return FormatIdentity(
		extension=extension,
		magic=magic,
		family=family,
		mismatch=mismatch,
		reason=reason,
	)


def _magic_family(content: bytes) -> str | None:
	if not content:
		return None
	head = content[:16]
	if head.startswith(MAGIC_PDF):
		return "pdf"
	if head.startswith(MAGIC_PNG):
		return "png"
	if head.startswith(MAGIC_JPEG):
		return "jpeg"
	if head.startswith(MAGIC_GIF):
		return "gif"
	if head.startswith(MAGIC_BMP):
		return "bmp"
	if head.startswith(MAGIC_TIFF_LE) or head.startswith(MAGIC_TIFF_BE):
		return "tiff"
	if head.startswith(b"RIFF") and content[8:12] == b"WEBP":
		return "webp"
	if head.startswith(MAGIC_ZIP) or head.startswith(MAGIC_ZIP_EMPTY):
		return zip_kind(content) or "zip"
	stripped = content.lstrip()
	if stripped.startswith(MAGIC_UTF8_BOM):
		stripped = stripped[3:].lstrip()
	if stripped[:1] in (b"{", b"["):
		return "json"
	if stripped.lower().startswith(b"<?xml"):
		return "xml"
	if stripped.lower().startswith(b"<html") or stripped.lower().startswith(b"<!doctype html"):
		return "html"
	if _looks_like_email(stripped):
		return "email"
	return None


def _looks_like_email(stripped: bytes) -> bool:
	sample = stripped[:512].lower()
	return any(
		sample.startswith(prefix)
		for prefix in (b"from:", b"return-path:", b"received:", b"mime-version:", b"message-id:")
	)


def _families_compatible(extension_family: str, magic_family: str) -> bool:
	if extension_family == magic_family:
		return True
	if extension_family in ZIP_FAMILIES and magic_family in ZIP_FAMILIES:
		return True
	if extension_family in IMAGE_FAMILIES and magic_family in IMAGE_FAMILIES:
		return True
	if extension_family in {"html", "xml"} and magic_family in {"html", "xml"}:
		return True
	return False


def app_version() -> str:
	"""Installed application version, for extraction provenance."""
	try:
		from ai_fr_hg import __version__

		return str(__version__)
	except Exception:
		return "unknown"


def build_versions(reader: BaseReader | type[BaseReader] | None) -> dict:
	"""Collect the version triple that produced an extraction result.

	Never raises: provenance collection must not be able to fail an extraction.
	"""
	versions: dict = {"app": app_version()}
	if reader is None:
		return versions
	reader_class = reader if isinstance(reader, type) else type(reader)
	versions["reader"] = str(getattr(reader_class, "version", "1.0"))
	package = getattr(reader_class, "requires", None)
	if package:
		versions["library"] = package
		try:
			versions["library_version"] = reader_class.library_version()
		except Exception:
			versions["library_version"] = None
	return versions


def build_evidence(
	identity: FormatIdentity,
	result,
	reader_label: str,
	content: bytes,
	reader: BaseReader | type[BaseReader] | None = None,
) -> ExtractionEvidence:
	"""Summarize extraction for durable persistence (bounded, no full text)."""
	embedded = list(getattr(result, "embedded_objects", None) or [])[:50]
	blocks = list(getattr(result, "structure", None) or [])
	kinds: dict[str, int] = {}
	for block in blocks:
		kind = str(block.get("kind") or "block")
		kinds[kind] = kinds.get(kind, 0) + 1
	return ExtractionEvidence(
		detector={
			"extension": identity.extension,
			"magic": identity.magic,
			"family": identity.family,
			"mismatch": identity.mismatch,
			"reason": identity.reason,
		},
		reader=reader_label,
		embedded_objects=embedded,
		structure={"block_count": len(blocks), "kinds": kinds},
		provenance={
			"bytes": len(content or b""),
			"checksum_sha256": hashlib.sha256(content or b"").hexdigest() if content else None,
			"page_count": getattr(result, "page_count", 0),
			"word_count": getattr(result, "word_count", 0),
			"character_count": getattr(result, "character_count", 0),
		},
		extracted_on=datetime.now(UTC).isoformat(),
		versions=build_versions(reader),
	)


def _needs_zip_guard(identity: FormatIdentity) -> bool:
	"""True when the *Office container* policy applies to this input.

	User archives (.zip/.tar/...) are deliberately excluded: they are containers
	of independent files governed by `ArchiveBudget`'s cumulative member, size,
	and depth ceilings, not by the single-document Office limits. Applying the
	Office 500-member cap to a user archive would reject legitimate bundles,
	while `ArchiveReader` still enforces ratio, traversal, and encryption rules
	member by member through the same authority module.
	"""
	if identity.extension in ARCHIVE_EXTENSIONS:
		return False
	if identity.magic in ZIP_FAMILIES:
		return True
	if identity.extension in ZIP_EXTENSIONS and not identity.mismatch:
		return True
	return False


def _translate_archive_error(exc: Exception) -> None:
	if isinstance(exc, ArchiveLimitError):
		from ai_fr_hg.ai.exceptions import DocumentResourceLimitError

		raise DocumentResourceLimitError(str(exc)) from exc
	if isinstance(exc, ArchiveCorruptError):
		from ai_fr_hg.ai.exceptions import CorruptDocumentError

		raise CorruptDocumentError(str(exc)) from exc
	raise exc


def _resolve_reader(filename: str, identity: FormatIdentity, reader: BaseReader | None) -> BaseReader | None:
	if reader is not None:
		return reader
	from ai_fr_hg.ai.readers import get_reader

	by_name = get_reader(filename)
	magic_name = identity.magic if identity.magic and identity.magic not in UNRESOLVED_ZIP_MAGICS else None
	by_magic = get_reader(f"detected.{magic_name}") if magic_name else None
	if identity.mismatch and by_magic is not None:
		return by_magic
	return by_name or by_magic


def extract_bytes(content: bytes, filename: str, *, reader: BaseReader | None = None) -> ExtractionOutcome:
	"""Run detection + the canonical reader and return a durable outcome."""
	identity = detect_format(content, filename)
	if _needs_zip_guard(identity):
		try:
			validate_zip_container(content, filename, force=True)
		except (ArchiveLimitError, ArchiveCorruptError) as exc:
			_translate_archive_error(exc)
			raise

	resolved = _resolve_reader(filename, identity, reader)
	if resolved is None:
		from ai_fr_hg.ai.exceptions import UnsupportedDocumentError

		raise UnsupportedDocumentError(
			f"No document reader is registered for {filename or identity.magic or 'unknown'}."
		)

	try:
		result = resolved.read(content, filename)
	except (ArchiveLimitError, ArchiveCorruptError) as exc:
		_translate_archive_error(exc)
		raise
	if identity.mismatch:
		warnings = list(result.warnings or [])
		warnings.append(
			f"Filename extension .{identity.extension} does not match detected format {identity.magic}."
		)
		result.warnings = warnings
	evidence = build_evidence(identity, result, resolved.label, content, reader=resolved)
	metadata = dict(result.metadata or {})
	metadata["extraction_evidence"] = evidence.as_dict()
	result.metadata = metadata
	return ExtractionOutcome(identity=identity, result=result, evidence=evidence, reader=resolved)
