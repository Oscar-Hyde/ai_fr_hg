# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Pipeline execution engine.

A pipeline is an ordered list of steps. Each step reads a key from the shared
run context, does its work and writes a key back, so steps compose without
knowing about each other. Every step's status, duration and output is recorded
on the `AI Pipeline Run` for full traceability.
"""

import json
import time
import traceback
from uuid import uuid4

import frappe
from frappe import _
from frappe.utils import cint, get_datetime, now_datetime

from ai_fr_hg.ai.exceptions import PipelineApprovalRequired, PipelineError, PipelineStepRecordedError
from ai_fr_hg.utils.authority import as_user as _authority_as_user

_PIPELINE_METHOD_MARKER = "_ai_pipeline_step_method"
MAX_NESTED_PIPELINE_DEPTH = 10
MAX_PIPELINE_DEPENDENCY_EDGES = 1000


def validate_pipeline_dependencies(pipeline: str, dependencies: list[str]) -> None:
	"""Reject configured nested-pipeline cycles and excessive dependency graphs.

	Runtime ancestry checks remain mandatory because configuration can change
	between queueing and execution, and legacy/corrupt rows may bypass validation.
	"""
	if not pipeline:
		return
	pending = [(dependency, (pipeline,), 1) for dependency in dependencies if dependency]
	edges = 0
	while pending:
		dependency, ancestry, depth = pending.pop()
		if dependency in ancestry:
			chain = " -> ".join((*ancestry, dependency))
			raise PipelineError(_("Nested pipeline dependency cycle detected: {0}.").format(chain))
		if depth > MAX_NESTED_PIPELINE_DEPTH:
			raise PipelineError(
				_("Nested pipeline dependencies cannot exceed {0} levels.").format(MAX_NESTED_PIPELINE_DEPTH)
			)

		children = frappe.get_all(
			"AI Pipeline Step",
			filters={
				"parent": dependency,
				"parenttype": "AI Pipeline",
				"parentfield": "steps",
				"step_type": "Pipeline",
			},
			pluck="sub_pipeline",
		)
		edges += len(children)
		if edges > MAX_PIPELINE_DEPENDENCY_EDGES:
			raise PipelineError(
				_("Nested pipeline dependency graph exceeds the {0}-edge validation limit.").format(
					MAX_PIPELINE_DEPENDENCY_EDGES
				)
			)
		next_ancestry = (*ancestry, dependency)
		pending.extend((child, next_ancestry, depth + 1) for child in children if child)


def pipeline_step_method(method):
	"""Explicitly mark an app-owned callable as safe for Custom Method steps.

	Extension apps can alternatively register dotted paths in the
	``ai_pipeline_methods`` hook. The callable still runs as the pipeline's
	requesting user and must enforce document-level permissions itself.
	"""
	setattr(method, _PIPELINE_METHOD_MARKER, True)
	return method


def resolve_pipeline_step_method(dotted_path: str):
	"""Resolve only explicitly trusted Custom Method implementations."""
	registered = _registered_pipeline_methods()
	app_owned = dotted_path.startswith("ai_fr_hg.")
	if dotted_path not in registered and not app_owned:
		raise PipelineError(
			_("Custom pipeline method {0} is not registered in ai_pipeline_methods.").format(dotted_path)
		)

	try:
		method = frappe.get_attr(dotted_path)
	except Exception as exc:
		raise PipelineError(_("Could not load custom pipeline method {0}.").format(dotted_path)) from exc
	if not callable(method):
		raise PipelineError(_("Custom pipeline method {0} is not callable.").format(dotted_path))
	if dotted_path not in registered and not getattr(method, _PIPELINE_METHOD_MARKER, False):
		raise PipelineError(
			_("Custom pipeline method {0} has not been marked with @pipeline_step_method.").format(
				dotted_path
			)
		)
	return method


def _registered_pipeline_methods() -> set[str]:
	configured = frappe.get_hooks("ai_pipeline_methods") or {}
	values = configured.values() if isinstance(configured, dict) else configured
	registered: set[str] = set()
	for value in values:
		if isinstance(value, (list, tuple, set)):
			registered.update(str(item) for item in value if item)
		elif value:
			registered.add(str(value))
	return registered


def _validate_parent_run(parent_run: str, child_pipeline: str) -> None:
	"""Validate nested-run authority, depth, and recursive pipeline cycles."""
	seen: set[str] = set()
	current = parent_run
	for _depth in range(MAX_NESTED_PIPELINE_DEPTH):
		if not current or current in seen:
			break
		seen.add(current)
		parent = frappe.db.get_value(
			"AI Pipeline Run",
			current,
			["pipeline", "status", "triggered_by", "parent_pipeline_run"],
			as_dict=True,
		)
		if not parent:
			raise PipelineError(_("The parent Pipeline Run does not exist."))
		if current == parent_run and (
			parent.status != "Running" or parent.triggered_by != frappe.session.user
		):
			raise PipelineError(_("The parent Pipeline Run is not active under the requesting user."))
		if parent.pipeline == child_pipeline:
			raise PipelineError(
				_("Recursive nested pipeline call to {0} was refused.").format(child_pipeline)
			)
		current = parent.parent_pipeline_run
	else:
		if current:
			raise PipelineError(
				_("Nested pipelines cannot exceed {0} levels.").format(MAX_NESTED_PIPELINE_DEPTH)
			)
	if current in seen:
		raise PipelineError(_("A cycle exists in the parent Pipeline Run chain."))


def run_pipeline(
	pipeline: str,
	input_data: dict | None = None,
	reference_doctype: str | None = None,
	reference_name: str | None = None,
	enqueue_job: bool = True,
	_parent_run: str | None = None,
	trigger_source: str = "Manual",
	idempotency_key: str | None = None,
):
	"""Start a pipeline run, either inline or on a background worker."""
	from ai_fr_hg.ai.governance import check_capability

	check_capability("pipeline")
	if input_data is not None and not isinstance(input_data, dict):
		frappe.throw(_("Pipeline input must be a JSON object."))

	pipeline_doc = frappe.get_cached_doc("AI Pipeline", pipeline)
	pipeline_doc.check_permission("read")
	if not pipeline_doc.enabled:
		frappe.throw(_("Pipeline {0} is disabled.").format(pipeline))
	if _parent_run:
		_validate_parent_run(_parent_run, pipeline)
	if idempotency_key:
		existing = frappe.db.get_value(
			"AI Pipeline Run",
			{"pipeline": pipeline, "idempotency_key": idempotency_key},
			["name", "status"],
			as_dict=True,
		)
		if existing:
			return frappe.get_doc("AI Pipeline Run", existing.name)

	run = frappe.new_doc("AI Pipeline Run")
	run.update(
		{
			"pipeline": pipeline,
			"status": "Queued",
			"triggered_by": frappe.session.user,
			"trigger_source": trigger_source or "Manual",
			"idempotency_key": idempotency_key,
			"parent_pipeline_run": _parent_run,
			"reference_doctype": reference_doctype,
			"reference_name": reference_name,
			"input_data": frappe.as_json(input_data or {}),
		}
	)
	run.flags.ignore_permissions = True
	run.insert(ignore_permissions=True)
	_audit_pipeline_state(run, "Queued", raise_on_error=True)

	if enqueue_job:
		frappe.enqueue(
			"ai_fr_hg.ai.pipeline.execute_run",
			queue=pipeline_doc.queue or "long",
			timeout=3600,
			job_id=f"ai_pipeline_{run.name}",
			deduplicate=True,
			run=run.name,
			enqueue_after_commit=True,
		)
	else:
		execute_run(run.name)
		run.reload()

	return run


def execute_run(run: str) -> dict:
	"""Execute a queued run once, under its original requester's authority."""
	run_doc = frappe.get_doc("AI Pipeline Run", run)
	authority = run_doc.triggered_by
	if not authority or authority == "Guest" or not frappe.db.get_value("User", authority, "enabled"):
		return _finish_invalid_run(run_doc, _("The pipeline requester is missing or disabled."))

	with _authority_as_user(authority):
		return _execute_run(run_doc)


