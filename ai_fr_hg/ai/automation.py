# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Event-driven automation.

`AI Automation Rule` records bind Frappe document events to AI actions. The
`*` doc_events hook routes every document event here, and this module decides -
cheaply, using a cached rule index - whether anything needs to run.

Canonical owner for AUTO-01 through AUTO-04: immutable event snapshots, source
field contracts, atomic counters, and revision-aware dedupe live here. Public
API and DocType controllers stay thin.
"""

from __future__ import annotations

import json

import frappe
from frappe import _
from frappe.utils import cint, now_datetime

from ai_fr_hg.ai.automation_utils import (
	event_revision_key,
	sanitize_snapshot,
	source_field_error,
)
from ai_fr_hg.ai.logging import write_audit_log
from ai_fr_hg.utils.authority import as_user, assert_valid_authority

CACHE_KEY = "ai_fr_hg:automation_rules"


def _source_field_messages() -> dict[str, str]:
	return {
		"child_table_path": _("Child-table paths are not a supported automation source."),
		"missing": _("The source field does not exist on {0}."),
		"disallowed_type": _("The source field {0} is not a readable scalar field."),
		"sensitive": _("The source field {0} is sensitive and cannot be used as automation input."),
	}


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
		try:
			in_install = bool(flags.in_install or flags.in_migrate or flags.in_patch)  # type: ignore[attr-defined]
		except Exception:
			in_install = False
		if in_install:
			return

		try:
			rules = get_rule_index().get(doctype, {}).get(method)
		except Exception:
			return
		if not rules:
			return

		for rule_name in rules:
			try:
				trigger_rule(rule_name, doc, method=method)
			except Exception:
				frappe.log_error(
					title=f"AI Automation Rule failed: {rule_name}", message=frappe.get_traceback()
				)
	except Exception:
		try:
			frappe.log_error(title="AI Automation hook failed", message=frappe.get_traceback())
		except Exception:
			pass


def trigger_rule(rule_name: str, doc, method: str | None = None, *, enqueue: bool = True) -> dict | None:
	"""Evaluate a rule's condition and enqueue its action when it matches."""
	rule = frappe.get_cached_doc("AI Automation Rule", rule_name)
	if not rule.enabled:
		return None

	event = method or rule.event
	if rule.condition and not evaluate_condition(rule.condition, doc):
		return None

	event_row = _register_event(rule, doc, event)
	if not event_row or event_row.get("skipped"):
		return event_row

	if not enqueue:
		return event_row
	coalesce = True if rule.get("coalesce_events") in (None, "") else bool(cint(rule.coalesce_events))
	job_id = (
		f"ai_rule_{rule_name}_{doc.doctype}_{doc.name}"
		if coalesce
		else f"ai_rule_{rule_name}_{event_row['event']}"
	)
	frappe.enqueue(
		"ai_fr_hg.ai.automation.execute_rule",
		queue=rule.queue or "long",
		timeout=1800,
		job_id=job_id,
		deduplicate=coalesce,
		enqueue_after_commit=True,
		rule_name=rule_name,
		doctype=doc.doctype,
		docname=doc.name,
		event_name=event_row["event"],
	)
	return event_row


