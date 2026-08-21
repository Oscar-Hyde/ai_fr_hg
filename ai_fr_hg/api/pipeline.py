# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Thin public facade for pipeline triggers (PIPE-01)."""

from __future__ import annotations

import json

import frappe
from frappe import _

from ai_fr_hg.utils.api_validation import bounded_payload, idempotency_key as parse_idempotency_key
from ai_fr_hg.utils.api_validation import valid_identifier


@frappe.whitelist()
def trigger(pipeline: str, input_data: str | None = None, idempotency_key: str | None = None) -> dict:
	"""Start an API-triggered pipeline run under the caller's authority."""
	from ai_fr_hg.ai.pipeline import run_pipeline

	name = valid_identifier(pipeline, label="pipeline", required=True)
	key = parse_idempotency_key(idempotency_key)
	payload_text = bounded_payload(input_data or "{}", label="input_data", max_bytes=32_000)
	try:
		payload = json.loads(payload_text or "{}")
	except ValueError:
		frappe.throw(_("Pipeline input must be valid JSON."))
	if not isinstance(payload, dict):
		frappe.throw(_("Pipeline input must be a JSON object."))

	pipeline_doc = frappe.get_doc("AI Pipeline", name)
	pipeline_doc.check_permission("read")
	if pipeline_doc.trigger_type != "API":
		frappe.throw(_("Pipeline {0} is not configured for API triggers.").format(name))
	if not pipeline_doc.enabled:
		frappe.throw(_("Pipeline {0} is disabled.").format(name))

	run = run_pipeline(
		name,
		input_data=payload,
		trigger_source="API",
		idempotency_key=key,
	)
	return {"run": run.name, "status": run.status, "pipeline": name}