def _execute_run(run_doc, *, resume_from_idx: int = 0) -> dict:
	"""Locked pipeline state machine. Call through :func:`execute_run` or :func:`resume_run`."""
	status = frappe.db.get_value("AI Pipeline Run", run_doc.name, "status", for_update=True)
	if resume_from_idx:
		if status != "Running":
			return {"run": run_doc.name, "status": status, "skipped": True}
	elif status != "Queued":
		return {"run": run_doc.name, "status": status, "skipped": True}

	pipeline_doc = frappe.get_cached_doc("AI Pipeline", run_doc.pipeline)
	if not pipeline_doc.enabled:
		return _finish_invalid_run(run_doc, _("Pipeline {0} is disabled.").format(run_doc.pipeline))
	started = time.monotonic()
	durable = _is_standalone_background_run(run_doc.name) or bool(resume_from_idx)
	run_doc.reload()
	if not resume_from_idx:
		run_doc.status = "Running"
		run_doc.started_at = now_datetime()
		run_doc.finished_at = None
		run_doc.error_message = None
		run_doc.traceback = None
		run_doc.set("step_logs", [])
		_audit_pipeline_state(run_doc, "Running", raise_on_error=True)
		_persist_checkpoint(run_doc, durable=durable)
	else:
		run_doc.status = "Running"
		run_doc.finished_at = None
		run_doc.error_message = None
		_persist_checkpoint(run_doc, durable=durable)

	try:
		if resume_from_idx:
			try:
				context = json.loads(run_doc.output_data or run_doc.input_data or "{}")
			except (TypeError, ValueError):
				context = json.loads(run_doc.input_data or "{}")
		else:
			context = json.loads(run_doc.input_data or "{}")
		if not isinstance(context, dict):
			raise PipelineError(_("Pipeline input must be a JSON object."))
		# ``run`` is reserved execution provenance; callers and parent pipelines
		# must not be able to carry a different run identity into this context.
		context["run"] = run_doc.name
		if run_doc.reference_doctype and run_doc.reference_name:
			context.setdefault("reference_doctype", run_doc.reference_doctype)
			context.setdefault("reference_name", run_doc.reference_name)

		failed = False
		cancelled = False
		error_message = None
		failure_traceback = None

		for step in pipeline_doc.steps:
			if resume_from_idx and cint(step.idx) <= cint(resume_from_idx):
				continue
			if _is_cancelled(run_doc.name):
				cancelled = True
				break

			if not step.enabled:
				run_doc.append(
					"step_logs",
					{"step_name": step.step_name, "step_type": step.step_type, "status": "Skipped"},
				)
				_persist_checkpoint(run_doc, context=context, durable=durable)
				continue

			step_started = time.monotonic()
			log_row = run_doc.append(
				"step_logs",
				{"step_name": step.step_name, "step_type": step.step_type, "status": "Running"},
			)
			_persist_checkpoint(run_doc, context=context, durable=durable)

			attempts = max(cint(step.retry_count), 0) + 1 if step.on_error == "Retry" else 1
			last_error = None

			for attempt in range(attempts):
				if _is_cancelled(run_doc.name):
					cancelled = True
					break
				save_point = f"ai_pipeline_step_{uuid4().hex}"
				frappe.db.savepoint(save_point)
				try:
					output = execute_step(step, context, run_doc)
					key = step.output_field or f"step_{step.idx}"
					context[key] = output
					log_row.status = "Success"
					log_row.output = frappe.as_json(output)[:20000]
					last_error = None
					frappe.db.release_savepoint(save_point)
					break
				except Exception as exc:
					if isinstance(exc, PipelineStepRecordedError):
						# The tool invocation and its mandatory audit record are the
						# durable result of this step; retain them while failing the run.
						frappe.db.release_savepoint(save_point)
					else:
						frappe.db.rollback(save_point=save_point)
					last_error = exc
					failure_traceback = traceback.format_exc()[:20000]
					if isinstance(exc, PipelineApprovalRequired):
						break
					if attempt < attempts - 1:
						if _wait_for_retry(run_doc.name, min(2**attempt, 8)):
							cancelled = True
							break

			log_row.duration_ms = int((time.monotonic() - step_started) * 1000)
			if cancelled:
				log_row.status = "Failed"
				log_row.error_message = _("Pipeline run cancelled.")
				_persist_checkpoint(run_doc, context=context, durable=durable)
				break

			if last_error is not None:
				if isinstance(last_error, PipelineApprovalRequired):
					return _pause_for_approval(
						run_doc,
						step,
						log_row,
						last_error,
						context,
						durable=durable,
						started=started,
					)
				log_row.status = "Failed"
				log_row.error_message = str(last_error)[:1000]
				if step.on_error in ("Stop", "Retry"):
					failed = True
					error_message = _("Step '{0}' failed: {1}").format(step.step_name, last_error)
			_persist_checkpoint(run_doc, context=context, durable=durable)
			if failed:
				break

		duration_ms = int((time.monotonic() - started) * 1000)
		# Cancellation is authoritative even if it races with the final step.
		cancelled = cancelled or _is_cancelled(run_doc.name)
		run_doc.update(
			{
				"status": "Cancelled" if cancelled else ("Failed" if failed else "Completed"),
				"finished_at": now_datetime(),
				"duration_ms": duration_ms,
				"output_data": frappe.as_json(_serialisable(context))[:60000],
				"error_message": error_message,
				"traceback": failure_traceback if failed else None,
			}
		)
		terminal_saved = _persist_terminal_state(run_doc, durable=durable)
		cancelled = cancelled or not terminal_saved or run_doc.status == "Cancelled"

		if not cancelled:
			try:
				_update_pipeline_stats(pipeline_doc.name, failed)
			except Exception:
				frappe.log_error(title="AI pipeline statistics update failed", message=frappe.get_traceback())

		try:
			frappe.publish_realtime(
				"ai_pipeline_finished",
				{"run": run_doc.name, "pipeline": pipeline_doc.name, "status": run_doc.status},
				user=run_doc.triggered_by,
			)
		except Exception:
			frappe.log_error(title="AI pipeline completion event failed", message=frappe.get_traceback())
		if run_doc.status == "Completed" and run_doc.parent_pipeline_run:
			_maybe_resume_parent(run_doc)
		return {"run": run_doc.name, "status": run_doc.status, "duration_ms": duration_ms}

	except Exception as exc:
		return _finish_invalid_run(
			run_doc,
			str(exc) or exc.__class__.__name__,
			started=started,
			trace=traceback.format_exc(),
			durable=durable,
		)


