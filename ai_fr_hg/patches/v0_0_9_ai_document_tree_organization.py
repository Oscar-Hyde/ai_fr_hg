# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Backfill the canonical fields used by the native AI Document Tree View.

``File`` remains the sole folder hierarchy. This patch records the stable
source File identity, normalises root-level documents to ``Home``, and assigns
collision-free organization names before creating the compound location index.
It deliberately does not create or maintain a second Nested Set.
"""

from __future__ import annotations

import re
from collections import OrderedDict

import frappe

from ai_fr_hg.ai.organization import organization_name_key

_COPY_SUFFIX = re.compile(r"^(?P<stem>.*?)(?: \(Copy(?: (?P<number>\d+))?\))?(?P<ext>\.[^.]+)?$")
_MAX_NAME_LENGTH = 140
_BATCH_SIZE = 400
_COLLISION_QUERY_SIZE = 50
_COPY_HINT_LIMIT = 2_048


def _candidate(value: str, number: int) -> str:
	match = _COPY_SUFFIX.match(value or "Document")
	stem = (match.group("stem") if match else value) or "Document"
	ext = (match.group("ext") if match else "") or ""
	suffix = " (Copy)" if number == 1 else f" (Copy {number})"
	available = _MAX_NAME_LENGTH - len(suffix) - len(ext)
	if available < 1:
		# Repair pathological legacy names while keeping every generated name
		# inside Frappe's Data-field limit.
		stem = value or "Document"
		ext = ""
		available = _MAX_NAME_LENGTH - len(suffix)
	return f"{stem[:available]}{suffix}{ext}"


def _chunks(values: list, size: int) -> list:
	for index in range(0, len(values), size):
		yield values[index : index + size]


def _existing_pairs(pairs: set[tuple[str, str]]) -> set[tuple[str, str]]:
	"""Fetch exact occupied location/name pairs with constant query bounds."""
	result = set()
	for batch in _chunks(sorted(pairs), _COLLISION_QUERY_SIZE):
		expected = set(batch)
		folders = sorted({folder for folder, _key in batch})
		keys = sorted({key for _folder, key in batch})
		for row in frappe.get_all(
			"AI Document",
			filters={"folder": ["in", folders], "organization_name_key": ["in", keys]},
			fields=["folder", "organization_name_key"],
			limit_page_length=0,
		):
			pair = (row.folder or "Home", row.organization_name_key)
			if pair in expected:
				result.add(pair)
	return result


def _remember_hint(hints: OrderedDict, key: tuple[str, str], number: int) -> None:
	hints[key] = number
	hints.move_to_end(key)
	if len(hints) > _COPY_HINT_LIMIT:
		hints.popitem(last=False)


def _paged_files(filters: dict):
	"""Stream File candidates without materializing every duplicate URL row."""
	offset = 0
	while True:
		rows = frappe.get_all(
			"File",
			filters=filters,
			fields=[
				"name",
				"file_name",
				"file_url",
				"folder",
				"attached_to_doctype",
				"attached_to_name",
				"creation",
			],
			order_by="creation asc, name asc",
			limit_start=offset,
			limit_page_length=_BATCH_SIZE,
		)
		if not rows:
			return
		yield from rows
		offset += len(rows)


def _resolve_batch_names(
	resolved: list,
	existing_folders: set[str],
	hints: OrderedDict,
) -> list[tuple[object, object, str, str]]:
	prepared = []
	base_pairs = set()
	for row, file_row in resolved:
		requested_folder = (file_row and file_row.folder) or row.folder or "Home"
		folder = requested_folder if requested_folder in existing_folders else "Home"
		base = (row.organization_name or (file_row and file_row.file_name) or row.title or row.name).strip()
		base = base[:_MAX_NAME_LENGTH] or "Document"
		prepared.append([row, file_row, folder, base, base, None])
		base_pairs.add((folder, organization_name_key(base)))

	occupied = _existing_pairs(base_pairs)
	reserved = set()
	for item in prepared:
		folder, base = item[2], item[3]
		origin = (folder, organization_name_key(base))
		name = base
		number = hints.get(origin, 1)
		while (folder, organization_name_key(name)) in occupied or (folder, organization_name_key(name)) in reserved:
			name = _candidate(base, number)
			number += 1
		_remember_hint(hints, origin, number)
		item[4] = name
		item[5] = number
		reserved.add((folder, organization_name_key(name)))

	# Generated candidates can collide with names assigned by an earlier page.
	# Resolve all such conflicts in bounded rounds without retaining repository-
	# sized Python sets.
	while conflicts := _existing_pairs({(item[2], organization_name_key(item[4])) for item in prepared}):
		occupied.update(conflicts)
		changed = False
		for item in prepared:
			folder, base, name = item[2], item[3], item[4]
			if (folder, organization_name_key(name)) not in conflicts:
				continue
			changed = True
			reserved.discard((folder, organization_name_key(name)))
			origin = (folder, organization_name_key(base))
			number = max(item[5] or 1, hints.get(origin, 1))
			while True:
				name = _candidate(base, number)
				number += 1
				if (folder, organization_name_key(name)) not in occupied and (folder, organization_name_key(name)) not in reserved:
					break
			item[4] = name
			item[5] = number
			reserved.add((folder, organization_name_key(name)))
			_remember_hint(hints, origin, number)
		if not changed:  # Defensive guard; conflicts always correspond to an item.
			break

	return [(row, file_row, folder, name) for row, file_row, folder, _base, name, _number in prepared]


def execute() -> None:
	if not frappe.db.exists("DocType", "AI Document"):
		return

	# A patch may be retried after partial execution. Clear only the derived key
	# so subsequent bounded pages see exactly the names assigned during this run.
	document_table = frappe.qb.DocType("AI Document")
	frappe.qb.update(document_table).set(document_table.organization_name_key, None).run()

	hints: OrderedDict = OrderedDict()
	offset = 0
	while True:
		rows = frappe.get_all(
			"AI Document",
			fields=["name", "title", "source_type", "source_file", "folder", "organization_name"],
			order_by="creation asc, name asc",
			limit_start=offset,
			limit_page_length=_BATCH_SIZE,
		)
		if not rows:
			break
		offset += len(rows)

		# Prefer the oldest File attached to this exact document, then the
		# oldest File for its URL. Separate fixed-size streams keep pathological
		# duplicate URLs bounded without issuing a query per document.
		file_documents = {
			row.name: row for row in rows if row.source_type == "File" and row.source_file
		}
		urls = sorted({row.source_file for row in file_documents.values()})
		exact_files = {}
		if urls:
			for item in _paged_files(
				{
					"file_url": ["in", urls],
					"is_folder": 0,
					"attached_to_doctype": "AI Document",
					"attached_to_name": ["in", list(file_documents)],
				}
			):
				document = file_documents.get(item.attached_to_name)
				if document and document.source_file == item.file_url:
					exact_files.setdefault(document.name, item)

		fallback_urls = sorted(
			{
				document.source_file
				for document in file_documents.values()
				if document.name not in exact_files
			}
		)
		fallback_files = {}
		for url_batch in _chunks(fallback_urls, _BATCH_SIZE):
			for item in _paged_files({"file_url": ["in", url_batch], "is_folder": 0}):
				fallback_files.setdefault(item.file_url, item)

		resolved = []
		for row in rows:
			file_row = (
				exact_files.get(row.name) or fallback_files.get(row.source_file)
				if row.source_type == "File"
				else None
			)
			resolved.append((row, file_row))

		requested_folders = sorted({(file_row and file_row.folder) or row.folder or "Home" for row, file_row in resolved})
		existing_folders = set(
			frappe.get_all(
				"File",
				filters={"name": ["in", requested_folders], "is_folder": 1},
				pluck="name",
				limit_page_length=0,
			)
		)
		for row, file_row, folder, name in _resolve_batch_names(resolved, existing_folders, hints):
			frappe.db.set_value(
				"AI Document",
				row.name,
				{
					"folder": folder,
					"source_folder": folder,
					"source_file_record": file_row.name if file_row else None,
					"organization_name": name,
					"organization_name_key": organization_name_key(name),
					"organization_revision": 0,
				},
				update_modified=False,
			)

	# Frappe owns creation/removal of this database index; mutations never touch
	# framework tree metadata or lft/rgt values.
	try:
		frappe.db.add_unique(
			"AI Document",
			["folder", "organization_name_key"],
			constraint_name="uniq_ai_document_folder_organization_name",
		)
	except TypeError:
		# Older database adapters do not accept the named-constraint keyword.
		frappe.db.add_unique("AI Document", ["folder", "organization_name_key"])
