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

	assignments = []
	params = []
	for field, field_value in values.items():
		column = field if field.startswith("`") else f"`{field}`"
		assignments.append(f"{column} = %s")
		params.append(field_value)

	table = doctype if doctype.startswith("tab") else f"tab{doctype}"
	params.append(name)
	frappe.db.sql(
		f"update `{table}` set {', '.join(assignments)} where name = %s",
		tuple(params),
	)
	frappe.clear_cache(doctype=doctype, name=name)