def _is_standalone_background_run(run: str) -> bool:
	"""Return whether this exact run owns the worker transaction.

	A nested synchronous run executes while the parent's RQ job is still in
	``frappe.local.job``. Matching the run argument prevents that child from
	committing through the parent's savepoints.
	"""
	job = getattr(frappe.local, "job", None)
	return bool(
		job
		and job.get("method") in {"ai_fr_hg.ai.pipeline.execute_run", "ai_fr_hg.ai.pipeline.resume_run"}
		and (job.get("kwargs") or {}).get("run") == run
	)


def _persist_checkpoint(run_doc, context: dict | None = None, *, durable: bool) -> bool:
	"""Persist progress without ever overwriting an authoritative cancellation."""
	current = frappe.db.get_value("AI Pipeline Run", run_doc.name, "status", for_update=True)
	if current == "Cancelled" and run_doc.status != "Cancelled":
		run_doc.status = "Cancelled"
		return False
	if current == "Waiting Approval" and run_doc.status not in {
		"Waiting Approval",
		"Running",
		"Cancelled",
		"Failed",
	}:
		run_doc.status = current
		return False
	if context is not None:
		run_doc.output_data = frappe.as_json(_serialisable(context))[:60000]
	run_doc.flags.ignore_permissions = True
	run_doc.save(ignore_permissions=True)
	if durable:
		# A standalone worker owns this transaction. Committing checkpoints makes
		# progress/cancellation observable and does not split a caller's transaction.
		frappe.db.commit()  # nosemgrep: frappe-manual-commit
	return True


