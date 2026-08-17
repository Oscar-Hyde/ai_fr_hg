# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint


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
		provenance_context: DF.SmallText | None
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
		from ai_fr_hg.ai.governance import check_capability

		actor = frappe.session.user
		check_capability("learning", user=actor)
		if not cint(frappe.db.get_single_value("AI Platform Settings", "learning_enabled")):
			frappe.throw(_("The Learning Loop is disabled in AI Platform Settings."))
		is_manager = actor == "Administrator" or bool(
			{"AI Manager", "System Manager"}.intersection(frappe.get_roles(actor))
		)
		if not self.user:
			self.user = actor
		if self.user != actor and not is_manager:
			frappe.throw(_("You cannot attribute teaching to another user."), frappe.PermissionError)
		if not is_manager:
			# The direct DocType form must obey the same least-privilege scope as
			# the canonical teaching API, regardless of client-supplied defaults.
			self.target_scope = "User"
			self.target_scope_value = actor

		# Older clients submitted free-form text through ``provenance``. Retain it
		# only as explicitly unverified context and replace the authoritative field
		# with deterministic server attribution below.
		if not self.provenance_context and self.provenance:
			self.provenance_context = self.provenance
		self.approval_required = 1 if cint(
			frappe.db.get_single_value("AI Platform Settings", "require_memory_approval")
		) else 0
		if self.status != "Draft":
			frappe.throw(_("A new knowledge candidate must start in Draft status."))
		self._set_authoritative_provenance(actor)

	def validate(self):
		from ai_fr_hg.ai.learning import _validate_reference, _validate_scope

		if not (self.content or "").strip():
			frappe.throw(_("Knowledge Content cannot be empty."))
		if not self.title:
			self.title = self.content[:140]
		if not self.is_new() and self.has_value_changed("user"):
			frappe.throw(_("The teaching user cannot be changed after candidate creation."))

		reference_ok, reference_message = _validate_reference(
			self.source_reference_doctype,
			self.source_reference_name,
			self.source_type,
			user=self.user,
		)
		if not reference_ok:
			frappe.throw(reference_message)
		scope_ok, scope_message = _validate_scope(
			self.target_scope or "Global", self.target_scope_value, self.user
		)
		if not scope_ok:
			frappe.throw(scope_message, frappe.PermissionError)
		if self.target_scope == "Global":
			self.target_scope_value = None
		if not self.is_new() and self.has_value_changed("status"):
			frappe.throw(_("Candidate status can only be changed through Validate, Approve, or Reject."))
		self._set_authoritative_provenance(self.owner or frappe.session.user)

	def _set_authoritative_provenance(self, recorded_by: str) -> None:
		"""Build attribution from trusted fields, clearly labelling user context."""
		parts = [
			_("Source Type: {0}").format(self.source_type or "Explicit Teaching"),
			_("Teaching User: {0}").format(self.user or frappe.session.user),
			_("Recorded By: {0}").format(recorded_by or frappe.session.user),
		]
		if self.source_reference_doctype and self.source_reference_name:
			parts.append(
				_("Source Record: {0} {1}").format(
					self.source_reference_doctype, self.source_reference_name
				)
			)
		if (self.provenance_context or "").strip():
			parts.append(
				_("User-Provided Context (Unverified): {0}").format(
					(self.provenance_context or "").strip()
				)
			)
		self.provenance = "; ".join(parts)[:2000]

	def after_insert(self):
		"""Make every insertion path produce the same fail-closed audit record."""
		from ai_fr_hg.ai.logging import write_audit_log

		write_audit_log(
			action="Knowledge Candidate Created",
			category="Learning",
			message=_("Candidate {0} ({1}) created for {2} scope.").format(
				self.name, self.candidate_type, self.target_scope
			),
			details={
				"teaching_user": self.user,
				"source_type": self.source_type,
				"source_reference_doctype": self.source_reference_doctype,
				"source_reference_name": self.source_reference_name,
				"target_scope_value": self.target_scope_value,
			},
			reference_doctype="AI Knowledge Candidate",
			reference_name=self.name,
			raise_on_error=True,
		)

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
