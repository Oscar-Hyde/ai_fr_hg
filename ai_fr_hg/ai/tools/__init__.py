# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Tool registry and safe execution.

A tool lets the model act on the Frappe site. Every invocation is permission
checked against the *calling user* (never elevated), recorded as an
`AI Tool Invocation`, and bounded by the tool's runtime limit. Write tools can
require explicit human approval before they run.
"""

import json
import signal
import threading
import time
from contextlib import contextmanager
from uuid import uuid4

import frappe
from frappe import _
from frappe.utils import cint, now_datetime

from ai_fr_hg.ai.exceptions import ToolExecutionError
from ai_fr_hg.ai.governance import check_capability
from ai_fr_hg.ai.logging import write_audit_log

TYPE_MAP = {
	"String": "string",
	"Number": "number",
	"Integer": "integer",
	"Boolean": "boolean",
	"Array": "array",
	"Object": "object",
}


def build_tool_schema(tool_doc) -> dict:
	"""Render an `AI Tool` into a JSON-Schema function definition."""
	properties: dict = {}
	required: list[str] = []

	for row in tool_doc.get("parameters") or []:
		schema: dict = {"type": TYPE_MAP.get(row.parameter_type, "string")}
		if row.description:
			schema["description"] = row.description
		if row.enum_values:
			values = [v.strip() for v in row.enum_values.replace(",", "\n").splitlines() if v.strip()]
			if values:
				schema["enum"] = values
		if schema["type"] == "array":
			schema["items"] = {"type": "string"}
		properties[row.parameter] = schema
		if row.required:
			required.append(row.parameter)

	return {
		"name": tool_doc.name,
		"description": tool_doc.description,
		"parameters": {
			"type": "object",
			"properties": properties,
			"required": required,
		},
	}


def get_agent_tool_schemas(agent_doc) -> list[dict]:
	"""Tool schemas the agent may use, filtered by the caller's roles."""
	schemas = []
	roles = set(frappe.get_roles())

	for row in agent_doc.get("tools") or []:
		if not row.enabled:
			continue
		tool_doc = frappe.get_cached_doc("AI Tool", row.tool)
		if not tool_doc.enabled:
			continue

		allowed = [r.role for r in tool_doc.get("allowed_roles") or []]
		if allowed and frappe.session.user != "Administrator" and not roles.intersection(allowed):
			continue

		schemas.append(build_tool_schema(tool_doc))
	return schemas


def get_builtin_handlers() -> dict:
	"""Built-in handlers merged with those contributed by installed apps."""
	from ai_fr_hg.ai.tools import builtin

	handlers = {
		"search_knowledge_base": builtin.search_knowledge_base,
		"get_document": builtin.get_document,
		"list_documents": builtin.list_documents,
		"count_documents": builtin.count_documents,
		"run_report": builtin.run_report,
		"get_document_text": builtin.get_document_text,
		"translate_content": builtin.translate_content,
		"current_datetime": builtin.current_datetime,
	}
	for name, dotted_path in (frappe.get_hooks("ai_tools") or {}).items():
		if isinstance(dotted_path, list):
			dotted_path = dotted_path[-1]
		try:
			handlers[name] = frappe.get_attr(dotted_path)
		except Exception:
			frappe.log_error(
				title="AI tool registry", message=f"Could not load tool handler {dotted_path} for {name}"
			)
	return handlers


def execute_tool(
	tool: str,
	arguments: dict | str | None = None,
	conversation: str | None = None,
	agent: str | None = None,
	pipeline_run: str | None = None,
) -> dict:
	"""Run a tool under the current user's authority and persist one invocation."""
	arguments = _normalise_arguments(arguments)
	if pipeline_run:
		context = frappe.db.get_value(
			"AI Pipeline Run", pipeline_run, ["status", "triggered_by"], as_dict=True
		)
		if not context or context.status != "Running" or context.triggered_by != frappe.session.user:
			return {
				"status": "Failed",
				"error": _("Invalid or inactive Pipeline Run execution context."),
				"result": None,
			}
	if not frappe.db.exists("AI Tool", tool):
		return {"status": "Failed", "error": f"Unknown tool: {tool}", "result": None}

	tool_doc = frappe.get_cached_doc("AI Tool", tool)
	invocation = _create_invocation(tool_doc, arguments, conversation, agent, pipeline_run)
	return _execute_invocation(
		tool_doc,
		arguments,
		invocation=invocation,
		conversation=conversation,
		agent=agent,
		approval_granted=False,
	)