def _persist_terminal_state(run_doc, *, durable: bool) -> bool:
	"""Atomically persist and audit a terminal state without losing cancellation."""
	current = frappe.db.get_value("AI Pipeline Run", run_doc.name, "status", for_update=True)
	if current in {"Completed", "Failed", "Cancelled"} and current != run_doc.status:
		run_doc.status = current
		return False
	if current == "Cancelled":
		run_doc.status = "Cancelled"
		return False

	_audit_pipeline_state(run_doc, run_doc.status, raise_on_error=True)
	run_doc.flags.ignore_permissions = True
	run_doc.save(ignore_permissions=True)
	if durable:
		frappe.db.commit()  # nosemgrep: frappe-manual-commit
	return True


def _audit_pipeline_state(run_doc, status: str, *, raise_on_error: bool) -> None:
	"""Write the canonical lifecycle audit record for a Pipeline Run state."""
	from ai_fr_hg.ai.logging import write_audit_log

	severity = "Critical" if status == "Failed" else ("Warning" if status == "Cancelled" else "Info")
	write_audit_log(
		action=f"Pipeline Run {status}",
		category="Execution",
		severity=severity,
		message=_("Pipeline run {0} entered state {1}.").format(run_doc.name, status),
		details={
			"pipeline": run_doc.pipeline,
			"status": status,
			"triggered_by": run_doc.triggered_by,
			"parent_pipeline_run": run_doc.parent_pipeline_run,
			"duration_ms": run_doc.duration_ms if status in {"Completed", "Failed", "Cancelled"} else None,
			"error": run_doc.error_message if status == "Failed" else None,
		},
		reference_doctype="AI Pipeline Run",
		reference_name=run_doc.name,
		raise_on_error=raise_on_error,
	)


def _is_cancelled(run: str) -> bool:
	return frappe.db.get_value("AI Pipeline Run", run, "status") == "Cancelled"


def _wait_for_retry(run: str, seconds: int) -> bool:
	"""Back off in short intervals so cancellation does not wait for the full delay."""
	for _retry_tick in range(max(seconds, 0) * 10):
		if _is_cancelled(run):
			return True
		time.sleep(0.1)
	return _is_cancelled(run)


