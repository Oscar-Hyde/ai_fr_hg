# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Part 1 File Intelligence completion — idempotent schema and data backfill.

Covers:

* `AI Pattern Entity.extraction_method` / `confidence` / `model_used` — existing
  rows are deterministic pattern matches, so they are stamped `pattern` with no
  confidence (an exact match has no meaningful confidence score).
* `AI Platform Settings.semantic_entities_enabled` / `semantic_confidence_floor`
  — semantic extraction is opt-in and starts disabled, so no existing site
  silently begins spending model calls after an upgrade.
* Documents extracted before evidence carried a timestamp and version are left
  with their existing evidence intact but flagged, so operators can identify
  exactly which documents predate the versioned extractor rather than guessing.
"""

from __future__ import annotations

import json

import frappe


def execute():
	_backfill_pattern_entity_columns()
	_backfill_platform_settings()
	_flag_unversioned_extraction_evidence()


def _backfill_pattern_entity_columns() -> None:
	if not frappe.db.table_exists("AI Pattern Entity"):
		return

	if not frappe.db.has_column("AI Pattern Entity", "extraction_method"):
		frappe.db.sql(
			"ALTER TABLE `tabAI Pattern Entity` ADD COLUMN `extraction_method` varchar(140) DEFAULT 'pattern'"
		)
	if not frappe.db.has_column("AI Pattern Entity", "confidence"):
		frappe.db.sql("ALTER TABLE `tabAI Pattern Entity` ADD COLUMN `confidence` decimal(21,9) DEFAULT 0")
	if not frappe.db.has_column("AI Pattern Entity", "model_used"):
		frappe.db.sql("ALTER TABLE `tabAI Pattern Entity` ADD COLUMN `model_used` varchar(140)")

	# Every pre-existing row was produced by the deterministic regex layer.
	frappe.db.sql(
		"""
		update `tabAI Pattern Entity`
		set extraction_method = 'pattern'
		where extraction_method is null or extraction_method = ''
		"""
	)
	# A deterministic match is exact; a confidence number on it would be invented.
	frappe.db.sql(
		"""
		update `tabAI Pattern Entity`
		set confidence = 0
		where extraction_method = 'pattern' and confidence is not null and confidence <> 0
		"""
	)


def _backfill_platform_settings() -> None:
	if not frappe.db.table_exists("AI Platform Settings"):
		return
	# Semantic extraction costs model calls, so an upgrade must never enable it.
	if frappe.db.get_single_value("AI Platform Settings", "semantic_entities_enabled") is None:
		frappe.db.set_single_value("AI Platform Settings", "semantic_entities_enabled", 0)
	current_floor = frappe.db.get_single_value("AI Platform Settings", "semantic_confidence_floor")
	if not current_floor:
		frappe.db.set_single_value("AI Platform Settings", "semantic_confidence_floor", 50)


def _flag_unversioned_extraction_evidence() -> None:
	"""Mark evidence produced before extraction recorded timestamp/version.

	The content itself is untouched. A `versions.app = "pre-0.0.2"` marker makes
	the previously unanswerable question -- "which extractor version produced
	this document?" -- answerable for historical rows too, instead of leaving a
	silent gap.
	"""
	if not frappe.db.table_exists("AI Document"):
		return
	if not frappe.db.has_column("AI Document", "extraction_evidence"):
		return

	rows = frappe.db.sql(
		"""
		select name, extraction_evidence
		from `tabAI Document`
		where extraction_evidence is not null
		  and extraction_evidence not in ('', '{}')
		  and extraction_evidence not like '%"versions"%'
		limit 5000
		""",
		as_dict=True,
	)
	for row in rows:
		try:
			evidence = json.loads(row.extraction_evidence or "{}")
		except (ValueError, TypeError):
			continue
		if not isinstance(evidence, dict) or evidence.get("versions"):
			continue
		evidence["versions"] = {"app": "pre-0.0.2", "reader": "unknown"}
		evidence.setdefault("extracted_on", None)
		frappe.db.set_value(
			"AI Document",
			row.name,
			"extraction_evidence",
			json.dumps(evidence, default=str, indent=2),
			update_modified=False,
		)
