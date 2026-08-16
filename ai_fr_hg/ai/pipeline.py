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

import frappe
from frappe import _
from frappe.utils import cint, now_datetime

from ai_fr_hg.ai.exceptions import PipelineError


def run_pipeline(
	pipeline: str,
	input_data: dict | None = None,
	reference_doctype: str | None = None,
	reference_name: str | None = None,
	enqueue_job: bool = True,
):
	"""Start a pipeline run, either inline or on a background worker."""
	from ai_fr_hg.ai.governance import check_capability

	check_capability("pipeline")

	pipeline_doc = frappe.get_cached_doc("AI Pipeline", pipeline)
	if not pipeline_doc.enabled:
		frappe.throw(_("Pipeline {0} is disabled.").format(pipeline))

	run = frappe.new_doc("AI Pipeline Run")
	run.update(
		{
			"pipeline": pipeline,
			"status": "Queued",
			"triggered_by": frappe.session.user,
			"reference_doctype": reference_doctype,
			"reference_name": reference_name,
			"input_data": frappe.as_json(input_data or {}),
		}
	)
	run.flags.ignore_permissions = True
	run.insert(ignore_permissions=True)

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
	"""Execute every step of a pipeline run in order."""
	run_doc = frappe.get_doc("AI Pipeline Run", run)
	pipeline_doc = frappe.get_cached_doc("AI Pipeline", run_doc.pipeline)

	started = time.monotonic()
	run_doc.db_set({"status": "Running", "started_at": now_datetime()}, update_modified=False)

	context: dict = json.loads(run_doc.input_data or "{}")
	context.setdefault("run", run)
	if run_doc.reference_doctype and run_doc.reference_name:
		context.setdefault("reference_doctype", run_doc.reference_doctype)
		context.setdefault("reference_name", run_doc.reference_name)

	# Reset the step log so re-runs do not accumulate rows.
	run_doc.set("step_logs", [])
	failed = False
	error_message = None

	for step in pipeline_doc.steps:
		if not step.enabled:
			run_doc.append(
				"step_logs",
				{"step_name": step.step_name, "step_type": step.step_type, "status": "Skipped"},
			)
			continue

		step_started = time.monotonic()
		log_row = run_doc.append(
			"step_logs",
			{"step_name": step.step_name, "step_type": step.step_type, "status": "Running"},
		)

		attempts = cint(step.retry_count) + 1 if step.on_error == "Retry" else 1
		last_error = None

		for attempt in range(attempts):
			try:
				output = execute_step(step, context, run_doc)
				key = step.output_field or f"step_{step.idx}"
				context[key] = output
				log_row.status = "Success"
				log_row.output = frappe.as_json(output)[:20000]
				last_error = None
				break
			except Exception as exc:
				last_error = exc
				if attempt < attempts - 1:
					time.sleep(min(2**attempt, 8))

		log_row.duration_ms = int((time.monotonic() - step_started) * 1000)

		if last_error is not None:
			log_row.status = "Failed"
			log_row.error_message = str(last_error)[:1000]
			if step.on_error in ("Stop", "Retry"):
				failed = True
				error_message = f"Step '{step.step_name}' failed: {last_error}"
				break

	duration_ms = int((time.monotonic() - started) * 1000)
	run_doc.update(
		{
			"status": "Failed" if failed else "Completed",
			"finished_at": now_datetime(),
			"duration_ms": duration_ms,
			"output_data": frappe.as_json(_serialisable(context))[:60000],
			"error_message": error_message,
			"traceback": traceback.format_exc()[:20000] if failed else None,
		}
	)
	run_doc.flags.ignore_permissions = True
	run_doc.save(ignore_permissions=True)

	_update_pipeline_stats(pipeline_doc.name, failed)

	frappe.publish_realtime(
		"ai_pipeline_finished",
		{"run": run, "pipeline": pipeline_doc.name, "status": run_doc.status},
		user=run_doc.triggered_by,
	)

	return {"run": run, "status": run_doc.status, "duration_ms": duration_ms}


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
		from ai_fr_hg.ai.ingestion import process_document

		document = value or context.get("document") or context.get("reference_name")
		if not document:
			raise PipelineError(_("No document supplied to the Extract Text step."))
		process_document(document, index=False)
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
		from ai_fr_hg.ai.knowledge import index_document

		document = context.get("document") or context.get("reference_name")
		if not document:
			raise PipelineError(_("No document supplied to the Embed step."))
		return index_document(document)

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
		return execute_tool(step.tool, arguments)

	if step_type == "Pipeline":
		sub_run = run_pipeline(step.sub_pipeline, input_data={**context}, enqueue_job=False)
		return json.loads(sub_run.output_data or "{}")

	if step_type == "Custom Method":
		if not step.method:
			raise PipelineError(_("The Custom Method step needs a dotted method path."))
		method = frappe.get_attr(step.method)
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
	row = frappe.db.get_value(
		"AI Pipeline", pipeline, ["run_count", "success_count", "failure_count"], as_dict=True
	)
	if not row:
		return
	frappe.db.set_value(
		"AI Pipeline",
		pipeline,
		{
			"run_count": cint(row.run_count) + 1,
			"success_count": cint(row.success_count) + (0 if failed else 1),
			"failure_count": cint(row.failure_count) + (1 if failed else 0),
			"last_run_on": now_datetime(),
		},
		update_modified=False,
	)
