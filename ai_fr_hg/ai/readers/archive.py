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

#: Extensions handled as *user* archives (containers of independent files),
#: as opposed to the Office containers above which are single documents.
ARCHIVE_EXTENSIONS = frozenset({"zip", "tar", "gz", "tgz", "bz2", "tbz2", "xz", "txz"})

#: Recursion and budget policy for user archives. These bound the *whole* tree,
#: not each member, so a nested archive cannot multiply the cost.
MAX_ARCHIVE_DEPTH = 3
MAX_TOTAL_MEMBERS = 1000
MAX_TOTAL_EXTRACTED = 200 * 1024 * 1024
#: Refuse individual members larger than this before reading them into memory.
MAX_MEMBER_SIZE = 50 * 1024 * 1024


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


# ---------------------------------------------------------------------------
# User archives (.zip/.tar family): safe, bounded, recursive member walking.
#
# This is the single authority for *iterating* an archive's members. Readers
# and ingestion never open an archive themselves, exactly as they never
# re-implement the container guard above.
# ---------------------------------------------------------------------------


class ArchiveBudget:
	"""Mutable budget shared across an entire (possibly nested) archive walk.

	Enforcing cumulative totals — rather than per-archive limits — is what stops
	a nested archive from multiplying resource use at each level.
	"""

	def __init__(
		self,
		max_members: int = MAX_TOTAL_MEMBERS,
		max_bytes: int = MAX_TOTAL_EXTRACTED,
		max_depth: int = MAX_ARCHIVE_DEPTH,
	):
		self.max_members = max_members
		self.max_bytes = max_bytes
		self.max_depth = max_depth
		self.members_seen = 0
		self.bytes_extracted = 0
		self.truncated = False
		self.notes: list[str] = []

	def note(self, message: str) -> None:
		if message not in self.notes:
			self.notes.append(message)

	def take_member(self) -> bool:
		"""Claim one member slot; False when the global member budget is spent."""
		if self.members_seen >= self.max_members:
			if not self.truncated:
				self.truncated = True
				self.note(f"Archive stopped after {self.max_members} members (limit reached).")
			return False
		self.members_seen += 1
		return True

	def take_bytes(self, size: int) -> bool:
		"""Claim `size` bytes; False when the global size budget is spent."""
		if self.bytes_extracted + size > self.max_bytes:
			if not self.truncated:
				self.truncated = True
				self.note(f"Archive stopped after {self.bytes_extracted} bytes (limit {self.max_bytes}).")
			return False
		self.bytes_extracted += size
		return True


def is_archive(filename: str) -> bool:
	"""True when the filename names a user archive this module can walk."""
	name = (filename or "").lower()
	if name.endswith((".tar.gz", ".tar.bz2", ".tar.xz")):
		return True
	return extension_of(name) in ARCHIVE_EXTENSIONS


def _safe_member_path(name: str) -> str | None:
	"""Normalize an archive member path, rejecting anything unsafe.

	Returns None when the entry must not be extracted: absolute paths, drive
	letters, parent traversal, or paths that escape the archive root.
	"""
	import posixpath

	if not name:
		return None
	normalized = name.replace("\\", "/")
	if normalized.startswith("/") or (len(normalized) > 1 and normalized[1] == ":"):
		return None
	# Resolve `.` and `..` first, then judge the *result*. Rejecting any member
	# whose raw path merely contains ".." would also discard legitimate entries
	# such as "docs/drafts/../final.txt", which normalizes to "docs/final.txt"
	# and never leaves the root. Only an escape is unsafe.
	resolved = posixpath.normpath(normalized)
	if resolved.startswith(("/", "../")) or resolved == "..":
		return None
	if resolved in (".", ""):
		return None
	return resolved