def _finish_invalid_run(
	run_doc,
	error: str,
	*,
	started: float | None = None,
	trace: str | None = None,
	durable: bool = False,
) -> dict:
	"""Persist and audit an unexpected failure without changing a terminal run."""
	current = frappe.db.get_value("AI Pipeline Run", run_doc.name, "status", for_update=True)
	duration_ms = int((time.monotonic() - started) * 1000) if started is not None else 0
	if current in {"Completed", "Failed", "Cancelled"}:
		run_doc.status = current
		return {"run": run_doc.name, "status": current, "duration_ms": duration_ms, "skipped": True}

	run_doc.update(
		{
			"status": "Failed",
			"finished_at": now_datetime(),
			"duration_ms": duration_ms,
			"error_message": error[:1000],
			"traceback": (trace or "")[:20000],
		}
	)
	_audit_pipeline_state(run_doc, "Failed", raise_on_error=True)
	run_doc.flags.ignore_permissions = True
	run_doc.save(ignore_permissions=True)
	if durable:
		frappe.db.commit()  # nosemgrep: frappe-manual-commit
	return {"run": run_doc.name, "status": run_doc.status, "duration_ms": duration_ms}


def execute_step(step, context: dict, run_doc):
	"""Execute a single pipeline step and return its output."""
	from ai_fr_hg.ai.intelligence import (
		classify,
		compare_documents,
		extract_data,
		run_prompt_template,
		summarize,
	)

	config = json.loads(step.config) if step.config else {}
	source_key = step.input_field or "content"
	value = context.get(source_key)

	step_type = step.step_type

	if step_type == "Extract Text":
		from ai_fr_hg.ai.ingestion import process_document_now

		document = value or context.get("document") or context.get("reference_name")
		if not document:
			raise PipelineError(_("No document supplied to the Extract Text step."))
		outcome = process_document_now(document, embed=False)
		if outcome.get("status") == "Failed":
			raise PipelineError(outcome.get("error") or _("Document text extraction failed."))
		return frappe.db.get_value("AI Document", document, "content")

	if step_type == "Chunk":
		from ai_fr_hg.ai.chunking import chunk_text

		chunks = chunk_text(
			_as_text(value),
			chunk_size=cint(config.get("chunk_size")) or 1200,
			chunk_overlap=cint(config.get("chunk_overlap")) or 150,
		)
		return [{"index": c.index, "heading": c.heading, "content": c.content} for c in chunks]

	if step_type == "Embed":
		from ai_fr_hg.ai.ingestion import process_document_now

		document = context.get("document") or context.get("reference_name")
		if not document:
			raise PipelineError(_("No document supplied to the Embed step."))
		outcome = process_document_now(document, embed=True)
		if outcome.get("status") == "Failed":
			raise PipelineError(outcome.get("error") or _("Document embedding failed."))
		return outcome

	if step_type == "Summarize":
		return summarize(
			_as_text(value),
			model=step.model,
			instructions=config.get("instructions", ""),
			max_words=cint(config.get("max_words")),
			reference_doctype=run_doc.reference_doctype,
			reference_name=run_doc.reference_name,
		)

	if step_type == "Classify":
		categories = config.get("categories") or []
		if not categories:
			raise PipelineError(_("The Classify step needs a 'categories' list in its configuration."))
		return classify(
			_as_text(value),
			categories=categories,
			model=step.model,
			instructions=config.get("instructions", ""),
			reference_doctype=run_doc.reference_doctype,
			reference_name=run_doc.reference_name,
		)

	if step_type == "Extract Data":
		if not step.extraction_schema:
			raise PipelineError(_("The Extract Data step needs an extraction schema."))
		return extract_data(
			_as_text(value),
			schema=step.extraction_schema,
			model=step.model,
			reference_doctype=run_doc.reference_doctype,
			reference_name=run_doc.reference_name,
		)

	if step_type == "Compare":
		a = context.get(config.get("document_a", "document_a"))
		b = context.get(config.get("document_b", "document_b"))
		if not a or not b:
			raise PipelineError(_("The Compare step needs two documents in the run context."))
		return compare_documents(a, b, model=step.model, instructions=config.get("instructions", ""))

	if step_type == "Translate":
		from ai_fr_hg.ai.translation import translate_text

		target = config.get("target_language")
		if not target:
			raise PipelineError(
				_("The Translate step needs a 'target_language' (ar, en or he) in its configuration.")
			)
		outcome = translate_text(
			_as_text(value),
			target,
			config.get("source_language"),
			model=step.model,
			glossary=config.get("glossary"),
			tone=config.get("tone") or "Neutral",
			domain=config.get("domain") or "",
			knowledge_base=step.knowledge_base,
			reference_doctype=run_doc.reference_doctype,
			reference_name=run_doc.reference_name,
		)
		if config.get("return") == "text":
			return outcome.text
		return {"text": outcome.text, **outcome.as_dict()}

	if step_type == "Prompt":
		if step.prompt_template:
			return run_prompt_template(
				step.prompt_template,
				context={**context, "input": value},
				model=step.model,
				reference_doctype=run_doc.reference_doctype,
				reference_name=run_doc.reference_name,
			)["output"]

		from ai_fr_hg.ai.engine import run_chat

		prompt = config.get("prompt") or _as_text(value)
		result = run_chat(
			[{"role": "user", "content": prompt}],
			model=step.model,
			operation="Chat",
			pipeline_run=run_doc.name,
			reference_doctype=run_doc.reference_doctype,
			reference_name=run_doc.reference_name,
		)
		return result.content

	if step_type == "Tool":
		from ai_fr_hg.ai.tools import execute_tool

		arguments = config.get("arguments") or (value if isinstance(value, dict) else {})
		outcome = execute_tool(step.tool, arguments, pipeline_run=run_doc.name)
		if outcome.get("status") == "Pending Approval":
			invocation = outcome.get("invocation") or (outcome.get("result") or {}).get("invocation")
			raise PipelineApprovalRequired(
				_("Tool {0} requires approval (invocation {1}).").format(
					step.tool, invocation or _("unknown")
				),
				invocation=invocation,
			)
		if outcome.get("status") != "Success":
			raise PipelineStepRecordedError(outcome.get("error") or _("Tool {0} failed.").format(step.tool))
		return outcome

	if step_type == "Pipeline":
		sub_run = run_pipeline(
			step.sub_pipeline,
			input_data={**context},
			enqueue_job=False,
			_parent_run=run_doc.name,
		)
		if sub_run.status == "Waiting Approval":
			raise PipelineApprovalRequired(
				_("Sub-pipeline {0} is waiting for approval.").format(step.sub_pipeline),
				invocation=sub_run.waiting_invocation,
				child_run=sub_run.name,
			)
		if sub_run.status != "Completed":
			error = _("Sub-pipeline {0} ended with status {1}: {2}").format(
				step.sub_pipeline, sub_run.status, sub_run.error_message or _("no details")
			)
			raise PipelineStepRecordedError(error)
		return json.loads(sub_run.output_data or "{}")

	if step_type == "Custom Method":
		if not step.method:
			raise PipelineError(_("The Custom Method step needs a dotted method path."))
		method = resolve_pipeline_step_method(step.method)
		return method(context=context, step=step, config=config)

	raise PipelineError(_("Unsupported step type {0}.").format(step_type))


