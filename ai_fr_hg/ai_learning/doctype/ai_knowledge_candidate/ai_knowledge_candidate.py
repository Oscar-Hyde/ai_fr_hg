# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class AIKnowledgeCandidate(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		approved_by: DF.Link | None
		approved_on: DF.Datetime | None
		approval_required: DF.Check
		candidate_type: DF.Literal["Fact", "Preference", "Instruction", "Feedback", "Document"]
		confidence: DF.Percent
		conflict_count: DF.Int
		conflicts_summary: DF.SmallText | None
		content: DF.LongText
		naming_series: DF.Literal["AI-CAND-.YYYY.-"]
		provenance: DF.SmallText | None
		source_reference_doctype: DF.Link | None
		source_reference_name: DF.DynamicLink | None
		source_type: DF.Literal[
			"Explicit Teaching", "Chat Correction", "Feedback", "Document", "Tool Result", "Automation"
		]
		status: DF.Literal["Draft", "Validated", "Conflict", "Approved", "Rejected"]
		target_scope: DF.Literal["Global", "User", "Role", "Agent"]
		target_scope_value: DF.Data | None
		testing_status: DF.Literal["Not Tested", "Passed", "Conflict", "Failed"]
		title: DF.Data
		user: DF.Link | None
		validation_notes: DF.SmallText | None
	# end: auto-generated types

	def before_insert(self):
		if not self.user:
			self.user = frappe.session.user
		if not self.provenance:
			self.provenance = f"{self.source_type or 'Explicit Teaching'} by {self.user}."
		configured = frappe.db.get_single_value("AI Platform Settings", "require_memory_approval")
		if configured is not None:
			self.approval_required = configured
		if self.status != "Draft":
			frappe.throw(_("A new knowledge candidate must start in Draft status."))

	def validate(self):
		if not (self.content or "").strip():
			frappe.throw(_("Knowledge Content cannot be empty."))
		if not self.title:
			self.title = self.content[:140]
		if bool(self.source_reference_doctype) != bool(self.source_reference_name):
			frappe.throw(_("Source DocType and Source Name must be provided together."))
		if self.target_scope != "Global" and not (self.target_scope_value or "").strip():
			frappe.throw(_("Target Scope Value is required when Target Scope is not Global."))
		if self.target_scope == "Global":
			self.target_scope_value = None
		if not self.is_new() and self.has_value_changed("status"):
			frappe.throw(_("Candidate status can only be changed through Validate, Approve, or Reject."))

	@frappe.whitelist()
	def validate_and_test(self) -> dict:
		"""Run provenance validation and conflict testing for a Desk-created candidate."""
		from ai_fr_hg.ai.learning import process_candidate

		return process_candidate(self.name)

	@frappe.whitelist()
	def approve(self, notes: str | None = None) -> dict:
		"""Approve this candidate and promote it to a memory or skill."""
		from ai_fr_hg.ai.learning import approve_candidate

		return approve_candidate(self.name, notes=notes)

	@frappe.whitelist()
	def reject(self, notes: str | None = None) -> dict:
		"""Reject this candidate so it is never learned."""
		from ai_fr_hg.ai.learning import reject_candidate

		return reject_candidate(self.name, notes=notes)