def _normalise_arguments(arguments: dict | str | None) -> dict:
	if isinstance(arguments, str):
		try:
			arguments = json.loads(arguments)
		except (TypeError, ValueError):
			return {"_raw": arguments}
	if not isinstance(arguments, dict):
		return {} if arguments is None else {"_raw": arguments}
	return arguments


def _execute_invocation(
	tool_doc,
	arguments: dict,
	*,
	invocation: str,
	conversation: str | None,
	agent: str | None,
	approval_granted: bool,
) -> dict:
	"""Validate and dispatch an already-persisted invocation exactly once."""
	started = time.monotonic()
	save_point = None
	try:
		if not tool_doc.enabled:
			raise ToolExecutionError(_("Tool {0} is disabled.").format(tool_doc.name))

		check_capability("tools")
		_check_tool_access(tool_doc)
		_check_execution_context(tool_doc, conversation=conversation, agent=agent)
		_validate_arguments(tool_doc, arguments)

		if _needs_approval(tool_doc) and not approval_granted:
			_mark_pending(invocation)
			write_audit_log(
				action=f"Tool Approval Requested: {tool_doc.name}",
				category="Security",
				severity="Warning",
				message=f"{frappe.session.user} requested approval for tool {tool_doc.name}.",
				details={"arguments": arguments, "agent": agent},
				reference_doctype="AI Tool Invocation",
				reference_name=invocation,
				raise_on_error=True,
			)
			return {
				"status": "Pending Approval",
				"result": {
					"message": _(
						"This action needs approval. An AI Manager must approve invocation {0}."
					).format(invocation),
					"invocation": invocation,
				},
				"error": None,
				"invocation": invocation,
			}

		# Roll back database mutations made by a failed handler while preserving
		# the invocation record and its terminal failure state.
		save_point = f"ai_tool_{uuid4().hex}"
		frappe.db.savepoint(save_point)
		with _runtime_limit(cint(tool_doc.max_runtime_seconds)):
			result = _dispatch(
				tool_doc,
				arguments,
				pipeline_run=frappe.db.get_value("AI Tool Invocation", invocation, "pipeline_run"),
			)

		# The action, terminal state, and mandatory audit record share one
		# savepoint. An audit failure must not commit an unaudited database act.
		_update_invocation(invocation, status="Success", started=started, result=result)
		write_audit_log(
			action=f"Tool Executed: {tool_doc.name}",
			category="Execution",
			message=f"Tool {tool_doc.name} executed successfully.",
			details={"arguments": arguments, "agent": agent},
			reference_doctype="AI Tool Invocation",
			reference_name=invocation,
			raise_on_error=True,
		)
		frappe.db.release_savepoint(save_point)
		save_point = None
		return {"status": "Success", "result": result, "error": None, "invocation": invocation}

	except Exception as exc:
		if save_point:
			try:
				frappe.db.rollback(save_point=save_point)
			except Exception:
				frappe.log_error(title="AI tool rollback failed", message=frappe.get_traceback())
		error = str(exc) or exc.__class__.__name__
		_update_invocation(invocation, status="Failed", started=started, error=error)
		write_audit_log(
			action=f"Tool Failed: {tool_doc.name}",
			category="Execution",
			severity="Warning",
			message=error,
			details={"arguments": arguments, "agent": agent},
			reference_doctype="AI Tool Invocation",
			reference_name=invocation,
			raise_on_error=True,
		)
		return {"status": "Failed", "result": None, "error": error, "invocation": invocation}


def _needs_approval(tool_doc) -> bool:
	settings = frappe.get_cached_doc("AI Platform Settings")
	return bool(
		tool_doc.requires_approval or (settings.require_tool_approval and not tool_doc.is_readonly_tool)
	)


def _check_tool_access(tool_doc) -> None:
	allowed = [row.role for row in tool_doc.get("allowed_roles") or []]
	if not allowed or frappe.session.user == "Administrator":
		return
	if not set(frappe.get_roles()).intersection(allowed):
		frappe.throw(
			_("You are not permitted to use the tool {0}.").format(tool_doc.name),
			frappe.PermissionError,
		)


