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
		if not self.steps:
			frappe.throw(_("A pipeline needs at least one step."))

		seen = set()
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

			if step.step_type == "Pipeline" and step.sub_pipeline == self.name:
				frappe.throw(_("Row {0}: a pipeline cannot call itself.").format(step.idx))

			if step.config:
				import json

				try:
					json.loads(step.config)
				except ValueError as exc:
					frappe.throw(
						_("Row {0}: Configuration is not valid JSON: {1}").format(step.idx, str(exc))
					)

	def validate_schedule(self):
		if self.trigger_type != "Scheduled":
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
