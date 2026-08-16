# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import now_datetime


class AITask(Document):
	_DOCTYPE_NAME = "AI Task"

	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		agent: DF.Link | None
		completed_on: DF.Datetime | None
		due_date: DF.Datetime | None
		error_message: DF.SmallText | None
		execution_log: DF.Link | None
		input_data: DF.Code | None
		instruction: DF.LongText
		knowledge_base: DF.Link | None
		model: DF.Link | None
		naming_series: DF.Literal["AITASK-.YYYY.-"]
		pipeline: DF.Link | None
		pipeline_run: DF.Link | None
		priority: DF.Literal["Low", "Medium", "High", "Urgent"]
		result: DF.LongText | None
		result_data: DF.Code | None
		status: DF.Literal[
			"Open",
			"Pending Approval",
			"Approved",
			"Rejected",
			"In Progress",
			"Completed",
			"Failed",
			"Cancelled",
		]
		subject: DF.Data
		task_type: DF.Literal[
			"Question", "Summarize", "Classify", "Extract Data", "Compare", "Pipeline", "Custom"
		]
	# end: auto-generated types

	def validate(self):
		if self.task_type == "Pipeline" and not self.pipeline:
			frappe.throw(_("A pipeline is required for a Pipeline task."))

	def on_update(self):
		"""Run the task once it is approved, or when it needs no approval."""
		if self.status != "Approved":
			return
		self.db_set("status", "In Progress", update_modified=False)
		frappe.enqueue(
			"ai_fr_hg.ai_automation.doctype.ai_task.ai_task.execute_task",
			queue="long",
			timeout=1800,
			job_id=f"ai_task_{self.name}",
			deduplicate=True,
			enqueue_after_commit=True,
			task=self.name,
		)

	@frappe.whitelist()
	def run_now(self) -> dict:
		"""Execute this task immediately."""
		return execute_task(self.name)


def execute_task(task: str) -> dict:
	"""Execute an AI task and store the result on the record."""
	import json

	from ai_fr_hg.ai.agent import run_agent_turn
	from ai_fr_hg.ai.intelligence import classify, extract_data, summarize

	doc = frappe.get_doc("AI Task", task)
	doc.db_set("status", "In Progress", update_modified=False)

	payload = {}
	if doc.input_data:
		try:
			payload = json.loads(doc.input_data)
		except ValueError:
			payload = {}

	try:
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
			)
			doc.db_set("pipeline_run", run.name, update_modified=False)
			result_data = json.loads(run.output_data or "{}")
			result_text = f"Pipeline run {run.name} finished with status {run.status}."

		elif doc.task_type == "Summarize":
			result_text = summarize(
				payload.get("text") or doc.instruction,
				model=doc.model,
				reference_doctype="AI Task",
				reference_name=task,
			)

		elif doc.task_type == "Classify":
			result_data = classify(
				payload.get("text") or doc.instruction,
				categories=payload.get("categories") or [],
				model=doc.model,
				reference_doctype="AI Task",
				reference_name=task,
			)
			result_text = result_data.get("category")

		elif doc.task_type == "Extract Data":
			result_data = extract_data(
				payload.get("text") or doc.instruction,
				schema=payload.get("schema"),
				model=doc.model,
				reference_doctype="AI Task",
				reference_name=task,
			)
			result_text = frappe.as_json(result_data)

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

		doc.db_set(
			{
				"status": "Completed",
				"result": result_text,
				"result_data": frappe.as_json(result_data) if result_data else None,
				"completed_on": now_datetime(),
				"error_message": None,
			},
			update_modified=False,
		)
		return {"task": task, "status": "Completed"}

	except Exception as exc:
		doc.db_set({"status": "Failed", "error_message": str(exc)[:1000]}, update_modified=False)
		frappe.log_error(title=f"AI Task failed: {task}", message=frappe.get_traceback())
		return {"task": task, "status": "Failed", "error": str(exc)}
