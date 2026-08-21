# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""AI Task execution and server-authorized state machine.

Canonical owner for TASK-01 through TASK-03. The DocType controller is a thin
Frappe lifecycle wrapper; every status change and every task-type contract is
enforced here independently of the form.
"""

from __future__ import annotations

import json

import frappe
from frappe import _
from frappe.utils import cint, now_datetime

from ai_fr_hg.ai.exceptions import TaskError, TaskIllegalTransition
from ai_fr_hg.ai.logging import write_audit_log
from ai_fr_hg.utils.authority import as_user, assert_valid_authority

TASK_TYPES = ("Question", "Summarize", "Classify", "Extract Data", "Compare", "Pipeline", "Custom")

PRIORITY_ORDER = {"Urgent": 0, "High": 1, "Medium": 2, "Low": 3}

LEGAL_TRANSITIONS = {
	"Open": {"Pending Approval", "In Progress", "Cancelled"},
	"Pending Approval": {"Approved", "Rejected", "Cancelled"},
	"Approved": {"In Progress", "Cancelled"},
	"In Progress": {"Completed", "Failed", "Cancelled"},
	"Failed": {"Open", "Cancelled"},
	"Rejected": set(),
	"Completed": set(),
	"Cancelled": set(),
}

_TASK_METHOD_MARKER = "_ai_task_method"


def task_method(method):
	"""Mark an app-owned callable as safe for Custom AI Tasks."""
	setattr(method, _TASK_METHOD_MARKER, True)
	return method


def _is_manager(user: str | None = None) -> bool:
	user = user or frappe.session.user
	if user == "Administrator":
		return True
	return bool(set(frappe.get_roles(user)).intersection({"AI Manager", "System Manager"}))


def _requester(doc) -> str:
	return doc.requested_by or doc.owner


def assert_transition(current: str, target: str) -> None:
	allowed = LEGAL_TRANSITIONS.get(current) or set()
	if target not in allowed:
		raise TaskIllegalTransition(_("Cannot move an AI Task from {0} to {1}.").format(current, target))


def validate_task_contract(doc) -> None:
	"""Per-type payload contract. Called from the controller on validate."""
	if doc.task_type not in TASK_TYPES:
		raise TaskError(_("Unsupported task type {0}.").format(doc.task_type))
	payload = _payload(doc)
	if doc.task_type == "Pipeline" and not doc.pipeline:
		raise TaskError(_("A pipeline is required for a Pipeline task."))
	if doc.task_type == "Custom":
		if not doc.custom_method:
			raise TaskError(_("A custom method is required for a Custom task."))
		resolve_task_method(doc.custom_method)
	if doc.task_type == "Compare":
		if not payload.get("document_a") or not payload.get("document_b"):
			raise TaskError(_("A Compare task needs document_a and document_b in Input Data."))
	if doc.task_type == "Classify":
		categories = payload.get("categories") or []
		if not isinstance(categories, list) or not categories:
			raise TaskError(_("A Classify task needs a categories list in Input Data."))
	if doc.task_type == "Extract Data":
		if not (doc.extraction_schema or payload.get("schema")):
			raise TaskError(_("An Extract Data task needs an extraction schema."))


def resolve_task_method(dotted_path: str):
	registered = _registered_task_methods()
	app_owned = dotted_path.startswith("ai_fr_hg.")
	if dotted_path not in registered and not app_owned:
		raise TaskError(_("Custom task method {0} is not registered in ai_task_methods.").format(dotted_path))
	try:
		method = frappe.get_attr(dotted_path)
	except Exception as exc:
		raise TaskError(_("Could not load custom task method {0}.").format(dotted_path)) from exc
	if not callable(method):
		raise TaskError(_("Custom task method {0} is not callable.").format(dotted_path))
	if dotted_path not in registered and not getattr(method, _TASK_METHOD_MARKER, False):
		raise TaskError(
			_("Custom task method {0} has not been marked with @task_method.").format(dotted_path)
		)
	return method


def _registered_task_methods() -> set[str]:
	configured = frappe.get_hooks("ai_task_methods") or {}
	values = configured.values() if isinstance(configured, dict) else configured
	registered: set[str] = set()
	for value in values:
		if isinstance(value, (list, tuple, set)):
			registered.update(str(item) for item in value if item)
		elif value:
			registered.add(str(value))
	return registered


def _payload(doc) -> dict:
	if not doc.input_data:
		return {}
	try:
		payload = json.loads(doc.input_data)
	except ValueError:
		raise TaskError(_("Input Data must be valid JSON."))
	if not isinstance(payload, dict):
		raise TaskError(_("Input Data must be a JSON object."))
	return payload


def _lock(name: str) -> str:
	status = frappe.db.get_value("AI Task", name, "status", for_update=True)
	if not status:
		raise TaskError(_("AI Task {0} does not exist.").format(name))
	return status


def _assert_actor(doc, *, manager_only: bool = False, allow_requester: bool = True) -> None:
	user = frappe.session.user
	if user == "Guest":
		raise frappe.PermissionError(_("Sign in to manage AI Tasks."))
	if manager_only and not _is_manager(user):
		raise frappe.PermissionError(_("Only an AI Manager can perform this action."))
	if _is_manager(user):
		return
	if allow_requester and user in {_requester(doc), doc.owner}:
		return
	raise frappe.PermissionError(_("You cannot perform this action on AI Task {0}.").format(doc.name))


def submit_task(name: str) -> dict:
	"""Open → Pending Approval."""
	doc = frappe.get_doc("AI Task", name)
	_assert_actor(doc)
	status = _lock(name)
	assert_transition(status, "Pending Approval")
	_set_status(name, "Pending Approval")
	_audit(name, "AI Task Submitted", f"{frappe.session.user} submitted {name} for approval.")
	return {"task": name, "status": "Pending Approval"}


def approve_task(name: str) -> dict:
	"""Pending Approval → Approved → enqueue In Progress. Manager, not requester."""
	doc = frappe.get_doc("AI Task", name)
	_assert_actor(doc, manager_only=True, allow_requester=False)
	if frappe.session.user == _requester(doc) and frappe.session.user != "Administrator":
		raise frappe.PermissionError(_("You cannot approve your own AI Task."))
	status = _lock(name)
	assert_transition(status, "Approved")
	_set_status(name, "Approved")
	_audit(name, "AI Task Approved", f"{frappe.session.user} approved {name}.")
	return enqueue_task(name)


def reject_task(name: str) -> dict:
	doc = frappe.get_doc("AI Task", name)
	_assert_actor(doc, manager_only=True)
	status = _lock(name)
	assert_transition(status, "Rejected")
	_set_status(name, "Rejected")
	_audit(name, "AI Task Rejected", f"{frappe.session.user} rejected {name}.", severity="Warning")
	return {"task": name, "status": "Rejected"}


def cancel_task(name: str) -> dict:
	doc = frappe.get_doc("AI Task", name)
	_assert_actor(doc)
	status = _lock(name)
	assert_transition(status, "Cancelled")
	_set_status(name, "Cancelled")
	_audit(name, "AI Task Cancelled", f"{frappe.session.user} cancelled {name}.", severity="Warning")
	return {"task": name, "status": "Cancelled"}


def retry_task(name: str) -> dict:
	doc = frappe.get_doc("AI Task", name)
	_assert_actor(doc)
	status = _lock(name)
	assert_transition(status, "Open")
	_set_status(name, "Open", extra={"error_message": None})
	_audit(name, "AI Task Retried", f"{frappe.session.user} reopened {name}.")
	return {"task": name, "status": "Open"}


def run_now(name: str) -> dict:
	"""Start execution from Open or Approved when the caller is authorized."""
	doc = frappe.get_doc("AI Task", name)
	_assert_actor(doc)
	status = _lock(name)
	if status == "Open":
		if cint(doc.requires_approval) and not _is_manager():
			raise frappe.PermissionError(_("This task requires manager approval before it can run."))
		assert_transition(status, "In Progress")
	elif status == "Approved":
		assert_transition(status, "In Progress")
	else:
		raise TaskIllegalTransition(_("AI Task {0} cannot be run from {1}.").format(name, status))
	return enqueue_task(name)


def enqueue_task(name: str) -> dict:
	status = _lock(name)
	if status not in {"Open", "Approved", "In Progress"}:
		raise TaskIllegalTransition(_("AI Task {0} cannot be queued from {1}.").format(name, status))
	if status != "In Progress":
		_set_status(name, "In Progress")
	frappe.enqueue(
		"ai_fr_hg.ai.tasks.execute_task",
		queue="long",
		timeout=1800,
		job_id=f"ai_task_{name}",
		deduplicate=True,
		enqueue_after_commit=True,
		task=name,
	)
	return {"task": name, "status": "In Progress"}


def execute_task(task: str) -> dict:
	"""Run the type-specific contract under the durable requester."""
	from ai_fr_hg.ai.agent import run_agent_turn
	from ai_fr_hg.ai.intelligence import classify, compare_documents, extract_data, summarize

	doc = frappe.get_doc("AI Task", task)
	authority = _requester(doc)
	try:
		authority = assert_valid_authority(authority)
	except Exception as exc:
		_set_status(task, "Failed", extra={"error_message": str(exc)[:1000]})
		return {"task": task, "status": "Failed", "error": str(exc)}

	status = _lock(task)
	if status == "Cancelled":
		return {"task": task, "status": "Cancelled", "skipped": True}
	if status not in {"In Progress", "Approved", "Open"}:
		return {"task": task, "status": status, "skipped": True}
	if status != "In Progress":
		_set_status(task, "In Progress")

	payload = {}
	if doc.input_data:
		try:
			payload = json.loads(doc.input_data)
		except ValueError:
			payload = {}
		if not isinstance(payload, dict):
			payload = {}

	try:
		with as_user(authority):
			result_text = None
			result_data = None
			if doc.task_type == "Pipeline":
				from ai_fr_hg.ai.pipeline import run_pipeline

				run = run_pipeline(
					doc.pipeline,
					input_data={"content": doc.instruction, **payload},
					reference_doctype="AI Task",
					reference_name=task,
					enqueue_job=False,
					trigger_source="Automation",
				)
				frappe.db.set_value("AI Task", task, "pipeline_run", run.name, update_modified=False)
				result_data = json.loads(run.output_data or "{}")
				result_text = f"Pipeline run {run.name} finished with status {run.status}."
				if run.status not in {"Completed"}:
					raise TaskError(run.error_message or result_text)
			elif doc.task_type == "Summarize":
				result_text = summarize(
					payload.get("text") or doc.instruction,
					model=doc.model,
					reference_doctype="AI Task",
					reference_name=task,
				)
			elif doc.task_type == "Classify":
				categories = payload.get("categories") or []
				if not categories:
					raise TaskError(_("A Classify task needs a categories list in Input Data."))
				result_data = classify(
					payload.get("text") or doc.instruction,
					categories=categories,
					model=doc.model,
					reference_doctype="AI Task",
					reference_name=task,
				)
				result_text = result_data.get("category")
			elif doc.task_type == "Extract Data":
				schema = doc.extraction_schema or payload.get("schema")
				if not schema:
					raise TaskError(_("An Extract Data task needs an extraction schema."))
				result_data = extract_data(
					payload.get("text") or doc.instruction,
					schema=schema,
					model=doc.model,
					reference_doctype="AI Task",
					reference_name=task,
				)
				result_text = frappe.as_json(result_data)
			elif doc.task_type == "Compare":
				left = payload.get("document_a")
				right = payload.get("document_b")
				if not left or not right:
					raise TaskError(_("A Compare task needs document_a and document_b."))
				result_data = compare_documents(
					left, right, model=doc.model, instructions=doc.instruction or ""
				)
				result_text = result_data.get("comparison") or frappe.as_json(result_data)
			elif doc.task_type == "Custom":
				method = resolve_task_method(doc.custom_method)
				outcome = method(task=doc, payload=payload)
				if isinstance(outcome, dict):
					result_data = outcome
					result_text = outcome.get("result") or frappe.as_json(outcome)
				else:
					result_text = str(outcome)
			else:
				outcome = run_agent_turn(
					doc.instruction,
					agent=doc.agent,
					model=doc.model,
					knowledge_bases=[doc.knowledge_base] if doc.knowledge_base else None,
					save_messages=False,
					include_history=False,
				)
				result_text = outcome["answer"]
				result_data = {"citations": outcome["citations"]}

		log_name = frappe.db.get_value(
			"AI Execution Log",
			{"reference_doctype": "AI Task", "reference_name": task},
			"name",
			order_by="creation desc",
		)
		_set_status(
			task,
			"Completed",
			extra={
				"result": result_text,
				"result_data": frappe.as_json(result_data) if result_data else None,
				"completed_on": now_datetime(),
				"error_message": None,
				"execution_log": log_name,
			},
		)
		_audit(task, "AI Task Completed", f"AI Task {task} completed.")
		return {"task": task, "status": "Completed"}
	except Exception as exc:
		_set_status(task, "Failed", extra={"error_message": str(exc)[:1000]})
		frappe.log_error(title=f"AI Task failed: {task}", message=frappe.get_traceback())
		_audit(task, "AI Task Failed", str(exc)[:1000], severity="Critical")
		return {"task": task, "status": "Failed", "error": str(exc)}


def claim_due_tasks(limit: int = 20) -> list[str]:
	"""Enqueue Open tasks whose due_date has passed, highest priority first."""
	now = now_datetime()
	claimed: list[str] = []
	rows = frappe.get_all(
		"AI Task",
		filters={"status": "Open", "due_date": ["<=", now]},
		fields=["name", "priority", "due_date", "requires_approval"],
		limit=max(1, cint(limit)),
	)
	rows.sort(key=lambda row: (PRIORITY_ORDER.get(row.priority or "Medium", 9), row.due_date or now))
	for row in rows:
		status = frappe.db.get_value("AI Task", row.name, "status", for_update=True)
		if status != "Open":
			continue
		if cint(row.requires_approval):
			_set_status(row.name, "Pending Approval")
			claimed.append(row.name)
			continue
		enqueue_task(row.name)
		claimed.append(row.name)
	return claimed


def task_actions_for(
	status: str, *, is_manager: bool, is_requester: bool, requires_approval: bool
) -> list[str]:
	"""Frontend contract: which buttons the current actor may see."""
	actions: list[str] = []
	if status == "Open":
		if is_requester or is_manager:
			actions.append("submit")
			if is_manager or not requires_approval:
				actions.append("run")
			actions.append("cancel")
	elif status == "Pending Approval":
		if is_manager and not (is_requester and not is_manager):
			actions.append("approve")
			actions.append("reject")
		if is_manager or is_requester:
			actions.append("cancel")
		if is_manager and is_requester:
			# Self-approve is forbidden; still allow reject/cancel.
			actions = [item for item in actions if item != "approve"]
	elif status == "Approved":
		if is_manager or is_requester:
			actions.append("run")
			actions.append("cancel")
	elif status == "In Progress":
		if is_manager or is_requester:
			actions.append("cancel")
	elif status == "Failed":
		if is_manager or is_requester:
			actions.append("retry")
	return actions


def _set_status(name: str, status: str, extra: dict | None = None) -> None:
	values = {"status": status}
	if extra:
		values.update(extra)
	frappe.db.set_value("AI Task", name, values, update_modified=False)
	frappe.clear_document_cache("AI Task", name)


def _audit(name: str, action: str, message: str, severity: str = "Info") -> None:
	write_audit_log(
		action=action,
		category="Execution",
		severity=severity,
		message=message,
		reference_doctype="AI Task",
		reference_name=name,
	)
