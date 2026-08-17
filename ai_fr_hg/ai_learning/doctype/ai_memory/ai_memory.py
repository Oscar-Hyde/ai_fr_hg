# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class AIMemory(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		confidence: DF.Percent
		content: DF.LongText
		embedding: DF.LongText | None
		embedding_dimensions: DF.Int
		embedding_format: DF.Data | None
		embedding_model: DF.Link | None
		helpful_count: DF.Int
		last_used_on: DF.Datetime | None
		memory_type: DF.Literal["Fact", "Preference", "Instruction", "Feedback"]
		naming_series: DF.Literal["AI-MEM-.YYYY.-"]
		not_helpful_count: DF.Int
		provenance: DF.SmallText | None
		scope: DF.Literal["Global", "User", "Role", "Agent"]
		scope_value: DF.Data | None
		source_candidate: DF.Link | None
		source_type: DF.Data | None
		source_user: DF.Link | None
		status: DF.Literal["Active", "Archived"]
		usage_count: DF.Int
	# end: auto-generated types

	def before_insert(self):
		if not self.flags.from_learning or not self.source_candidate:
			frappe.throw(_("AI Memory must be created by approving an AI Knowledge Candidate."))

	def validate(self):
		if not (self.content or "").strip():
			frappe.throw(_("Memory content cannot be empty."))
		if self.scope != "Global" and not (self.scope_value or "").strip():
			frappe.throw(_("Scope Value is required when Scope is not Global."))
		if self.scope == "Global":
			self.scope_value = None

	@frappe.whitelist()
	def archive(self) -> dict:
		"""Stop this memory from being injected into future turns."""
		frappe.only_for(["AI Manager", "System Manager"])
		if self.status == "Archived":
			return {"status": "Archived", "memory": self.name}
		self.db_set("status", "Archived")
		return {"status": "Archived", "memory": self.name}
