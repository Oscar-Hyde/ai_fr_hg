# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Event-driven automation.

`AI Automation Rule` records bind Frappe document events to AI actions. The
`*` doc_events hook routes every document event here, and this module decides -
cheaply, using a cached rule index - whether anything needs to run.
"""

import frappe
from frappe import _
from frappe.utils import cint, now_datetime

CACHE_KEY = "ai_fr_hg:automation_rules"


def get_rule_index() -> dict:
	"""Map of `doctype -> event -> [rule names]`, cached for cheap lookups."""
	cached = frappe.cache.get_value(CACHE_KEY)
	if cached is not None:
		return cached

	index: dict = {}
	for rule in frappe.get_all(
		"AI Automation Rule",
		filters={"enabled": 1},
		fields=["name", "document_type", "event"],
	):
		index.setdefault(rule.document_type, {}).setdefault(rule.event, []).append(rule.name)

	frappe.cache.set_value(CACHE_KEY, index, expires_in_sec=3600)
	return index


def clear_rule_cache() -> None:
	frappe.cache.delete_value(CACHE_KEY)


def handle_document_event(doc, method: str | None = None) -> None:
	"""Entry point wired to the `*` doc_events hook.

	Never throw during a Frappe document lifecycle. Returning to Desk
	triggers saves for Workspace, Onboarding, User and other core
	DocTypes; a stray exception here would brick Desk with a 500 and
	prevent the socket handshake from completing (xhr poll error).
	"""
	try:
		if not method:
			return
		doctype = getattr(doc, "doctype", None)
		if not doctype or doctype.startswith("AI "):
			return  # never let the platform trigger itself recursively
		flags = getattr(frappe, "flags", None) or {}
		# frappe.flags may be a dict-like object; handle both cases.
		try:
			in_install = bool(flags.in_install or flags.in_migrate or flags.in_patch)  # type: ignore[attr-defined]
		except Exception:
			in_install = False
		if in_install:
			return

		try:
			rules = get_rule_index().get(doctype, {}).get(method)
		except Exception:
			# Cache/DB not ready during migrate/restore — fail silently so
			# Desk boot is never blocked.
			return
		if not rules:
			return

		for rule_name in rules:
			try:
				trigger_rule(rule_name, doc)
			except Exception:
				frappe.log_error(
					title=f"AI Automation Rule failed: {rule_name}", message=frappe.get_traceback()
				)
	except Exception:
		# Absolute last resort: Desk must never see an exception from this hook.
		try:
			frappe.log_error(title="AI Automation hook failed", message=frappe.get_traceback())
		except Exception:
			pass


def trigger_rule(rule_name: str, doc) -> None:
	"""Evaluate a rule's condition and enqueue its action when it matches."""
	rule = frappe.get_cached_doc("AI Automation Rule", rule_name)
	if not rule.enabled:
		return

	if rule.condition and not evaluate_condition(rule.condition, doc):
		return

	frappe.enqueue(
		"ai_fr_hg.ai.automation.execute_rule",
		queue=rule.queue or "long",
		timeout=1800,
		job_id=f"ai_rule_{rule_name}_{doc.doctype}_{doc.name}",
		deduplicate=True,
		enqueue_after_commit=True,
		rule_name=rule_name,
		doctype=doc.doctype,
		docname=doc.name,
	)


def evaluate_condition(condition: str, doc) -> bool:
	"""Safely evaluate a rule condition against the triggering document."""
	from frappe.utils.safe_exec import get_safe_globals

	try:
		return bool(
			frappe.safe_eval(
				condition,
				get_safe_globals(),
				{
					"doc": doc.as_dict(),
					"frappe": frappe._dict(db=frappe._dict(get_value=frappe.db.get_value)),
				},
			)
		)
	except Exception as exc:
		frappe.log_error(
			title="AI Automation condition failed",
			message=f"Condition: {condition}\nError: {exc}",
		)
		return False


