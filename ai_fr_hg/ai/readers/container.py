# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Archive reader — recursive, bounded, evidence-preserving container reading.

Part 1 §6.2 "Archives" requires safe extraction, recursive processing, file
relationship tracking, and protection against unsafe input. This module
provides all four:

* **Safe extraction** — every member path, size, ratio, and encryption flag is
  vetted by `ai.readers.archive`, the single container-policy authority. Links
  are never followed and traversal paths are refused.
* **Recursive processing** — a nested archive is walked in place, up to
  `MAX_ARCHIVE_DEPTH`, under one cumulative `ArchiveBudget` shared by the whole
  tree so nesting cannot multiply cost.
* **File relationship tracking** — every member appears in `structure` with its
  path, depth, parent, and the reader that handled it, so the containment tree
  is fully reconstructable from the evidence.
* **Protection against unsafe input** — cumulative member/byte ceilings, a
  depth ceiling, cycle-safe descent, and per-member failure isolation.

**Relationship to ADR-003.** Archive members do not become synthetic folders or
independent `AI Document` rows. The archive is *one* document whose text is the
concatenation of its members and whose structure records the containment tree.
Frappe `File` therefore remains the sole folder authority, and no second
recursive tree competes with it.
"""

from __future__ import annotations

from ai_fr_hg.ai.readers.archive import (
	MAX_ARCHIVE_DEPTH,
	ArchiveBudget,
	ArchiveCorruptError,
	ArchiveLimitError,
	is_archive,
	iter_archive_members,
)
from ai_fr_hg.ai.readers.base import BaseReader, ReadResult

#: Member text is truncated at this size in the combined document so one large
#: member cannot dominate the archive's extracted text.
MAX_MEMBER_TEXT = 200_000


class ArchiveReader(BaseReader):
	"""Read every supported member of an archive into one document."""

	label = "Archive"
	version = "1.0"

	def read(self, content: bytes, filename: str) -> ReadResult:
		budget = ArchiveBudget()
		parts: list[str] = []
		structure: list[dict] = []
		embedded: list[dict] = []
		warnings: list[str] = []

		try:
			self._walk(
				content=content,
				filename=filename,
				parent=filename or "archive",
				depth=0,
				budget=budget,
				parts=parts,
				structure=structure,
				embedded=embedded,
				warnings=warnings,
			)
		except (ArchiveCorruptError, ArchiveLimitError):
			# Container policy failures are the caller's to translate into the
			# platform's document exceptions, exactly like the Office readers.
			raise

		for note in budget.notes:
			warnings.append(note)

		members = [block for block in structure if block["kind"] == "archive_member"]
		read_count = sum(1 for block in members if block.get("read"))
		metadata = {
			"format": "archive",
			"member_count": len(members),
			"members_read": read_count,
			"members_skipped": len(members) - read_count,
			"bytes_extracted": budget.bytes_extracted,
			"truncated": budget.truncated,
			"max_depth_reached": max([block.get("depth", 0) for block in members], default=0),
		}

		return ReadResult(
			# Members are joined verbatim: each reader already normalized its own
			# output, and `clean()` here would collapse indentation that
			# indentation-sensitive members (source code) depend on.
			text="\n\n".join(parts).strip(),
			page_count=len(members),
			metadata=metadata,
			warnings=warnings,
			structure=structure,
			embedded_objects=embedded,
		)

	def _walk(
		self,
		*,
		content: bytes,
		filename: str,
		parent: str,
		depth: int,
		budget: ArchiveBudget,
		parts: list[str],
		structure: list[dict],
		embedded: list[dict],
		warnings: list[str],
	) -> None:
		"""Walk one archive level, recursing into nested archives."""
		from ai_fr_hg.ai.readers import get_reader

		for path, data in iter_archive_members(content, filename, budget):
			member_id = f"{parent}!{path}"
			block: dict = {
				"kind": "archive_member",
				"path": path,
				"parent": parent,
				"depth": depth,
				"bytes": len(data),
				"read": False,
			}

			# Nested archive: descend when the depth budget allows.
			if is_archive(path):
				block["kind"] = "archive_member"
				block["is_archive"] = True
				if depth + 1 > budget.max_depth:
					block["skipped_reason"] = "max_depth"
					budget.note(
						f"Nested archive not expanded (depth limit {MAX_ARCHIVE_DEPTH}): {path[:120]}"
					)
					structure.append(block)
					continue
				structure.append(block)
				try:
					self._walk(
						content=data,
						filename=path,
						parent=member_id,
						depth=depth + 1,
						budget=budget,
						parts=parts,
						structure=structure,
						embedded=embedded,
						warnings=warnings,
					)
				except (ArchiveCorruptError, ArchiveLimitError) as exc:
					# One bad nested archive must not destroy the whole result.
					budget.note(f"Nested archive could not be read ({path[:120]}): {exc}")
				continue

			reader = get_reader(path)
			if reader is None:
				block["skipped_reason"] = "unsupported_format"
				embedded.append({"kind": "unsupported_member", "name": path[:200], "location": parent[:200]})
				structure.append(block)
				continue

			# An archive member must never be able to fail the whole archive.
			try:
				result = reader.read(data, path)
			except Exception as exc:
				block["skipped_reason"] = "read_failed"
				block["error"] = str(exc)[:300]
				budget.note(f"Member could not be parsed ({path[:120]}): {str(exc)[:160]}")
				structure.append(block)
				continue

			text = (result.text or "").strip()
			if len(text) > MAX_MEMBER_TEXT:
				text = text[:MAX_MEMBER_TEXT]
				budget.note(f"Member text truncated at {MAX_MEMBER_TEXT} characters: {path[:120]}")
			block["read"] = True
			block["reader"] = reader.label
			block["characters"] = len(text)
			structure.append(block)

			# Preserve each member's own warnings, attributed to its path.
			for warning in result.warnings or []:
				warnings.append(f"{path}: {warning}" if isinstance(warning, str) else warning)
			for item in result.embedded_objects or []:
				entry = dict(item)
				entry["location"] = f"{path}:{entry.get('location', '')}".rstrip(":")
				embedded.append(entry)

			if text:
				parts.append(f"[Archive member: {path}]\n{text}")
