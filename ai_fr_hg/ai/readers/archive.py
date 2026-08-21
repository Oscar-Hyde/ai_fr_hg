# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Single ZIP-container guard for every Office/OpenDocument reader.

Frappe has no archive-bomb policy. This module is the only place ZIP member
count, uncompressed size, compression ratio, encryption, and path traversal
are enforced so ingestion and readers cannot drift.

This module does not import Frappe. Domain exceptions are translated by
`ai.extraction` so unit tests can exercise the guard without a bench.
"""

from __future__ import annotations

import zipfile
from io import BytesIO

MAX_ARCHIVE_MEMBERS = 500
MAX_UNCOMPRESSED = 50 * 1024 * 1024
MAX_RATIO = 100
ZIP_EXTENSIONS = frozenset({"docx", "xlsx", "xlsm", "pptx", "odt", "ods", "odp"})


class ArchiveError(Exception):
	"""Base class for ZIP-container policy failures."""


class ArchiveCorruptError(ArchiveError):
	"""The container is malformed, encrypted, or contains unsafe paths."""


class ArchiveLimitError(ArchiveError):
	"""The container exceeds member, size, or compression-ratio limits."""


def extension_of(filename: str) -> str:
	return filename.rsplit(".", 1)[-1].lower() if filename and "." in filename else ""


def validate_zip_container(content: bytes, filename: str, *, force: bool = False) -> None:
	"""Reject traversal, bombs, encryption, and corrupt ZIP office containers."""
	if not force and extension_of(filename) not in ZIP_EXTENSIONS:
		return
	try:
		archive = zipfile.ZipFile(BytesIO(content))
	except (zipfile.BadZipFile, zipfile.LargeZipFile) as exc:
		raise ArchiveCorruptError(f"{filename}: invalid or corrupted archive") from exc

	members = archive.infolist()
	if len(members) > MAX_ARCHIVE_MEMBERS:
		raise ArchiveLimitError(f"{filename}: archive has too many members ({len(members)})")
	total_uncompressed = 0
	total_compressed = 0
	for info in members:
		name = info.filename or ""
		if ".." in name or name.startswith("/") or name.startswith("\\"):
			raise ArchiveCorruptError(f"{filename}: archive contains unsafe path {name}")
		if info.flag_bits & 0x1:
			raise ArchiveCorruptError(f"{filename}: encrypted Office documents are not supported.")
		total_uncompressed += info.file_size
		total_compressed += info.compress_size
		if total_uncompressed > MAX_UNCOMPRESSED:
			raise ArchiveLimitError(f"{filename}: archive uncompressed size exceeds limit")
		if info.compress_size and info.file_size / max(info.compress_size, 1) > MAX_RATIO:
			raise ArchiveLimitError(f"{filename}: archive compression ratio exceeded for {name}")
	if total_compressed and total_uncompressed / max(total_compressed, 1) > MAX_RATIO:
		raise ArchiveLimitError(f"{filename}: archive total compression ratio exceeded")


def zip_kind(content: bytes) -> str | None:
	"""Best-effort Open XML / OpenDocument subtype from ZIP members."""
	try:
		archive = zipfile.ZipFile(BytesIO(content))
	except (zipfile.BadZipFile, zipfile.LargeZipFile, OSError):
		return None
	names = set(archive.namelist())
	mimetype = ""
	if "mimetype" in names:
		try:
			mimetype = archive.read("mimetype").decode("utf-8", errors="ignore")
		except Exception:
			mimetype = ""
	if "word/document.xml" in names or any(name.startswith("word/") for name in names):
		return "docx"
	if any(name.startswith("xl/") for name in names):
		return "xlsx"
	if any(name.startswith("ppt/") for name in names):
		return "pptx"
	if "opendocument.text" in mimetype:
		return "odt"
	if "opendocument.spreadsheet" in mimetype:
		return "ods"
	if "opendocument.presentation" in mimetype:
		return "odp"
	if "META-INF/manifest.xml" in names:
		return "odf"
	if "[Content_Types].xml" in names:
		return "openxml"
	return "zip"