def _as_text(value) -> str:
	if value is None:
		return ""
	if isinstance(value, str):
		return value
	if isinstance(value, list):
		return "\n\n".join(item.get("content", "") if isinstance(item, dict) else str(item) for item in value)
	if isinstance(value, dict):
		return json.dumps(value, default=str, indent=2)
	return str(value)


def _serialisable(context: dict) -> dict:
	"""Strip values that cannot be stored as JSON on the run record."""
	clean = {}
	for key, value in context.items():
		try:
			json.dumps(value, default=str)
			clean[key] = value
		except (TypeError, ValueError):
			clean[key] = str(value)
	return clean


def _update_pipeline_stats(pipeline: str, failed: bool) -> None:
	"""Atomically update counters so concurrent runs cannot lose increments."""
	frappe.db.sql(
		"""
		update `tabAI Pipeline`
		set run_count = coalesce(run_count, 0) + 1,
			success_count = coalesce(success_count, 0) + %s,
			failure_count = coalesce(failure_count, 0) + %s,
			last_run_on = %s
		where name = %s
		""",
		(0 if failed else 1, 1 if failed else 0, now_datetime(), pipeline),
	)


def _pause_for_approval(run_doc, step, log_row, error, context, *, durable: bool, started: float) -> dict:
	"""Persist Waiting Approval and checkpoint so approval can resume exactly once."""
	log_row.status = "Waiting Approval"
	log_row.error_message = str(error)[:1000]
	run_doc.status = "Waiting Approval"
	run_doc.resume_from_idx = cint(step.idx)
	run_doc.resume_output_key = step.output_field or f"step_{step.idx}"
	run_doc.waiting_invocation = getattr(error, "invocation", None)
	run_doc.waiting_child_run = getattr(error, "child_run", None)
	run_doc.output_data = frappe.as_json(_serialisable(context))[:60000]
	run_doc.error_message = str(error)[:1000]
	_audit_pipeline_state(run_doc, "Waiting Approval", raise_on_error=True)
	run_doc.flags.ignore_permissions = True
	run_doc.save(ignore_permissions=True)
	if durable:
		frappe.db.commit()  # nosemgrep: frappe-manual-commit
	try:
		frappe.publish_realtime(
			"ai_pipeline_finished",
			{"run": run_doc.name, "pipeline": run_doc.pipeline, "status": "Waiting Approval"},
			user=run_doc.triggered_by,
		)
	except Exception:
		frappe.log_error(title="AI pipeline waiting-approval event failed", message=frappe.get_traceback())
	return {
		"run": run_doc.name,
		"status": "Waiting Approval",
		"duration_ms": int((time.monotonic() - started) * 1000),
		"invocation": run_doc.waiting_invocation,
	}


