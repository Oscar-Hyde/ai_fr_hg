# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Execution logging, redaction and audit trail.

Every AI call produces an `AI Execution Log`. Prompts and responses are only
persisted when the administrator has enabled it, and are always passed through
the configured redaction patterns first.
"""

import json
import re
import traceback
from functools import lru_cache

import frappe
from frappe.utils import cint, now_datetime

MAX_STORED_CHARACTERS = 60_000


@lru_cache(maxsize=8)
def _compiled_patterns(raw: str) -> tuple:
	patterns = []
	for line in (raw or "").splitlines():
		line = line.strip()
		if not line:
			continue
		try:
			patterns.append(re.compile(line))
		except re.error:
			continue
	return tuple(patterns)


def redact(text: str | None) -> str:
	"""Apply administrator-configured redaction patterns to `text`."""
	if not text:
		return ""
	raw = frappe.db.get_single_value("AI Platform Settings", "redact_patterns") or ""
	for pattern in _compiled_patterns(raw):
		text = pattern.sub("[REDACTED]", text)
	return text[:MAX_STORED_CHARACTERS]


def clear_pattern_cache() -> None:
	_compiled_patterns.cache_clear()


def _serialise(value) -> str | None:
	if value is None:
		return None
	try:
		return json.dumps(value, default=str, indent=2)[:MAX_STORED_CHARACTERS]
	except (TypeError, ValueError):
		return str(value)[:MAX_STORED_CHARACTERS]


def start_execution_log(
	operation: str,
	model,
	messages=None,
	options: dict | None = None,
	reference_doctype: str | None = None,
	reference_name: str | None = None,
	conversation: str | None = None,
	pipeline_run: str | None = None,
):
	"""Create an `AI Execution Log` in the Running state."""
	settings = frappe.get_cached_doc("AI Platform Settings")

	prompt_text = None
	if messages and settings.log_prompts:
		prompt_text = redact(
			"\n\n".join(f"[{m.role}] {m.content}" for m in messages if getattr(m, "content", None))
		)

	log = frappe.new_doc("AI Execution Log")
	log.update(
		{
			"operation": operation,
			"status": "Running",
			"provider": model.provider if model else None,
			"model": model.name if model else None,
			"started_at": now_datetime(),
			"user": frappe.session.user,
			"reference_doctype": reference_doctype,
			"reference_name": reference_name,
			"conversation": conversation,
			"pipeline_run": pipeline_run,
			"prompt_text": prompt_text,
			"request_payload": _serialise(options),
		}
	)
	log.flags.ignore_permissions = True
	log.insert(ignore_permissions=True)
	return log


def finish_execution_log(
	log,
	result,
	provider: str | None = None,
	error=None,
	retry_count: int = 0,
	model: str | None = None,
):
	"""Close an execution log with either a result or an error.

	`provider` and `model` record the target that *actually* served the call.
	PROV-01 failover can move a request to an equivalent model on another
	runtime, and an audit trail that still names the originally requested model
	would be wrong.
	"""
	if not log:
		return

	settings = frappe.get_cached_doc("AI Platform Settings")
	values = {
		"finished_at": now_datetime(),
		"retry_count": cint(retry_count),
	}
	if provider:
		values["provider"] = provider
	if model:
		values["model"] = model

	if error is not None:
		values.update(
			{
				"status": "Failed",
				"error_message": str(error)[:1000],
				"traceback": traceback.format_exc()[:MAX_STORED_CHARACTERS],
			}
		)
	elif result is not None:
		values.update(
			{
				"status": "Success",
				"duration_ms": cint(result.duration_ms),
				"prompt_tokens": cint(result.prompt_tokens),
				"completion_tokens": cint(result.completion_tokens),
				"total_tokens": cint(result.total_tokens),
				"tokens_per_second": result.tokens_per_second,
			}
		)
		if settings.log_responses:
			values["response_text"] = redact(result.content)
			values["response_payload"] = _serialise(result.raw)
	else:
		values["status"] = "Failed"

	try:
		frappe.db.set_value("AI Execution Log", log.name, values, update_modified=False)
	except Exception:
		frappe.log_error(title="AI Execution Log update failed", message=frappe.get_traceback())


def write_audit_log(
	action: str,
	category: str = "Execution",
	severity: str = "Info",
	message: str | None = None,
	details: dict | None = None,
	reference_doctype: str | None = None,
	reference_name: str | None = None,
	*,
	raise_on_error: bool = False,
) -> None:
	"""Append an entry to the platform audit trail.

	Most observational call sites remain best-effort. Security-sensitive owners
	can pass ``raise_on_error=True`` so an unaudited state change fails closed.
	"""
	savepoint = f"ai_audit_{frappe.generate_hash(length=8)}"
	frappe.db.savepoint(savepoint)
	try:
		doc = frappe.new_doc("AI Audit Log")
		doc.update(
			{
				"action": action,
				"category": category,
				"severity": severity,
				"user": frappe.session.user,
				"message": (message or "")[:1000],
				"details": _serialise(details),
				"reference_doctype": reference_doctype,
				"reference_name": reference_name,
				"ip_address": frappe.local.request_ip if hasattr(frappe.local, "request_ip") else None,
			}
		)
		if getattr(frappe.local, "request", None):
			doc.site_user_agent = (frappe.get_request_header("User-Agent") or "")[:500]
		doc.flags.ignore_permissions = True
		doc.insert(ignore_permissions=True)
	except Exception:
		traceback = frappe.get_traceback()
		# PostgreSQL aborts the transaction after a database error. Restore the
		# caller's transaction before either propagating a fail-closed audit or
		# writing the best-effort Error Log.
		frappe.db.rollback(save_point=savepoint)
		if raise_on_error:
			raise
		frappe.log_error(title="AI Audit Log write failed", message=traceback)
	else:
		release = getattr(frappe.db, "release_savepoint", None)
		if release:
			release(savepoint)
