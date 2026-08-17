# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Tool registry and safe execution.

A tool lets the model act on the Frappe site. Every invocation is permission
checked against the *calling user* (never elevated), recorded as an
`AI Tool Invocation`, and bounded by the tool's runtime limit. Write tools can
require explicit human approval before they run.
"""

import json
import time

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
	tool: str, arguments: dict | str | None = None, conversation: str | None = None, agent: str | None = None
) -> dict:
	"""Run a tool and return `{status, result, error}`. Never raises."""
	started = time.monotonic()

	if isinstance(arguments, str):
		try:
			arguments = json.loads(arguments)
		except Exception:
			arguments = {"_raw": arguments}
	if not isinstance(arguments, dict):
		arguments = {} if arguments is None else {"_raw": arguments}

	if not frappe.db.exists("AI Tool", tool):
		return {"status": "Failed", "error": f"Unknown tool: {tool}", "result": None}

	tool_doc = frappe.get_cached_doc("AI Tool", tool)
	invocation = _create_invocation(tool_doc, arguments, conversation, agent)

	try:
		if not tool_doc.enabled:
			raise ToolExecutionError(_("Tool {0} is disabled.").format(tool))

		check_capability("tools")
		_check_tool_access(tool_doc)

		settings = frappe.get_cached_doc("AI Platform Settings")
		needs_approval = tool_doc.requires_approval or (
			settings.require_tool_approval and not tool_doc.is_readonly_tool
		)
		if needs_approval and not frappe.flags.ai_tool_approved:
			_update_invocation(invocation, status="Pending Approval", started=started)
			return {
				"status": "Pending Approval",
				"result": {
					"message": _(
						"This action needs approval. An administrator must approve invocation {0}."
					).format(invocation),
					"invocation": invocation,
				},
				"error": None,
			}

		result = _dispatch(tool_doc, arguments or {})
		_update_invocation(invocation, status="Success", started=started, result=result)

		write_audit_log(
			action=f"Tool Executed: {tool}",
			category="Execution",
			message=f"Tool {tool} executed successfully.",
			details={"arguments": arguments},
			reference_doctype="AI Tool Invocation",
			reference_name=invocation,
		)
		return {"status": "Success", "result": result, "error": None, "invocation": invocation}

	except Exception as exc:
		error = str(exc)
		_update_invocation(invocation, status="Failed", started=started, error=error)
		write_audit_log(
			action=f"Tool Failed: {tool}",
			category="Execution",
			severity="Warning",
			message=error,
			details={"arguments": arguments},
			reference_doctype="AI Tool Invocation",
			reference_name=invocation,
		)
		return {"status": "Failed", "result": None, "error": error, "invocation": invocation}


def _check_tool_access(tool_doc) -> None:
	allowed = [row.role for row in tool_doc.get("allowed_roles") or []]
	if not allowed or frappe.session.user == "Administrator":
		return
	if not set(frappe.get_roles()).intersection(allowed):
		frappe.throw(
			_("You are not permitted to use the tool {0}.").format(tool_doc.name),
			frappe.PermissionError,
		)


def _dispatch(tool_doc, arguments: dict):
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

		run = run_pipeline(tool_doc.pipeline, input_data=arguments, enqueue_job=False)
		return {"pipeline_run": run.name, "output": json.loads(run.output_data or "{}")}

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


def _create_invocation(tool_doc, arguments, conversation, agent) -> str:
	doc = frappe.new_doc("AI Tool Invocation")
	doc.update(
		{
			"tool": tool_doc.name,
			"status": "Running",
			"conversation": conversation,
			"user": frappe.session.user,
			"started_at": now_datetime(),
			"arguments": frappe.as_json(arguments or {}),
		}
	)
	doc.flags.ignore_permissions = True
	doc.insert(ignore_permissions=True)
	return doc.name


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


@frappe.whitelist()
def approve_invocation(invocation: str) -> dict:
	"""Approve and run a tool invocation that was held for approval."""
	frappe.only_for(["AI Manager", "System Manager"])

	doc = frappe.get_doc("AI Tool Invocation", invocation)
	if doc.status != "Pending Approval":
		frappe.throw(_("Invocation {0} is not pending approval.").format(invocation))

	arguments = json.loads(doc.arguments or "{}")
	frappe.flags.ai_tool_approved = True
	try:
		outcome = execute_tool(doc.tool, arguments, conversation=doc.conversation)
	finally:
		frappe.flags.ai_tool_approved = False

	write_audit_log(
		action="Tool Invocation Approved",
		category="Security",
		severity="Warning",
		message=f"{frappe.session.user} approved tool {doc.tool}.",
		reference_doctype="AI Tool Invocation",
		reference_name=invocation,
	)
	return outcome


@frappe.whitelist()
def reject_invocation(invocation: str) -> dict:
	"""Reject a tool invocation that was held for approval."""
	frappe.only_for(["AI Manager", "System Manager"])

	frappe.db.set_value("AI Tool Invocation", invocation, "status", "Rejected")
	write_audit_log(
		action="Tool Invocation Rejected",
		category="Security",
		severity="Warning",
		reference_doctype="AI Tool Invocation",
		reference_name=invocation,
	)
	return {"status": "Rejected"}