def _iter_zip_members(content: bytes, budget: ArchiveBudget):
	"""Yield (path, bytes) for each regular file in a ZIP archive."""
	try:
		archive = zipfile.ZipFile(BytesIO(content))
	except (zipfile.BadZipFile, zipfile.LargeZipFile) as exc:
		raise ArchiveCorruptError(f"invalid or corrupted archive: {exc}") from exc

	with archive:
		for info in archive.infolist():
			if info.is_dir():
				continue
			safe = _safe_member_path(info.filename)
			if safe is None:
				budget.note(f"Skipped unsafe archive path: {info.filename[:120]}")
				continue
			if info.flag_bits & 0x1:
				budget.note(f"Skipped encrypted member: {safe[:120]}")
				continue
			if info.file_size > MAX_MEMBER_SIZE:
				budget.note(f"Skipped oversized member ({info.file_size} bytes): {safe[:120]}")
				continue
			if info.compress_size and info.file_size / max(info.compress_size, 1) > MAX_RATIO:
				raise ArchiveLimitError(f"compression ratio exceeded for {safe[:120]}")
			if not budget.take_member():
				return
			if not budget.take_bytes(info.file_size):
				return
			try:
				with archive.open(info) as handle:
					yield safe, handle.read(MAX_MEMBER_SIZE + 1)
			except (RuntimeError, zipfile.BadZipFile, OSError) as exc:
				budget.note(f"Member could not be read ({safe[:120]}): {exc}")
				continue


def _iter_tar_members(content: bytes, budget: ArchiveBudget):
	"""Yield (path, bytes) for each regular file in a TAR archive."""
	import tarfile

	try:
		archive = tarfile.open(fileobj=BytesIO(content), mode="r:*")
	except tarfile.TarError as exc:
		raise ArchiveCorruptError(f"invalid or corrupted archive: {exc}") from exc

	with archive:
		for member in archive:
			# Links are never followed: a symlink in an archive is a classic
			# path-escape vector and carries no content of its own.
			if member.issym() or member.islnk():
				budget.note(f"Skipped link member: {member.name[:120]}")
				continue
			if not member.isfile():
				continue
			safe = _safe_member_path(member.name)
			if safe is None:
				budget.note(f"Skipped unsafe archive path: {member.name[:120]}")
				continue
			if member.size > MAX_MEMBER_SIZE:
				budget.note(f"Skipped oversized member ({member.size} bytes): {safe[:120]}")
				continue
			if not budget.take_member():
				return
			if not budget.take_bytes(member.size):
				return
			try:
				handle = archive.extractfile(member)
				if handle is None:
					continue
				yield safe, handle.read(MAX_MEMBER_SIZE + 1)
			except (tarfile.TarError, OSError) as exc:
				budget.note(f"Member could not be read ({safe[:120]}): {exc}")
				continue


def _decompress_single_stream(content: bytes, filename: str, budget: ArchiveBudget):
	"""Handle single-stream .gz/.bz2/.xz that are not tarballs."""
	extension = extension_of(filename)
	openers = {"gz": "gzip", "bz2": "bz2", "xz": "lzma"}
	module_name = openers.get(extension)
	if module_name is None:
		return None
	import importlib

	module = importlib.import_module(module_name)
	try:
		# Read one byte past the cap so truncation is detectable.
		data = module.decompress(content)
	except Exception as exc:
		raise ArchiveCorruptError(f"{filename}: could not decompress: {exc}") from exc
	if len(data) > MAX_MEMBER_SIZE:
		raise ArchiveLimitError(f"{filename}: decompressed member exceeds {MAX_MEMBER_SIZE} bytes")
	if not budget.take_member() or not budget.take_bytes(len(data)):
		return None
	inner = filename.rsplit(".", 1)[0] or "content"
	return [(inner, data)]


def iter_archive_members(content: bytes, filename: str, budget: ArchiveBudget):
	"""Yield ``(member_path, member_bytes)`` for a user archive.

	Dispatches on real content (magic bytes) with the filename only as a hint,
	so a mislabelled archive is still handled safely.
	"""
	import tarfile

	lower = (filename or "").lower()
	is_zip = content[:4] in (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")

	if is_zip:
		yield from _iter_zip_members(content, budget)
		return

	# Try tar (handles .tar, .tar.gz, .tgz, .tar.bz2, .tar.xz transparently).
	try:
		if tarfile.is_tarfile(BytesIO(content)):
			yield from _iter_tar_members(content, budget)
			return
	except Exception:
		pass

	# Compressed single files that are not tarballs.
	if lower.endswith((".gz", ".bz2", ".xz")):
		single = _decompress_single_stream(content, filename, budget)
		if single:
			yield from single
			return

	raise ArchiveCorruptError(f"{filename}: unrecognized or unsupported archive container")