def resume_run(run: str) -> dict:
	"""Resume a Waiting Approval run exactly once under the original requester."""
	run_doc = frappe.get_doc("AI Pipeline Run", run)
	authority = run_doc.triggered_by
	if not authority or authority == "Guest" or not frappe.db.get_value("User", authority, "enabled"):
		return _finish_invalid_run(run_doc, _("The pipeline requester is missing or disabled."))

	status = frappe.db.get_value("AI Pipeline Run", run, "status", for_update=True)
	if status != "Waiting Approval":
		return {"run": run, "status": status, "skipped": True}

	resume_from = cint(run_doc.resume_from_idx)
	output_key = run_doc.resume_output_key
	invocation = run_doc.waiting_invocation
	child_run = run_doc.waiting_child_run

	if child_run:
		child_status = frappe.db.get_value("AI Pipeline Run", child_run, "status")
		if child_status == "Waiting Approval":
			return {"run": run, "status": "Waiting Approval", "skipped": True}
		if child_status != "Completed":
			return _finish_invalid_run(
				run_doc, _("Nested pipeline {0} ended with status {1}.").format(child_run, child_status)
			)
	elif invocation:
		inv_status = frappe.db.get_value("AI Tool Invocation", invocation, "status")
		if inv_status != "Success":
			return _finish_invalid_run(
				run_doc, _("Waiting tool invocation {0} is {1}.").format(invocation, inv_status or "missing")
			)

	run_doc.db_set(
		{
			"status": "Running",
			"waiting_invocation": None,
			"waiting_child_run": None,
			"error_message": None,
		},
		update_modified=False,
	)
	# Apply the approved step output into the checkpointed context.
	try:
		context = json.loads(run_doc.output_data or run_doc.input_data or "{}")
	except (TypeError, ValueError):
		context = {}
	if not isinstance(context, dict):
		context = {}
	if child_run:
		child_output = frappe.db.get_value("AI Pipeline Run", child_run, "output_data")
		context[output_key or f"step_{resume_from}"] = json.loads(child_output or "{}")
	elif invocation:
		raw = frappe.db.get_value("AI Tool Invocation", invocation, "result")
		try:
			context[output_key or f"step_{resume_from}"] = json.loads(raw or "{}")
		except (TypeError, ValueError):
			context[output_key or f"step_{resume_from}"] = raw
	injected = context.get(output_key or f"step_{resume_from}")
	for log in run_doc.step_logs:
		if log.status == "Waiting Approval":
			log.status = "Success"
			if injected is not None:
				log.output = frappe.as_json(injected)[:20000]
			log.error_message = None
	run_doc.output_data = frappe.as_json(_serialisable(context))[:60000]
	run_doc.flags.ignore_permissions = True
	run_doc.save(ignore_permissions=True)

	with _authority_as_user(authority):
		return _execute_run(run_doc, resume_from_idx=resume_from)


def resume_after_approval(run: str, invocation: str | None = None) -> dict:
	"""Claim and resume a paused run after a tool invocation is approved or a child completes."""
	status = frappe.db.get_value("AI Pipeline Run", run, "status", for_update=True)
	if status != "Waiting Approval":
		return {"run": run, "status": status, "skipped": True}
	waiting = frappe.db.get_value("AI Pipeline Run", run, "waiting_invocation")
	if invocation and waiting and waiting != invocation:
		return {"run": run, "status": status, "skipped": True, "reason": "invocation_mismatch"}
	pipeline = frappe.db.get_value("AI Pipeline Run", run, "pipeline")
	queue = frappe.db.get_value("AI Pipeline", pipeline, "queue") or "long"
	job = getattr(frappe.local, "job", None)
	# Inline when we are already on a worker or in tests; otherwise enqueue.
	if job or frappe.flags.in_test:
		return resume_run(run)
	frappe.enqueue(
		"ai_fr_hg.ai.pipeline.resume_run",
		queue=queue,
		timeout=3600,
		job_id=f"ai_pipeline_resume_{run}_{invocation or 'child'}",
		deduplicate=True,
		run=run,
		enqueue_after_commit=True,
	)
	return {"run": run, "status": "Waiting Approval", "queued_resume": True}


def fail_waiting_run(run: str, error: str) -> dict:
	"""Reject a paused run after a tool invocation is denied."""
	run_doc = frappe.get_doc("AI Pipeline Run", run)
	status = frappe.db.get_value("AI Pipeline Run", run, "status", for_update=True)
	if status != "Waiting Approval":
		return {"run": run, "status": status, "skipped": True}
	return _finish_invalid_run(run_doc, error, durable=True)


