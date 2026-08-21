# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Pure automation helpers (no database).

Field-contract and snapshot sanitization live here so unit tests can prove
AUTO-01/AUTO-02 without a Frappe site. Runtime still re-validates against
live metadata.
"""

from __future__ import annotations

import json
import re

ALLOWED_SOURCE_FIELDTYPES = frozenset(
	{
		"Check",
		"Code",
		"Currency",
		"Data",
		"Date",
		"Datetime",
		"Duration",
		"Dynamic Link",
		"Float",
		"HTML Editor",
		"Int",
		"Link",
		"Long Text",
		"Markdown Editor",
		"Percent",
		"Read Only",
		"Select",
		"Small Text",
		"Text",
		"Text Editor",
		"Time",
	}
)

DISALLOWED_SOURCE_FIELDTYPES = frozenset(
	{
		"Attach",
		"Attach Image",
		"Button",
		"Column Break",
		"Fold",
		"Heading",
		"HTML",
		"Image",
		"Password",
		"Section Break",
		"Tab Break",
		"Table",
		"Table MultiSelect",
	}
)

_SENSITIVE_NAME = re.compile(
	r"(password|passwd|secret|token|api[_-]?key|private[_-]?key|credential|auth)",
	re.IGNORECASE,
)

MAX_SNAPSHOT_CHARS = 100_000
MAX_SNAPSHOT_FIELDS = 80


def is_sensitive_field_name(fieldname: str) -> bool:
	return bool(fieldname and _SENSITIVE_NAME.search(fieldname))


def is_allowed_source_fieldtype(fieldtype: str | None) -> bool:
	return (fieldtype or "") in ALLOWED_SOURCE_FIELDTYPES


def source_field_error(fieldname: str | None, fieldtype: str | None, *, exists: bool) -> str | None:
	"""Return a stable error code, or None when the field is usable as source text."""
	if not fieldname:
		return None
	if "." in fieldname:
		return "child_table_path"
	if not exists:
		return "missing"
	if fieldtype in DISALLOWED_SOURCE_FIELDTYPES or not is_allowed_source_fieldtype(fieldtype):
		return "disallowed_type"
	if is_sensitive_field_name(fieldname) or fieldtype == "Password":
		return "sensitive"
	return None


def sanitize_snapshot(payload: dict, *, denied_fields: set[str] | None = None) -> dict:
	"""Return a JSON-safe, bounded, non-sensitive snapshot of a document dict."""
	denied = {name.lower() for name in (denied_fields or set())}
	clean: dict = {}
	count = 0
	for key, value in (payload or {}).items():
		if not isinstance(key, str):
			continue
		if key.startswith("_") or key in {"flags", "permissions"}:
			continue
		if is_sensitive_field_name(key) or key.lower() in denied:
			continue
		if isinstance(value, (list, dict)) and key not in {"doctype", "name"}:
			# Child tables and nested objects are not a source-text contract.
			continue
		if callable(value):
			continue
		try:
			json.dumps(value, default=str)
		except (TypeError, ValueError):
			value = str(value)
		clean[key] = value
		count += 1
		if count >= MAX_SNAPSHOT_FIELDS:
			break
	encoded = json.dumps(clean, default=str)
	if len(encoded) > MAX_SNAPSHOT_CHARS:
		clean = {key: clean[key] for key in ("doctype", "name", "modified", "owner") if key in clean}
	return clean


def event_revision_key(rule: str, doctype: str, docname: str, modified: str) -> str:
	"""Stable identity for one rule + document revision."""
	return f"{rule}::{doctype}::{docname}::{modified}"
