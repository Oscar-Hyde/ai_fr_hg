# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class AITask(Document):
	_DOCTYPE_NAME = "AI Task"

	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		agent: DF.Link | None
		completed_on: DF.Datetime | None
		custom_method: DF.Data | None
		due_date: DF.Datetime | None
		error_message: DF.SmallText | None
		execution_log: DF.Link | None
		extraction_schema: DF.Link | None
		input_data: DF.Code | None
		instruction: DF.LongText
		knowledge_base: DF.Link | None
		model: DF.Link | None
		naming_series: DF.Literal["AITASK-.YYYY.-"]
		pipeline: DF.Link | None
		pipeline_run: DF.Link | None
		priority: DF.Literal["Low", "Medium", "High", "Urgent"]
		requested_by: DF.Link | None
		requires_approval: DF.Check
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

	def before_insert(self):
		self.status = "Open"
		if not self.requested_by:
			self.requested_by = frappe.session.user

	def validate(self):
		from ai_fr_hg.ai.tasks import validate_task_contract

		if self.is_new():
			self.status = "Open"
		elif not self.flags.get("task_transition"):
			previous = self.get_doc_before_save()
			if previous and previous.status != self.status:
				frappe.throw(
					_("Task status can only change through Submit, Approve, Reject, Run, Cancel, or Retry."),
					frappe.ValidationError,
				)
		if self.requested_by and not self.is_new():
			previous = self.get_doc_before_save()
			if previous and previous.requested_by and previous.requested_by != self.requested_by:
				frappe.throw(_("The task requester cannot be changed."))
		validate_task_contract(self)

	@frappe.whitelist()
	def submit_task(self) -> dict:
		from ai_fr_hg.ai.tasks import submit_task

		return submit_task(self.name)

	@frappe.whitelist()
	def approve(self) -> dict:
		from ai_fr_hg.ai.tasks import approve_task

		return approve_task(self.name)

	@frappe.whitelist()
	def reject(self) -> dict:
		from ai_fr_hg.ai.tasks import reject_task

		return reject_task(self.name)

	@frappe.whitelist()
	def cancel_task(self) -> dict:
		from ai_fr_hg.ai.tasks import cancel_task

		return cancel_task(self.name)

	@frappe.whitelist()
	def retry(self) -> dict:
		from ai_fr_hg.ai.tasks import retry_task

		return retry_task(self.name)

	@frappe.whitelist()
	def run_now(self) -> dict:
		from ai_fr_hg.ai.tasks import run_now

		return run_now(self.name)