def _check_execution_context(tool_doc, *, conversation: str | None, agent: str | None) -> None:
	"""Revalidate the runtime links that authorized the model to offer a tool."""
	user = frappe.session.user
	if conversation:
		if not frappe.has_permission("AI Conversation", "read", doc=conversation, user=user):
			frappe.throw(_("You cannot use a tool in this conversation."), frappe.PermissionError)
		conversation_agent = frappe.db.get_value("AI Conversation", conversation, "agent")
		if agent and conversation_agent and conversation_agent != agent:
			frappe.throw(
				_("The invocation agent does not match the conversation agent."), frappe.PermissionError
			)

	if not agent:
		return
	if not frappe.has_permission("AI Agent", "read", doc=agent, user=user):
		frappe.throw(_("You are not permitted to use agent {0}.").format(agent), frappe.PermissionError)

	agent_doc = frappe.get_cached_doc("AI Agent", agent)
	assigned = any(row.enabled and row.tool == tool_doc.name for row in (agent_doc.get("tools") or []))
	if not agent_doc.enabled or not agent_doc.use_tools or not assigned:
		frappe.throw(
			_("Tool {0} is not enabled for agent {1}.").format(tool_doc.name, agent),
			frappe.PermissionError,
		)


def _validate_arguments(tool_doc, arguments: dict) -> None:
	"""Apply the deterministic parameter contract before calling any handler."""
	parameters = {row.parameter: row for row in (tool_doc.get("parameters") or [])}
	unknown = sorted(set(arguments).difference(parameters))
	if unknown:
		raise ToolExecutionError(
			_("Tool {0} received unsupported arguments: {1}.").format(tool_doc.name, ", ".join(unknown))
		)

	type_checks = {
		"String": lambda value: isinstance(value, str),
		"Number": lambda value: isinstance(value, (int, float)) and not isinstance(value, bool),
		"Integer": lambda value: isinstance(value, int) and not isinstance(value, bool),
		"Boolean": lambda value: isinstance(value, bool),
		"Array": lambda value: isinstance(value, list),
		"Object": lambda value: isinstance(value, dict),
	}
	for name, row in parameters.items():
		if row.required and name not in arguments:
			raise ToolExecutionError(_("Tool {0} requires argument {1}.").format(tool_doc.name, name))
		if name not in arguments:
			continue
		value = arguments[name]
		check = type_checks.get(row.parameter_type)
		if check and not check(value):
			raise ToolExecutionError(
				_("Argument {0} for tool {1} must be {2}.").format(name, tool_doc.name, row.parameter_type)
			)
		allowed = [
			item.strip() for item in (row.enum_values or "").replace(",", "\n").splitlines() if item.strip()
		]
		if allowed and value not in allowed:
			raise ToolExecutionError(
				_("Argument {0} for tool {1} is not an allowed value.").format(name, tool_doc.name)
			)


@contextmanager
def _runtime_limit(seconds: int):
	"""Install a hard wall-clock deadline, or fail closed when that is unsafe."""
	if seconds < 1:
		raise ToolExecutionError(_("The tool has no valid runtime limit."))
	if threading.current_thread() is not threading.main_thread():
		raise ToolExecutionError(
			_("This worker cannot safely enforce tool runtime limits; execution was refused.")
		)
	if not all(hasattr(signal, attr) for attr in ("SIGALRM", "setitimer", "getitimer", "ITIMER_REAL")):
		raise ToolExecutionError(
			_("This platform cannot enforce tool runtime limits; execution was refused.")
		)

	previous_timer = signal.getitimer(signal.ITIMER_REAL)
	if previous_timer[0] > 0:
		raise ToolExecutionError(
			_("A conflicting process deadline is already active; tool execution was refused.")
		)
	previous_handler = signal.getsignal(signal.SIGALRM)

	def timeout_handler(_signum, _frame):
		raise ToolExecutionError(_("Tool execution exceeded the {0}-second runtime limit.").format(seconds))

	signal.signal(signal.SIGALRM, timeout_handler)
	signal.setitimer(signal.ITIMER_REAL, seconds)
	try:
		yield
	finally:
		signal.setitimer(signal.ITIMER_REAL, 0)
		signal.signal(signal.SIGALRM, previous_handler)


