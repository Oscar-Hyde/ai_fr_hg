# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Small database helpers used by hot paths and background reconciliation."""

from __future__ import annotations

from collections.abc import Mapping

import frappe
from frappe.utils import now_datetime


def safe_set_value(
	doctype: str,
	name: str,
	values: Mapping | str,
	value=None,
	*,
	update_modified: bool = True,
):
	"""Update one or more columns without read-modify-write timestamp conflicts.

	Runtime records are written by several concurrent paths: chat metrics,
	health checks, discovery, pull jobs and scheduled syncs. A normal ORM
	``db_set`` can fail with ``1020 Record has changed since last read`` when
	another worker updates the row first. A direct SQL update avoids both the
	stale-read check and the overhead of loading a full document on every
	telemetry write.
	"""
	if isinstance(values, str):
		values = {values: value}
	values = dict(values)
	if not values:
		return

	if update_modified:
		values = {
			**values,
			"modified": now_datetime(),
			"modified_by": frappe.session.user,
		}

	meta = frappe.get_meta(doctype.removeprefix("tab"))

	# Singles are persisted by Frappe in ``tabSingles``, not in a table named
	# after the DocType.  Keep this helper on Frappe's canonical Single API
	# instead of issuing the hot-path SQL used for ordinary DocTypes.
	if meta.issingle:
		for field, field_value in values.items():
			field = field.strip("`")
			if field in {"modified", "modified_by"}:
				continue
			if not meta.has_field(field):
				frappe.throw(f"Cannot update unknown field {field} on {meta.name}.")
			frappe.db.set_single_value(meta.name, field, field_value)
		frappe.clear_cache(doctype=meta.name)
		return

	allowed_standard_fields = {"modified", "modified_by"}
	assignments = []
	params = []
	for field, field_value in values.items():
		field = field.strip("`")
		if field not in allowed_standard_fields and not meta.has_field(field):
			frappe.throw(f"Cannot update unknown field {field} on {meta.name}.")
		assignments.append(f"`{field}` = %s")
		params.append(field_value)

	table = f"tab{meta.name}"
	params.append(name)
	frappe.db.sql(  # nosemgrep: frappe-sql-format-injection -- identifiers are validated against DocType metadata above
		f"update `{table}` set {', '.join(assignments)} where name = %s",
		tuple(params),
	)
	frappe.clear_document_cache(meta.name, name)
