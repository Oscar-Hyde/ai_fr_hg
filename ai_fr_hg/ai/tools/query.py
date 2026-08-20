# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Central permission-aware document query mechanism for generic AI tools.

Every generic tool that reads Frappe records - the builtin ``get_document``,
``list_documents`` and ``count_documents`` handlers and the configurable
``DocType Query`` tool - funnels through this module, so none of them can
drift into a separate permission implementation.

Each call composes native Frappe permission primitives instead of recreating
them:

1. **DocType permission** - ``frappe.has_permission(doctype, "read")``.
2. **Row-level permission** - ``frappe.get_list`` / ``frappe.get_doc``, which
   apply every registered ``permission_query_conditions`` and
   ``has_permission`` hook (the AI DocTypes register their rules in
   :mod:`ai_fr_hg.utils.permissions`).
3. **Field-level permission** - ``frappe.model.get_permitted_fields``, which
   already respects per-role ``permlevel`` rules.
4. **Sensitive-field denial** - ``Password`` fields, a conservative name
   pattern list, and the operator-configured deny list, on top of the
   field-level rules above.
5. **Bounded results** - hard caps on list size, requested fields, filter
   keys, filter list lengths and the exact-count scan budget.

Write-side helpers apply the same field rules so a model can never inject a
restricted value through a ``DocType Action`` tool either.
"""

from __future__ import annotations

import json

import frappe
from frappe import _
from frappe.model import get_permitted_fields
from frappe.utils import cint

from ai_fr_hg.ai.exceptions import ToolExecutionError

#: Default row count returned when the caller does not choose.
DEFAULT_LIST_LIMIT = 20
#: Hard row cap for any single generic read call.
HARD_LIST_LIMIT = 100
#: Exact-count scan budget. Beyond this the tool reports a bounded count.
COUNT_SCAN_CAP = 5000
#: Hard cap on projected fields per call (including the unrequested dump).
MAX_FIELDS = 25
#: Hard cap on filter keys per call.
MAX_FILTER_KEYS = 20
#: Hard cap on list length inside a filter value (IN lists etc.).
MAX_FILTER_VALUES = 100
#: Hard cap on a filter value string.
MAX_FILTER_VALUE_CHARS = 200

#: Standard identity/audit fields that every DocType exposes and that are
#: never sensitive. They pass the field rules even when a caller requests them
#: explicitly, but are not part of an unrequested field dump.
STANDARD_FIELDS = {"name", "creation", "modified", "owner", "docstatus"}

#: Field types that must never reach a model-facing tool result.
SENSITIVE_FIELD_TYPES = {"Password"}

#: Exact lower-cased field names that are denied outright.
SENSITIVE_FIELD_NAMES = {
	"password",
	"passwd",
	"pwd",
	"token",
	"secret",
	"api_key",
	"apikey",
	"api_secret",
	"api_token",
	"auth_token",
	"access_token",
	"refresh_token",
	"client_secret",
	"client_key",
	"private_key",
	"secret_key",
	"credential",
	"credentials",
	"session",
	"session_id",
	"session_key",
	"otp",
	"pin",
	"auth",
	"authorization",
}

#: Substrings that make a field name sensitive wherever they appear.
SENSITIVE_FIELD_SUBSTRINGS = ("password", "passwd", "token", "secret", "api_key", "private_key")


def configured_sensitive_fields() -> set[str]:
	"""Operator-configured extra deny entries from AI Platform Settings.

	Each entry is a bare field name (applies to every DocType) or a scoped
	``Doctype.fieldname`` entry.
	"""
	value = frappe.db.get_single_value("AI Platform Settings", "tool_sensitive_fields", cache=False) or ""
	return {item.strip() for item in value.replace(",", "\n").splitlines() if item.strip()}


def denied_fieldnames(doctype: str) -> set[str]:
	"""Every field of `doctype` that generic tools must never expose."""
	meta = frappe.get_meta(doctype)
	denied: set[str] = set()
	for field in meta.fields:
		if field.fieldtype in SENSITIVE_FIELD_TYPES:
			denied.add(field.fieldname)
		name = (field.fieldname or "").lower()
		if name in SENSITIVE_FIELD_NAMES or any(marker in name for marker in SENSITIVE_FIELD_SUBSTRINGS):
			denied.add(field.fieldname)
	for entry in configured_sensitive_fields():
		if "." in entry:
			scoped_doctype, _, fieldname = entry.partition(".")
			if scoped_doctype == doctype and fieldname:
				denied.add(fieldname)
		else:
			denied.add(entry)
	return denied


def _assert_readable(doctype: str, user: str | None) -> None:
	"""Reject a DocType the caller has no read rule for, before any query."""
	user = user or frappe.session.user
	if not frappe.has_permission(doctype, "read", user=user):
		frappe.throw(_("You are not permitted to read {0}.").format(doctype), frappe.PermissionError)


def _assert_readable_record(doctype: str, name: str, user: str | None) -> None:
	"""Reject one record the caller cannot read (row-level rules run here)."""
	user = user or frappe.session.user
	if not frappe.has_permission(doctype, "read", doc=name, user=user):
		frappe.throw(
			_("You are not permitted to read {0} {1}.").format(doctype, name), frappe.PermissionError
		)


def _normalise_filters(filters, doctype: str, readable: set[str]) -> dict:
	"""Coerce the tool filter contract and strip keys outside read authority.

	A filter on a denied or unreadable field is itself an exfiltration channel
	(``{"api_key": ["!=", ""]}``), so those keys are dropped rather than run.
	"""
	if filters is None:
		return {}
	if isinstance(filters, str):
		try:
			filters = json.loads(filters)
		except ValueError as error:
			raise ToolExecutionError(_("Filters must be a JSON object.")) from error
	if not isinstance(filters, dict):
		raise ToolExecutionError(_("Filters must be a JSON object."))

	if len(filters) > MAX_FILTER_KEYS:
		raise ToolExecutionError(_("A query supports at most {0} filter keys.").format(MAX_FILTER_KEYS))

	# `name` is inside STANDARD_FIELDS, so filtering on the record identity
	# always remains possible; anything outside the readable set is dropped
	# to prevent blind probing through aggregate or inequality filters.
	allowed_fields = readable | STANDARD_FIELDS
	cleaned: dict = {}
	for key, value in filters.items():
		if key not in allowed_fields:
			continue
		if isinstance(value, list):
			if len(value) > MAX_FILTER_VALUES:
				raise ToolExecutionError(
					_("A filter list supports at most {0} values.").format(MAX_FILTER_VALUES)
				)
			try:
				value = [_bounded_scalar(item) for item in value]
			except ToolExecutionError:
				# Malformed operator shapes (e.g. ["in", [[...]]]) are dropped
				# rather than run: a model-supplied filter must not explode.
				continue
		elif isinstance(value, dict):
			continue  # dict/aggregate syntax is never accepted from a tool
		else:
			value = _bounded_scalar(value)
		cleaned[key] = value
	return cleaned


def _bounded_scalar(value):
	if isinstance(value, str) and len(value) > MAX_FILTER_VALUE_CHARS:
		raise ToolExecutionError(
			_("A filter value supports at most {0} characters.").format(MAX_FILTER_VALUE_CHARS)
		)
	if isinstance(value, (str, int, float, bool)) or value is None:
		return value
	raise ToolExecutionError(_("Filter values must be strings, numbers or booleans."))


def _normalise_fields(meta, requested, readable: set[str], deny: set[str], *, allowlist=None) -> list[str]:
	"""Intersect requested fields with readable, non-denied, allowed fields."""
	allowed = readable - deny
	if allowlist:
		# A configured per-tool allowlist is an additional restriction; it
		# never grants a field the caller cannot read.
		allowed = allowed.intersection(allowlist)

	if requested:
		fields = []
		for raw in requested:
			field = str(raw).strip()
			if field in allowed or field in STANDARD_FIELDS:
				fields.append(field)
			if len(fields) >= MAX_FIELDS:
				break
		return fields or ["name"]
	# No explicit request: return readable non-denied fields, bounded to a
	# deterministic prefix. The caller-side tools surface the truncation
	# marker instead of failing the whole call on a wide DocType.
	fields = sorted(allowed)
	return fields[:MAX_FIELDS] or ["name"]


def _normalise_order_by(order_by, readable: set[str], deny: set[str]) -> str:
	if not order_by:
		return "modified desc"
	allowed = readable - deny
	clauses = []
	for raw in str(order_by).split(","):
		clause = raw.strip()
		if not clause:
			continue
		parts = clause.split()
		field = parts[0]
		if field in allowed or field in STANDARD_FIELDS:
			clauses.append(clause)
	return ", ".join(clauses) or "modified desc"


def readable_fields(doctype: str, user: str | None = None) -> tuple[set[str], set[str]]:
	"""``(permitted, denied)`` field sets for one DocType and user.

	Permitted comes from Frappe's own permlevel-aware rules; denied layers the
	sensitive-field policy on top.
	"""
	user = user or frappe.session.user
	permitted = set(get_permitted_fields(doctype, user=user, permission_type="read"))
	return permitted, denied_fieldnames(doctype)


def safe_get(
	doctype: str,
	name: str,
	fields=None,
	*,
	user: str | None = None,
	allowlist: set[str] | None = None,
) -> dict:
	"""Fetch one document with row- and field-level enforcement."""
	if not doctype or not name:
		raise ToolExecutionError(_("Both a DocType and a document name are required."))
	_assert_readable_record(doctype, name, user)
	meta = frappe.get_meta(doctype)
	permitted, deny = readable_fields(doctype, user)
	projected = _normalise_fields(meta, fields, permitted, deny, allowlist=allowlist)
	truncated = not fields and len(sorted(permitted - deny)) > len(projected)
	doc = frappe.get_doc(doctype, name)
	result = {}
	for field in projected:
		# A field could be readable on the DocType but restricted on this
		# record by has_permission; the record check above is the authority.
		result[field] = doc.get(field)
	if truncated:
		result["_fields_truncated"] = True
	return result


def safe_list(
	doctype: str,
	filters=None,
	fields=None,
	limit=None,
	order_by=None,
	*,
	user: str | None = None,
	allowlist: set[str] | None = None,
) -> list[dict]:
	"""List records through the caller's row- and field-level permission.

	Row-level conditions are evaluated by ``frappe.get_list`` against the
	active session; pass ``user`` only to mirror the session with
	``frappe.set_user`` first.
	"""
	if not doctype:
		raise ToolExecutionError(_("A DocType is required."))
	_assert_readable(doctype, user)
	meta = frappe.get_meta(doctype)
	permitted, deny = readable_fields(doctype, user)
	cleaned_filters = _normalise_filters(filters, doctype, permitted - deny)
	projected = _normalise_fields(meta, fields, permitted, deny, allowlist=allowlist)
	row_limit = max(min(cint(limit) or DEFAULT_LIST_LIMIT, HARD_LIST_LIMIT), 1)
	return frappe.get_list(
		doctype,
		filters=cleaned_filters or None,
		fields=projected,
		limit=row_limit,
		order_by=_normalise_order_by(order_by, permitted, deny),
	)


def safe_count(doctype: str, filters=None, *, user: str | None = None) -> dict:
	"""Permission-aware, bounded record count.

	``frappe.db.count`` bypasses row-level permission query conditions, so the
	count runs through ``frappe.get_list`` instead: every row the caller could
	not list is invisible to the aggregate too. The scan is bounded; beyond
	the budget the tool reports an exact lower bound and flags the count as
	bounded instead of pretending to be exact.
	"""
	if not doctype:
		raise ToolExecutionError(_("A DocType is required."))
	_assert_readable(doctype, user)
	permitted, deny = readable_fields(doctype, user)
	cleaned_filters = _normalise_filters(filters, doctype, permitted - deny)
	names = frappe.get_list(
		doctype,
		filters=cleaned_filters or None,
		pluck="name",
		limit=COUNT_SCAN_CAP + 1,
		order_by="name asc",
	)
	exact = len(names) <= COUNT_SCAN_CAP
	return {
		"doctype": doctype,
		"count": len(names) if exact else COUNT_SCAN_CAP,
		"exact": exact,
		"bounded": not exact,
	}


def safe_field_values(doctype: str, values: dict, *, user: str | None = None) -> dict:
	"""Strip every key of `values` the caller may not write (or that is denied).

	Used by write tools before ``doc.update`` so a model-supplied payload can
	never set a restricted field even when the DocType-level write rule passed.
	"""
	if not isinstance(values, dict):
		raise ToolExecutionError(_("Values must be a JSON object."))
	user = user or frappe.session.user
	permitted = set(get_permitted_fields(doctype, user=user, permission_type="write"))
	deny = denied_fieldnames(doctype)
	allowed = permitted - deny
	return {key: value for key, value in values.items() if key in allowed}