def evaluate_condition(condition: str, doc) -> bool:
	"""Safely evaluate a rule condition against the triggering document."""
	from frappe.utils.safe_exec import get_safe_globals

	payload = doc.as_dict() if hasattr(doc, "as_dict") else dict(doc)
	try:
		return bool(
			frappe.safe_eval(
				condition,
				get_safe_globals(),
				{
					"doc": payload,
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


def execute_rule(
	rule_name: str,
	doctype: str,
	docname: str,
	event_name: str | None = None,
) -> dict:
	"""Run a rule's configured action against a live document or an event snapshot."""
	from ai_fr_hg.ai.intelligence import classify, extract_data, summarize

	rule = frappe.get_cached_doc("AI Automation Rule", rule_name)
	event_doc = frappe.get_doc("AI Automation Event", event_name) if event_name else None
	event_kind = (event_doc.event if event_doc else rule.event) or "on_update"
	authority = (event_doc.requested_by if event_doc else None) or frappe.session.user

	if event_doc:
		claimed = _claim_event(event_doc.name)
		if not claimed:
			return {
				"rule": rule_name,
				"status": frappe.db.get_value("AI Automation Event", event_doc.name, "status"),
				"skipped": True,
			}

	try:
		authority = assert_valid_authority(authority)
	except Exception as exc:
		_record_failure(rule_name, str(exc))
		if event_doc:
			_finish_event(event_doc.name, "Failed", str(exc))
		raise

	with as_user(authority):
		doc = _resolve_document(rule, doctype, docname, event_doc, event_kind)
		source_text = _get_source_text(rule, doc)
		result = None
		try:
			if event_kind == "on_trash" and rule.target_field:
				raise frappe.ValidationError(
					_("Delete-event rules cannot write a target field on the deleted document.")
				)

			if rule.action_type == "Run Pipeline":
				from ai_fr_hg.ai.pipeline import run_pipeline

				run = run_pipeline(
					rule.pipeline,
					input_data={"content": source_text, "reference_name": docname},
					reference_doctype=doctype,
					reference_name=docname,
					enqueue_job=False,
					trigger_source="Automation",
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
					source_text,
					categories=categories,
					reference_doctype=doctype,
					reference_name=docname,
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

			if event_kind != "on_trash":
				_write_result(rule, doc, result)
			_record_success(rule_name)
			if event_doc:
				_finish_event(event_doc.name, "Success")
			write_audit_log(
				action="Automation Rule Success",
				category="Execution",
				message=_("Automation rule {0} ran for {1} {2}.").format(rule_name, doctype, docname),
				details={"event": event_kind, "action": rule.action_type},
				reference_doctype="AI Automation Rule",
				reference_name=rule_name,
			)
			return {"rule": rule_name, "status": "Success", "event": event_doc.name if event_doc else None}

		except Exception as exc:
			_record_failure(rule_name, str(exc))
			if event_doc:
				_finish_event(event_doc.name, "Failed", str(exc))
			write_audit_log(
				action="Automation Rule Failed",
				category="Execution",
				severity="Warning",
				message=_("Automation rule {0} failed for {1} {2}.").format(rule_name, doctype, docname),
				details={"event": event_kind, "error": str(exc)[:1000]},
				reference_doctype="AI Automation Rule",
				reference_name=rule_name,
			)
			raise


def validate_source_field(document_type: str | None, source_field: str | None) -> None:
	"""Reject missing, child-table, sensitive, and non-scalar source fields."""
	if not source_field or not document_type:
		return
	meta = frappe.get_meta(document_type)
	df = meta.get_field(source_field) if meta.has_field(source_field) else None
	code = source_field_error(
		source_field,
		getattr(df, "fieldtype", None),
		exists=bool(df),
	)
	if not code:
		return
	template = _source_field_messages()[code]
	if code == "missing":
		frappe.throw(template.format(document_type))
	frappe.throw(template.format(source_field))


def validate_target_field(document_type: str | None, target_field: str | None, event: str | None) -> None:
	if event == "on_trash" and target_field:
		frappe.throw(_("Delete-event rules cannot write a target field."))
	if not target_field or not document_type:
		return
	if not frappe.get_meta(document_type).has_field(target_field):
		frappe.throw(_("{0} has no field named {1}.").format(document_type, target_field))
	df = frappe.get_meta(document_type).get_field(target_field)
	if getattr(df, "fieldtype", None) == "Password" or source_field_error(
		target_field, getattr(df, "fieldtype", None), exists=True
	) in {"sensitive", "disallowed_type", "child_table_path"}:
		frappe.throw(_("The target field {0} cannot be written by automation.").format(target_field))


def _register_event(rule, doc, event: str) -> dict | None:
	"""Persist one automation event, applying revision dedupe and coalescing."""
	modified = str(getattr(doc, "modified", None) or now_datetime())
	revision = event_revision_key(rule.name, doc.doctype, doc.name, modified)
	existing = frappe.db.get_value(
		"AI Automation Event",
		{"revision_key": revision},
		["name", "status"],
		as_dict=True,
	)
	if existing and existing.status in {"Queued", "Running", "Success"}:
		return {
			"event": existing.name,
			"status": existing.status,
			"skipped": True,
			"reason": "duplicate_revision",
		}

	coalesce = True if rule.get("coalesce_events") in (None, "") else bool(cint(rule.coalesce_events))
	if coalesce:
		active = frappe.db.get_value(
			"AI Automation Event",
			{
				"rule": rule.name,
				"source_doctype": doc.doctype,
				"source_name": doc.name,
				"status": ["in", ["Queued", "Running"]],
			},
			"name",
		)
		if active:
			coalesced = _insert_event(rule, doc, event, revision, status="Coalesced")
			return {"event": coalesced, "status": "Coalesced", "skipped": True, "reason": "coalesced"}

	name = _insert_event(rule, doc, event, revision, status="Queued")
	return {"event": name, "status": "Queued"}


def _insert_event(rule, doc, event: str, revision: str, *, status: str) -> str:
	snapshot = sanitize_snapshot(doc.as_dict() if hasattr(doc, "as_dict") else dict(doc))
	row = frappe.new_doc("AI Automation Event")
	row.update(
		{
			"rule": rule.name,
			"event": event,
			"status": status,
			"requested_by": frappe.session.user,
			"source_doctype": doc.doctype,
			"source_name": doc.name,
			"document_modified": getattr(doc, "modified", None) or now_datetime(),
			"revision_key": f"{revision}:{status}:{frappe.generate_hash(length=8)}"
			if status == "Coalesced"
			else revision,
			"snapshot": frappe.as_json(snapshot),
		}
	)
	row.flags.ignore_permissions = True
	row.insert(ignore_permissions=True)
	return row.name


def _claim_event(event_name: str) -> bool:
	status = frappe.db.get_value("AI Automation Event", event_name, "status", for_update=True)
	if status != "Queued":
		return False
	frappe.db.set_value(
		"AI Automation Event",
		event_name,
		{"status": "Running", "started_on": now_datetime()},
		update_modified=False,
	)
	return True


def _finish_event(event_name: str, status: str, error: str | None = None) -> None:
	values = {"status": status, "finished_on": now_datetime()}
	if error:
		values["error_message"] = error[:1000]
	frappe.db.set_value("AI Automation Event", event_name, values, update_modified=False)


def _resolve_document(rule, doctype: str, docname: str, event_doc, event_kind: str):
	if event_kind == "on_trash":
		if not event_doc or not event_doc.snapshot:
			frappe.throw(_("Delete-event automation requires an immutable snapshot."))
		return SnapshotDocument(json.loads(event_doc.snapshot))
	if frappe.db.exists(doctype, docname):
		doc = frappe.get_doc(doctype, docname)
		if rule.source_field:
			validate_source_field(doctype, rule.source_field)
		return doc
	if event_doc and event_doc.snapshot:
		return SnapshotDocument(json.loads(event_doc.snapshot))
	frappe.throw(_("{0} {1} no longer exists and no snapshot was stored.").format(doctype, docname))


class SnapshotDocument:
	"""Read-only stand-in for a deleted (or otherwise unavailable) document."""

	def __init__(self, payload: dict):
		self._data = payload or {}
		self.doctype = self._data.get("doctype")
		self.name = self._data.get("name")
		for key, value in self._data.items():
			if not hasattr(self, key):
				setattr(self, key, value)

	def get(self, key, default=None):
		return self._data.get(key, default)

	def as_dict(self):
		return dict(self._data)

	@property
	def meta(self):
		return (
			frappe.get_meta(self.doctype)
			if self.doctype
			else frappe._dict(fields=[], has_field=lambda *_: False)
		)


def _get_source_text(rule, doc) -> str:
	"""Text handed to the AI action: a named field, or the whole document."""
	if rule.source_field:
		validate_source_field(doc.doctype, rule.source_field)
		return str(doc.get(rule.source_field) or "")

	meta = frappe.get_meta(doc.doctype) if getattr(doc, "doctype", None) else None
	if not meta:
		return str(doc.get("name") or "")
	lines = []
	for field in meta.fields:
		code = source_field_error(field.fieldname, field.fieldtype, exists=True)
		if code:
			continue
		if value := doc.get(field.fieldname):
			lines.append(f"{field.label or field.fieldname}: {value}")
	return "\n".join(lines)


def _write_result(rule, doc, result) -> None:
	"""Write the AI result back onto the triggering document, when configured."""
	if not rule.target_field or result is None:
		return
	if isinstance(doc, SnapshotDocument):
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
	frappe.db.sql(
		"""
		update `tabAI Automation Rule`
		set run_count = coalesce(run_count, 0) + 1,
			last_run_on = %s,
			last_error = null
		where name = %s
		""",
		(now_datetime(), rule_name),
	)


def _record_failure(rule_name: str, error: str) -> None:
	frappe.db.sql(
		"""
		update `tabAI Automation Rule`
		set failure_count = coalesce(failure_count, 0) + 1,
			last_run_on = %s,
			last_error = %s
		where name = %s
		""",
		(now_datetime(), (error or "")[:1000], rule_name),
	)