def _maybe_resume_parent(child_run) -> None:
	parent = child_run.parent_pipeline_run
	if not parent:
		return
	status = frappe.db.get_value("AI Pipeline Run", parent, "status")
	waiting_child = frappe.db.get_value("AI Pipeline Run", parent, "waiting_child_run")
	if status == "Waiting Approval" and waiting_child == child_run.name:
		resume_after_approval(parent)


def next_cron_datetime(expression: str, after) -> object:
	from croniter import croniter

	return croniter(expression, after).get_next(type(after))


def compute_next_run(expression: str, after=None):
	after = after or now_datetime()
	try:
		return next_cron_datetime(expression, after)
	except Exception:
		return None


def claim_due_scheduled_pipelines(now=None) -> list[str]:
	"""Atomically claim due scheduled pipelines under a row lock.

	Misfire Skip: jump to the next future slot without running.
	Misfire Run Once: run once, then jump to the next future slot.
	"""
	now = now or now_datetime()
	claimed: list[str] = []
	rows = frappe.get_all(
		"AI Pipeline",
		filters={"enabled": 1, "trigger_type": "Scheduled"},
		fields=["name", "schedule_cron", "next_run_on", "misfire_policy"],
	)
	for row in rows:
		if not row.schedule_cron:
			continue
		due = get_datetime(row.next_run_on) if row.next_run_on else None
		if due and due > now:
			continue
		locked = frappe.db.get_value(
			"AI Pipeline",
			row.name,
			["name", "enabled", "trigger_type", "schedule_cron", "next_run_on", "misfire_policy"],
			as_dict=True,
			for_update=True,
		)
		if not locked or not locked.enabled or locked.trigger_type != "Scheduled" or not locked.schedule_cron:
			continue
		due = get_datetime(locked.next_run_on) if locked.next_run_on else None
		if due and due > now:
			continue
		future = compute_next_run(locked.schedule_cron, now)
		policy = locked.misfire_policy or "Run Once"
		should_run = policy != "Skip"
		frappe.db.set_value(
			"AI Pipeline",
			row.name,
			{"next_run_on": future, "last_run_on": now if should_run else locked.next_run_on},
			update_modified=False,
		)
		if should_run:
			claimed.append(row.name)
	return claimed


def trigger_document_ingest_pipelines(
	document_name: str, knowledge_base: str | None, requested_by: str
) -> list[str]:
	"""Start Document Ingest pipelines after a successful canonical index."""
	started: list[str] = []
	pipelines = frappe.get_all(
		"AI Pipeline",
		filters={"enabled": 1, "trigger_type": "Document Ingest"},
		fields=["name", "knowledge_base"],
	)
	content = frappe.db.get_value("AI Document", document_name, "content") or ""
	for row in pipelines:
		if row.knowledge_base and knowledge_base and row.knowledge_base != knowledge_base:
			continue
		job_id = f"ai_ingest_{row.name}_{document_name}"
		try:
			from frappe.utils.background_jobs import is_job_enqueued

			if is_job_enqueued(job_id):
				continue
		except Exception:
			pass
		run_pipeline(
			row.name,
			input_data={
				"document": document_name,
				"content": content,
				"knowledge_base": knowledge_base,
			},
			reference_doctype="AI Document",
			reference_name=document_name,
			trigger_source="Document Ingest",
			idempotency_key=job_id[:64],
		)
		started.append(row.name)
	return started


STEP_CONFIG_CONTRACTS = {
	"Classify": {"categories": list},
	"Summarize": {},
	"Chunk": {},
	"Compare": {},
	"Translate": {"target_language": str},
	"Tool": {},
}


def validate_step_config(step_type: str, config_text: str | None) -> dict:
	"""Typed configuration contract for PIPE-04. Empty config is allowed except where required."""
	if not config_text:
		if step_type == "Classify":
			raise PipelineError(_("The Classify step needs a 'categories' list in its configuration."))
		if step_type == "Translate":
			raise PipelineError(_("The Translate step needs a 'target_language' in its configuration."))
		return {}
	try:
		config = json.loads(config_text)
	except ValueError as exc:
		raise PipelineError(_("Configuration is not valid JSON: {0}").format(str(exc))) from exc
	if not isinstance(config, dict):
		raise PipelineError(_("Step configuration must be a JSON object."))
	required = STEP_CONFIG_CONTRACTS.get(step_type) or {}
	for key, expected in required.items():
		if key not in config:
			raise PipelineError(_("Step configuration is missing '{0}'.").format(key))
		if expected is list and not isinstance(config[key], list):
			raise PipelineError(_("Step configuration '{0}' must be a list.").format(key))
		if expected is str and not isinstance(config[key], str):
			raise PipelineError(_("Step configuration '{0}' must be a string.").format(key))
	return config