def execute_rule(rule_name: str, doctype: str, docname: str) -> dict:
	"""Run a rule's configured action against the triggering document."""
	from ai_fr_hg.ai.intelligence import classify, extract_data, summarize

	rule = frappe.get_cached_doc("AI Automation Rule", rule_name)
	doc = frappe.get_doc(doctype, docname)

	source_text = _get_source_text(rule, doc)
	result = None

	try:
		if rule.action_type == "Run Pipeline":
			from ai_fr_hg.ai.pipeline import run_pipeline

			run = run_pipeline(
				rule.pipeline,
				input_data={"content": source_text, "reference_name": docname},
				reference_doctype=doctype,
				reference_name=docname,
				enqueue_job=False,
			)
			result = {"pipeline_run": run.name, "status": run.status}

		elif rule.action_type == "Run Agent":
			from ai_fr_hg.ai.agent import run_agent_turn

			outcome = run_agent_turn(
				source_text,
				agent=rule.agent,
				save_messages=False,
				knowledge_bases=[rule.knowledge_base] if rule.knowledge_base else None,
			)
			result = outcome["answer"]

		elif rule.action_type == "Summarize":
			result = summarize(source_text, reference_doctype=doctype, reference_name=docname)

		elif rule.action_type == "Classify":
			import json

			categories = []
			if rule.prompt_template:
				template = frappe.get_cached_doc("AI Prompt Template", rule.prompt_template)
				try:
					categories = json.loads(template.json_schema or "{}").get("categories", [])
				except ValueError:
					categories = []
			if not categories:
				frappe.throw(_("Rule {0} has no categories configured.").format(rule_name))
			result = classify(
				source_text, categories=categories, reference_doctype=doctype, reference_name=docname
			)

		elif rule.action_type == "Extract Data":
			result = extract_data(
				source_text,
				schema=rule.extraction_schema,
				reference_doctype=doctype,
				reference_name=docname,
			)

		elif rule.action_type == "Translate":
			from ai_fr_hg.ai.translation import translate_text

			outcome = translate_text(
				source_text,
				rule.target_language,
				reference_doctype=doctype,
				reference_name=docname,
			)
			result = outcome.text

		elif rule.action_type == "Ingest Document":
			from ai_fr_hg.ai.ingestion import process_document

			ai_doc = frappe.new_doc("AI Document")
			ai_doc.update(
				{
					"title": f"{doctype} {docname}",
					"knowledge_base": rule.knowledge_base,
					"source_type": "DocType Record",
					"source_doctype": doctype,
					"source_name": docname,
					"status": "Queued",
				}
			)
			ai_doc.insert(ignore_permissions=True)
			result = process_document(ai_doc.name)

		_write_result(rule, doc, result)
		_record_success(rule_name)
		return {"rule": rule_name, "status": "Success"}

	except Exception as exc:
		_record_failure(rule_name, str(exc))
		raise


def _get_source_text(rule, doc) -> str:
	"""Text handed to the AI action: a named field, or the whole document."""
	if rule.source_field:
		return str(doc.get(rule.source_field) or "")

	meta = frappe.get_meta(doc.doctype)
	lines = []
	for field in meta.fields:
		if field.fieldtype in (
			"Section Break",
			"Column Break",
			"Tab Break",
			"Button",
			"HTML",
			"Table",
			"Table MultiSelect",
		):
			continue
		if value := doc.get(field.fieldname):
			lines.append(f"{field.label or field.fieldname}: {value}")
	return "\n".join(lines)


def _write_result(rule, doc, result) -> None:
	"""Write the AI result back onto the triggering document, when configured."""
	if not rule.target_field or result is None:
		return
	if not doc.meta.has_field(rule.target_field):
		frappe.log_error(
			title="AI Automation mapping failed",
			message=f"{doc.doctype} has no field {rule.target_field}.",
		)
		return

	value = result
	if isinstance(result, dict):
		value = result.get("category") or frappe.as_json(result)

	frappe.db.set_value(doc.doctype, doc.name, rule.target_field, value, update_modified=False)


def _record_success(rule_name: str) -> None:
	row = frappe.db.get_value("AI Automation Rule", rule_name, ["run_count"], as_dict=True)
	frappe.db.set_value(
		"AI Automation Rule",
		rule_name,
		{"run_count": cint(row.run_count) + 1, "last_run_on": now_datetime(), "last_error": None},
		update_modified=False,
	)


def _record_failure(rule_name: str, error: str) -> None:
	row = frappe.db.get_value("AI Automation Rule", rule_name, ["failure_count"], as_dict=True)
	frappe.db.set_value(
		"AI Automation Rule",
		rule_name,
		{
			"failure_count": cint(row.failure_count) + 1,
			"last_run_on": now_datetime(),
			"last_error": error[:1000],
		},
		update_modified=False,
	)