def _dispatch(tool_doc, arguments: dict, *, pipeline_run: str | None = None):
	"""Route a tool to its handler based on the tool type."""
	tool_type = tool_doc.tool_type

	if tool_type in ("Builtin", "Server Method"):
		return _run_method(tool_doc, arguments)
	if tool_type == "DocType Query":
		return _run_doctype_query(tool_doc, arguments)
	if tool_type == "DocType Action":
		return _run_doctype_action(tool_doc, arguments)
	if tool_type == "Report":
		return _run_report(tool_doc, arguments)
	if tool_type == "Pipeline":
		from ai_fr_hg.ai.pipeline import run_pipeline

		parent_run = None
		if pipeline_run:
			parent = frappe.db.get_value(
				"AI Pipeline Run", pipeline_run, ["status", "triggered_by"], as_dict=True
			)
			if parent and parent.status == "Running" and parent.triggered_by == frappe.session.user:
				parent_run = pipeline_run
		run = run_pipeline(
			tool_doc.pipeline,
			input_data=arguments,
			enqueue_job=False,
			_parent_run=parent_run,
		)
		# Starting and recording the governed run is the Pipeline tool's action.
		# The run's own terminal status remains explicit in the result, including
		# failures caused by a nested approval request, and is never hidden.
		return {
			"pipeline_run": run.name,
			"pipeline_status": run.status,
			"error": run.error_message,
			"output": json.loads(run.output_data or "{}"),
		}

	raise ToolExecutionError(_("Unsupported tool type {0}.").format(tool_type))


def _run_method(tool_doc, arguments: dict):
	handler = tool_doc.handler
	if not handler:
		raise ToolExecutionError(_("Tool {0} has no handler configured.").format(tool_doc.name))

	if tool_doc.tool_type == "Builtin":
		builtins = get_builtin_handlers()
		if handler in builtins:
			return builtins[handler](**arguments)

	# Server Method: only whitelisted callables may be reached.
	method = frappe.get_attr(handler)
	if not getattr(method, "__wrapped__", None) and handler not in frappe.whitelisted:
		if not getattr(method, "whitelisted", False):
			raise ToolExecutionError(
				_("Method {0} is not whitelisted and cannot be used as a tool.").format(handler)
			)
	return method(**arguments)


def _run_doctype_query(tool_doc, arguments: dict):
	"""Read records, always through the caller's own permissions."""
	doctype = tool_doc.target_doctype
	if not doctype:
		raise ToolExecutionError(_("Tool {0} has no target DocType.").format(tool_doc.name))

	frappe.has_permission(doctype, "read", throw=True)

	filters = arguments.get("filters") or {}
	if isinstance(filters, str):
		try:
			filters = json.loads(filters)
		except ValueError:
			filters = {}

	fields = arguments.get("fields") or ["name"]
	if isinstance(fields, str):
		fields = [f.strip() for f in fields.split(",") if f.strip()]

	meta = frappe.get_meta(doctype)
	valid = {df.fieldname for df in meta.fields} | {
		"name",
		"owner",
		"creation",
		"modified",
		"docstatus",
	}
	fields = [f for f in fields if f in valid] or ["name"]

	return frappe.get_list(
		doctype,
		filters=filters,
		fields=fields,
		limit_page_length=min(cint(arguments.get("limit")) or 20, 100),
		order_by=arguments.get("order_by") or "modified desc",
	)


def _run_doctype_action(tool_doc, arguments: dict):
	"""Create or update a record, enforcing the caller's write permission."""
	doctype = tool_doc.target_doctype
	action = (arguments.get("action") or "create").lower()

	if action == "create":
		frappe.has_permission(doctype, "create", throw=True)
		doc = frappe.new_doc(doctype)
		doc.update(arguments.get("values") or {})
		doc.insert()
		return {"name": doc.name, "doctype": doctype, "action": "created"}

	if action == "update":
		name = arguments.get("name")
		if not name:
			raise ToolExecutionError(_("An update action needs a document name."))
		doc = frappe.get_doc(doctype, name)
		doc.check_permission("write")
		doc.update(arguments.get("values") or {})
		doc.save()
		return {"name": doc.name, "doctype": doctype, "action": "updated"}

	raise ToolExecutionError(_("Unsupported action {0}.").format(action))


def _run_report(tool_doc, arguments: dict):
	from frappe.desk.query_report import run

	report = tool_doc.target_report
	if not report:
		raise ToolExecutionError(_("Tool {0} has no target report.").format(tool_doc.name))

	frappe.has_permission("Report", "read", throw=True)
	filters = arguments.get("filters") or {}
	if isinstance(filters, str):
		try:
			filters = json.loads(filters)
		except ValueError:
			filters = {}

	result = run(report, filters=filters, ignore_prepared_report=True)
	return {
		"columns": [c.get("label") if isinstance(c, dict) else c for c in (result.get("columns") or [])],
		"rows": (result.get("result") or [])[:100],
	}


