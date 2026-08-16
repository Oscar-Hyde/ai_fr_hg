# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class AIPipelineRun(Document):
	_DOCTYPE_NAME = "AI Pipeline Run"

	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		from ai_fr_hg.ai_automation.doctype.ai_pipeline_run_step.ai_pipeline_run_step import AIPipelineRunStep

		duration_ms: DF.Int
		error_message: DF.SmallText | None
		finished_at: DF.Datetime | None
		input_data: DF.Code | None
		naming_series: DF.Literal["AIRUN-.YYYY.-"]
		output_data: DF.Code | None
		pipeline: DF.Link
		reference_doctype: DF.Link | None
		reference_name: DF.DynamicLink | None
		started_at: DF.Datetime | None
		status: DF.Literal["Queued", "Running", "Completed", "Failed", "Cancelled"]
		step_logs: DF.Table[AIPipelineRunStep]
		traceback: DF.Code | None
		triggered_by: DF.Link | None
	# end: auto-generated types

	@frappe.whitelist()
	def retry(self) -> dict:
		"""Re-run this pipeline with the same input."""
		import json

		from ai_fr_hg.ai.pipeline import run_pipeline

		run = run_pipeline(
			self.pipeline,
			input_data=json.loads(self.input_data or "{}"),
			reference_doctype=self.reference_doctype,
			reference_name=self.reference_name,
		)
		return {"run": run.name}

	@frappe.whitelist()
	def cancel_run(self) -> dict:
		"""Mark a queued run as cancelled."""
		if self.status not in ("Queued", "Running"):
			frappe.throw(_("Only queued or running pipelines can be cancelled."))
		self.db_set("status", "Cancelled")
		return {"run": self.name, "status": "Cancelled"}
