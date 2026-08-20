# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Shared input validation for the public API facade.

Every whitelisted endpoint that accepts user-controlled limits, offsets,
payload text, identifiers, lists or enums must go through these helpers so
abuse bounds stay consistent across the facade. The caps are the published
platform limits (``docs/DEVELOPMENT_PLAN.md`` §7.1): a caller can never raise
them by passing a larger value, only the platform default applies.

All failures raise :class:`frappe.ValidationError` with a safe, translated
message - no stack detail or internal value is exposed.
"""

from __future__ import annotations

import json
import re

import frappe
from frappe import _
from frappe.utils import cint

# ---------------------------------------------------------------------------
# Published platform caps
# ---------------------------------------------------------------------------

MAX_CHAT_MESSAGE_CHARS = 32_000
MAX_DOCUMENTS_PER_TURN = 25
MAX_KNOWLEDGE_BASES_PER_REQUEST = 25
MAX_TOP_K = 100
MAX_CONVERSATION_PAGE = 200
MAX_MESSAGE_PAGE = 100
MAX_CHUNK_ENTITY_PAGE = 500
MAX_TRANSLATION_PAGE = 200
MAX_LEARNING_PAGE = 200
MAX_USAGE_DAYS = 366
MAX_MODEL_TEST_PROMPT_CHARS = 8_000
MAX_FOLDER_TREE_DEPTH = 20
MAX_BULK_MOVE_ITEMS = 100
MAX_FOLDER_SEARCH_RESULTS = 100
MAX_NAME_LENGTH = 140
MAX_IDEMPOTENCY_KEY_LENGTH = 64
MAX_TITLE_CHARS = 280
MAX_IDENTIFIER_CHARS = 200

#: Document names and File paths are restricted to the same conservative
#: character class Frappe itself uses for doc names.
_NAME_PATTERN = re.compile(r"^[\w\-. /:%@]{1,140}$")

#: An idempotency key is an operator-supplied correlation token, not free text.
_IDEMPOTENCY_KEY_PATTERN = re.compile(r"^[A-Za-z0-9._:\-]{1,64}$")


def _label(label: str) -> str:
	return label or _("value")


def bounded_text(value, *, label, max_length, required: bool = False) -> str:
	"""Coerce `value` to a non-empty (when required), length-bounded string."""
	text = "" if value is None else str(value)
	if required and not text.strip():
		frappe.throw(_("{0} is required.").format(_label(label)), frappe.ValidationError)
	if len(text) > max_length:
		frappe.throw(
			_("{0} supports at most {1} characters.").format(_label(label), max_length),
			frappe.ValidationError,
		)
	return text


def bounded_integer(value, *, label, default, minimum: int = 0, maximum: int) -> int:
	"""Clamp `value` into ``[minimum, maximum]``, defaulting when absent/zero."""
	number = cint(value) or default
	if number < minimum:
		frappe.throw(_("{0} must be at least {1}.").format(_label(label), minimum), frappe.ValidationError)
	return min(number, maximum)


def pagination(limit=None, offset=None, *, default_limit: int, hard_limit: int) -> tuple[int, int]:
	"""Validated ``(limit, offset)`` pair within the published caps."""
	page = bounded_integer(limit, label="limit", default=default_limit, maximum=hard_limit)
	start = bounded_integer(offset, label="offset", default=0, maximum=1_000_000)
	return page, start


def bounded_list(value, *, label, max_items, item_max_length: int = MAX_IDENTIFIER_CHARS) -> list[str]:
	"""Coerce a JSON string / comma string / list into bounded scalar items."""
	if isinstance(value, str):
		raw = value.strip()
		if not raw:
			return []
		if raw.startswith("[") or raw.startswith("{"):
			# JSON-shaped input must actually parse to a JSON array; an object
			# or malformed payload is a caller error, never silently split.
			try:
				value = json.loads(raw)
			except ValueError:
				frappe.throw(_("{0} must be a JSON array.").format(_label(label)), frappe.ValidationError)
			if not isinstance(value, list):
				frappe.throw(_("{0} must be a JSON array.").format(_label(label)), frappe.ValidationError)
		else:
			value = raw.split(",")
	if value is None:
		return []
	if not isinstance(value, list):
		frappe.throw(_("{0} must be a list.").format(_label(label)), frappe.ValidationError)
	if len(value) > max_items:
		frappe.throw(
			_("{0} supports at most {1} entries.").format(_label(label), max_items),
			frappe.ValidationError,
		)
	items: list[str] = []
	for entry in value:
		item = bounded_text(entry, label=label, max_length=item_max_length).strip()
		if item and item not in items:
			items.append(item)
	return items


def enum_choice(value, *, allowed, label, default: str | None = None) -> str | None:
	"""Return a validated enum value; absent values yield `default`."""
	if value in (None, ""):
		return default
	if value not in allowed:
		frappe.throw(
			_("{0} must be one of: {1}.").format(_label(label), ", ".join(allowed)),
			frappe.ValidationError,
		)
	return value


def valid_identifier(value, *, label, required: bool = False) -> str:
	"""Validate a document name, File path or other stable identifier."""
	identifier = bounded_text(value, label=label, max_length=MAX_NAME_LENGTH, required=required)
	if identifier and not _NAME_PATTERN.match(identifier):
		frappe.throw(_("{0} contains unsupported characters.").format(_label(label)), frappe.ValidationError)
	return identifier


def idempotency_key(value) -> str | None:
	"""Validate an optional idempotency key (bounded correlation token)."""
	if value in (None, ""):
		return None
	key = str(value).strip()
	if not _IDEMPOTENCY_KEY_PATTERN.match(key):
		frappe.throw(
			_("Idempotency key supports at most {0} characters from letters, numbers and . _ : -.").format(
				MAX_IDEMPOTENCY_KEY_LENGTH
			),
			frappe.ValidationError,
		)
	return key


def bounded_payload(value, *, label, max_bytes: int) -> str:
	"""Validate a JSON-encoded payload string against a byte budget."""
	payload = bounded_text(value, label=label, max_length=max_bytes, required=False)
	if len(payload.encode("utf-8")) > max_bytes:
		frappe.throw(
			_("{0} supports at most {1} bytes.").format(_label(label), max_bytes),
			frappe.ValidationError,
		)
	return payload