def _create_invocation(tool_doc, arguments, conversation, agent, pipeline_run) -> str:
	now = now_datetime()
	doc = frappe.new_doc("AI Tool Invocation")
	doc.update(
		{
			"tool": tool_doc.name,
			"status": "Running",
			"conversation": conversation,
			"agent": agent,
			"pipeline_run": pipeline_run,
			"user": frappe.session.user,
			"requested_at": now,
			"started_at": now,
			"arguments": frappe.as_json(arguments or {}),
		}
	)
	doc.flags.ignore_permissions = True
	doc.insert(ignore_permissions=True)
	return doc.name


def _mark_pending(name: str) -> None:
	frappe.db.set_value(
		"AI Tool Invocation",
		name,
		{
			"status": "Pending Approval",
			"started_at": None,
			"finished_at": None,
			"duration_ms": 0,
		},
		update_modified=False,
	)


def _update_invocation(name, status, started, result=None, error=None) -> None:
	values = {
		"status": status,
		"finished_at": now_datetime(),
		"duration_ms": int((time.monotonic() - started) * 1000),
	}
	if result is not None:
		values["result"] = frappe.as_json(result)[:60000]
	if error:
		values["error_message"] = error[:1000]
	frappe.db.set_value("AI Tool Invocation", name, values, update_modified=False)


def _lock_pending_invocation(invocation: str):
	if not frappe.db.get_value("AI Tool Invocation", invocation, "name", for_update=True):
		frappe.throw(_("Invocation {0} does not exist.").format(invocation))
	doc = frappe.get_doc("AI Tool Invocation", invocation)
	if doc.status != "Pending Approval":
		frappe.throw(_("Invocation {0} is not pending approval.").format(invocation))
	return doc


@frappe.whitelist()
def approve_invocation(invocation: str) -> dict:
	"""Resume one pending invocation under its original requester's authority."""
	frappe.only_for(["AI Manager", "System Manager"])
	approver = frappe.session.user
	doc = _lock_pending_invocation(invocation)
	if not doc.user or not frappe.db.get_value("User", doc.user, "enabled"):
		frappe.throw(_("The original requester is missing or disabled; execution was refused."))

	try:
		arguments = json.loads(doc.arguments or "{}")
	except (TypeError, ValueError):
		frappe.throw(_("Invocation {0} contains invalid arguments.").format(invocation))
	if not isinstance(arguments, dict):
		frappe.throw(_("Invocation {0} arguments must be a JSON object.").format(invocation))

	frappe.db.set_value(
		"AI Tool Invocation",
		invocation,
		{
			"status": "Running",
			"approved_by": approver,
			"approved_at": now_datetime(),
			"started_at": now_datetime(),
			"finished_at": None,
			"duration_ms": 0,
			"error_message": None,
		},
		update_modified=False,
	)

	tool_doc = frappe.get_cached_doc("AI Tool", doc.tool)
	frappe.set_user(doc.user)
	try:
		outcome = _execute_invocation(
			tool_doc,
			arguments,
			invocation=invocation,
			conversation=doc.conversation,
			agent=doc.agent,
			approval_granted=True,
		)
	finally:
		frappe.set_user(approver)

	write_audit_log(
		action="Tool Invocation Approved",
		category="Security",
		severity="Warning",
		message=f"{approver} approved tool {doc.tool} for requester {doc.user}.",
		details={"requester": doc.user, "agent": doc.agent, "outcome": outcome.get("status")},
		reference_doctype="AI Tool Invocation",
		reference_name=invocation,
		raise_on_error=True,
	)
	return outcome


@frappe.whitelist()
def reject_invocation(invocation: str) -> dict:
	"""Reject one pending invocation and record the decision provenance."""
	frappe.only_for(["AI Manager", "System Manager"])
	approver = frappe.session.user
	doc = _lock_pending_invocation(invocation)
	frappe.db.set_value(
		"AI Tool Invocation",
		invocation,
		{
			"status": "Rejected",
			"rejected_by": approver,
			"rejected_at": now_datetime(),
			"finished_at": now_datetime(),
		},
		update_modified=False,
	)
	write_audit_log(
		action="Tool Invocation Rejected",
		category="Security",
		severity="Warning",
		message=f"{approver} rejected tool {doc.tool} for requester {doc.user}.",
		details={"requester": doc.user, "agent": doc.agent},
		reference_doctype="AI Tool Invocation",
		reference_name=invocation,
		raise_on_error=True,
	)
	return {"status": "Rejected", "invocation": invocation}
