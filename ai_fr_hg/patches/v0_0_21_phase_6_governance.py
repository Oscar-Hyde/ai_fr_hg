# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Phase 6 governance/report migration.

LEARN-01: the three learning reports shipped as **Query Reports** carrying
static SQL. Frappe runs that SQL and never calls the module's ``execute()``,
so every filter in the sidebar was inert. The definitions are now Script
Reports; this patch makes an already-installed site pick that up and clears
the stale query text, without touching any learning data.

PROV-02: `AI Model` capability flags were cosmetic — nothing read them, and
model discovery never populated them. Now that the engine enforces them, an
existing site whose models all carry ``0`` would suddenly lose tool calling,
JSON mode, and streaming. This patch backfills each model with exactly the
capability its provider adapter can transport, which is precisely the
behaviour that applied before enforcement. Operators can then switch a flag
*off* to impose a restriction; nothing is switched off by the migration.

Idempotent: reloading a document definition, clearing an already-empty column,
and writing a capability value that is already correct are all safe to repeat.
"""

from __future__ import annotations

import frappe
from frappe.utils import cint

REPORTS = (
	("learning_activity", "Learning Activity"),
	("memory_usage", "Memory Usage"),
	("skill_summary", "Skill Summary"),
)

CAPABILITY_FIELDS = (
	"supports_streaming",
	"supports_tools",
	"supports_json_mode",
	"supports_vision",
)


def execute():
	_convert_learning_reports()
	_backfill_model_capabilities()


def _convert_learning_reports():
	for module_path, report_name in REPORTS:
		try:
			frappe.reload_doc("ai_learning", "report", module_path, force=True)
		except Exception:
			# A site that never installed the report has nothing to migrate;
			# the standard sync will create it from the JSON definition.
			frappe.log_error(
				title="Phase 6: report reload skipped",
				message=f"Could not reload report {report_name}: {frappe.get_traceback()}",
			)
			continue

		if not frappe.db.exists("Report", report_name):
			continue

		# Belt and braces: if an older row survived the reload with its
		# Query Report shape, force the Script Report contract explicitly.
		frappe.db.set_value(
			"Report",
			report_name,
			{"report_type": "Script Report", "is_standard": "Yes", "query": ""},
			update_modified=False,
		)

	frappe.clear_cache()


def _backfill_model_capabilities():
	if not all(frappe.db.has_column("AI Model", field) for field in CAPABILITY_FIELDS):
		return

	from ai_fr_hg.ai import capability
	from ai_fr_hg.ai.providers import get_provider_class_for

	adapters: dict[str, object] = {}
	rows = frappe.get_all(
		"AI Model",
		fields=["name", "provider", "model_name", "model_type", *CAPABILITY_FIELDS],
	)

	for row in rows:
		if not row.provider:
			continue
		if row.provider not in adapters:
			try:
				adapters[row.provider] = get_provider_class_for(row.provider)
			except Exception:
				# An unresolvable adapter (deleted Custom path, removed app)
				# must not stop the migration for every other model.
				adapters[row.provider] = None
		adapter = adapters[row.provider]
		if adapter is None:
			continue

		defaults = capability.discovery_capability_defaults(
			adapter, row.model_type or "Chat", row.model_name or ""
		)
		# Only ever raise a flag the adapter supports; never lower one an
		# operator has already set deliberately.
		changes = {field: 1 for field, value in defaults.items() if value and not cint(row.get(field))}
		if changes:
			frappe.db.set_value("AI Model", row.name, changes, update_modified=False)

	frappe.clear_cache(doctype="AI Model")
