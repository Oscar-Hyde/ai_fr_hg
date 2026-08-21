# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class AIPipeline(Document):
	_DOCTYPE_NAME = "AI Pipeline"

	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		from ai_fr_hg.ai_automation.doctype.ai_pipeline_step.ai_pipeline_step import AIPipelineStep

		description: DF.SmallText | None
		enabled: DF.Check
		failure_count: DF.Int
		knowledge_base: DF.Link | None
		last_run_on: DF.Datetime | None
		misfire_policy: DF.Literal["Run Once", "Skip"]
		next_run_on: DF.Datetime | None
		pipeline_name: DF.Data
		queue: DF.Literal["default", "short", "long"]
		run_count: DF.Int
		schedule_cron: DF.Data | None
		steps: DF.Table[AIPipelineStep]
		success_count: DF.Int
		trigger_type: DF.Literal["Manual", "Document Ingest", "Scheduled", "API"]
	# end: auto-generated types

	def validate(self):
		self.validate_steps()
		self.validate_schedule()

	def validate_steps(self):
		from ai_fr_hg.ai.exceptions import PipelineError
		from ai_fr_hg.ai.pipeline import resolve_pipeline_step_method, validate_pipeline_dependencies

		if not self.steps:
			frappe.throw(_("A pipeline needs at least one step."))

		seen = set()
		dependencies = []
		for step in self.steps:
			if step.step_name in seen:
				frappe.throw(_("Row {0}: step name {1} is duplicated.").format(step.idx, step.step_name))
			seen.add(step.step_name)

			if not step.output_field:
				step.output_field = frappe.scrub(step.step_name or f"step_{step.idx}")

			required = {
				"Tool": "tool",
				"Extract Data": "extraction_schema",
				"Pipeline": "sub_pipeline",
				"Custom Method": "method",
			}.get(step.step_type)
			if required and not step.get(required):
				frappe.throw(
					_("Row {0}: {1} is required for a {2} step.").format(
						step.idx, required.replace("_", " ").title(), step.step_type
					)
				)

			if step.step_type == "Pipeline":
				if step.sub_pipeline == self.name:
					frappe.throw(_("Row {0}: a pipeline cannot call itself.").format(step.idx))
				if step.sub_pipeline:
					dependencies.append(step.sub_pipeline)

			if step.step_type == "Custom Method" and step.method:
				try:
					resolve_pipeline_step_method(step.method)
				except Exception as exc:
					frappe.throw(_("Row {0}: {1}").format(step.idx, str(exc)))

			if step.config or step.step_type in {"Classify", "Translate"}:
				from ai_fr_hg.ai.pipeline import validate_step_config

				try:
					validate_step_config(step.step_type, step.config)
				except Exception as exc:
					frappe.throw(_("Row {0}: {1}").format(step.idx, str(exc)))

		try:
			validate_pipeline_dependencies(self.name, dependencies)
		except PipelineError as exc:
			frappe.throw(str(exc))

	def validate_schedule(self):
		if self.trigger_type == "Document Ingest" and not self.knowledge_base:
			# Empty knowledge base means "any KB"; that is an explicit operator choice.
			pass
		if self.trigger_type != "Scheduled":
			self.next_run_on = None
			return
		if not self.schedule_cron:
			frappe.throw(_("A cron schedule is required for a scheduled pipeline."))
		try:
			from croniter import croniter

			croniter(self.schedule_cron)
		except ImportError:
			pass
		except Exception:
			frappe.throw(_("{0} is not a valid cron expression.").format(self.schedule_cron))
		from ai_fr_hg.ai.pipeline import compute_next_run

		self.next_run_on = compute_next_run(self.schedule_cron) or self.next_run_on

	@frappe.whitelist()
	def run_now(self, input_data: str | None = None) -> dict:
		"""Trigger this pipeline immediately."""
		import json

		from ai_fr_hg.ai.pipeline import run_pipeline

		payload = {}
		if input_data:
			try:
				payload = json.loads(input_data)
			except ValueError:
				payload = {"content": input_data}

		run = run_pipeline(self.name, input_data=payload)
		return {"run": run.name, "status": run.status}
