# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Enable Arabic / English / Hebrew translation on an existing site.

A DocType `default` only applies to newly created records, so the Single that
already exists needs its translation defaults written explicitly. The built-in
translation tool is registered here too, so agents installed before this
release gain the capability without a reinstall.
"""

import frappe
from frappe.utils import cint

DEFAULTS = {
	"translation_enabled": 1,
	"default_target_language": "en",
	"translation_segment_characters": 1800,
	"translation_batch_segments": 6,
	"translation_quality_checks": 1,
	"translation_repair_pass": 1,
	"translation_memory_enabled": 1,
	"translation_back_translation_samples": 0,
	"translation_index_output": 0,
}


def execute():
	for doctype in (
		"AI Translation Term",
		"AI Translation Glossary",
		"AI Translation Segment",
		"AI Translation",
		"AI Platform Settings",
		"AI Execution Log",
		"AI Pipeline Step",
	):
		try:
			frappe.reload_doctype(doctype, force=True)
		except Exception:
			frappe.log_error(title=f"Translation patch: could not reload {doctype}")

	settings = frappe.get_single("AI Platform Settings")
	changed = False
	for field, value in DEFAULTS.items():
		current = settings.get(field)
		# Only fill genuinely unset values: an administrator who already tuned
		# a field on a pre-release site keeps their choice.
		if current in (None, "") or (field == "translation_segment_characters" and not cint(current)):
			frappe.db.set_single_value("AI Platform Settings", field, value)
			changed = True

	if changed:
		frappe.clear_cache(doctype="AI Platform Settings")

	try:
		from ai_fr_hg.install import create_builtin_tools

		create_builtin_tools()
	except Exception:
		frappe.log_error(title="Translation patch: could not register the translate_content tool")
