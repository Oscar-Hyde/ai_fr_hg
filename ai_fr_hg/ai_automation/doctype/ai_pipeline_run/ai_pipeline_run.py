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
		parent_pipeline_run: DF.Link | None
		pipeline: DF.Link
		reference_doctype: DF.Link | None
		reference_name: DF.DynamicLink | None
		started_at: DF.Datetime | None
		status: DF.Literal["Queued", "Running", "Completed", "Failed", "Cancelled"]
		step_logs: DF.Table[AIPipelineRunStep]
		traceback: DF.Code | None
		triggered_by: DF.Link | None
	# end: auto-generated types

	def _assert_action_authority(self) -> None:
		user = frappe.session.user
		roles = set(frappe.get_roles(user))
		if user == "Administrator" or roles.intersection({"AI Manager", "System Manager"}):
			return
		if self.triggered_by != user:
			frappe.throw(
				_("Only the run owner or an AI Manager can perform this action."), frappe.PermissionError
			)

	@frappe.whitelist()
	def retry(self) -> dict:
		"""Create a new run from a terminal failed/cancelled run."""
		import json

		from ai_fr_hg.ai.pipeline import run_pipeline

		self._assert_action_authority()
		status = frappe.db.get_value("AI Pipeline Run", self.name, "status", for_update=True)
		if status not in {"Failed", "Cancelled"}:
			frappe.throw(_("Only failed or cancelled pipeline runs can be retried."))
		try:
			input_data = json.loads(self.input_data or "{}")
		except (TypeError, ValueError):
			frappe.throw(_("The saved pipeline input is not valid JSON."))
		if not isinstance(input_data, dict):
			frappe.throw(_("The saved pipeline input must be a JSON object."))

		run = run_pipeline(
			self.pipeline,
			input_data=input_data,
			reference_doctype=self.reference_doctype,
			reference_name=self.reference_name,
		)
		return {"run": run.name, "status": run.status}

	@frappe.whitelist()
	def cancel_run(self) -> dict:
		"""Authoritatively cancel a queued/running run and signal its RQ job."""
		from frappe.utils import now_datetime

		from ai_fr_hg.ai.logging import write_audit_log

		self._assert_action_authority()
		status = frappe.db.get_value("AI Pipeline Run", self.name, "status", for_update=True)
		if status not in {"Queued", "Running"}:
			frappe.throw(_("Only queued or running pipelines can be cancelled."))

		frappe.db.set_value(
			"AI Pipeline Run",
			self.name,
			{"status": "Cancelled", "finished_at": now_datetime()},
			update_modified=False,
		)
		write_audit_log(
			action="Pipeline Run Cancelled",
			category="Execution",
			severity="Warning",
			message=_("Pipeline run {0} was cancelled by {1}.").format(self.name, frappe.session.user),
			details={"previous_status": status, "pipeline": self.pipeline},
			reference_doctype="AI Pipeline Run",
			reference_name=self.name,
			raise_on_error=True,
		)

		try:
			from frappe.utils.background_jobs import get_job, get_redis_conn
			from rq.command import send_stop_job_command
			from rq.job import JobStatus

			job = get_job(f"ai_pipeline_{self.name}")
			if job:
				job_status = job.get_status(refresh=True)
				if job_status == JobStatus.QUEUED:
					job.cancel()
				elif job_status == JobStatus.STARTED:
					send_stop_job_command(connection=get_redis_conn(), job_id=job.id)
		except Exception:
			# The committed state guard prevents a queued job from starting and
			# cooperative checks stop a running job even if Redis signalling fails.
			frappe.log_error(title="AI pipeline RQ cancellation failed", message=frappe.get_traceback())

		frappe.publish_realtime(
			"ai_pipeline_finished",
			{"run": self.name, "pipeline": self.pipeline, "status": "Cancelled"},
			user=self.triggered_by,
		)
		return {"run": self.name, "status": "Cancelled"}
