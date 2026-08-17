# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class AISkill(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		description: DF.SmallText | None
		enabled: DF.Check
		instructions: DF.LongText
		scope: DF.Literal["Global", "User", "Role", "Agent"]
		scope_value: DF.Data | None
		skill_name: DF.Data
		skill_type: DF.Literal["Procedural", "Formatting", "Workflow"]
		source_candidate: DF.Link | None
		source_user: DF.Link | None
		usage_count: DF.Int
		version: DF.Int
	# end: auto-generated types

	def before_insert(self):
		if not self.flags.from_learning or not self.source_candidate:
			frappe.throw(_("AI Skill must be created by approving an AI Knowledge Candidate."))

	def validate(self):
		if not (self.instructions or "").strip():
			frappe.throw(_("Skill instructions cannot be empty."))
		if self.scope != "Global" and not (self.scope_value or "").strip():
			frappe.throw(_("Scope Value is required when Scope is not Global."))
		if self.scope == "Global":
			self.scope_value = None

	@frappe.whitelist()
	def disable(self) -> dict:
		"""Disable this skill so it is no longer injected into prompts."""
		frappe.only_for(["AI Manager", "System Manager"])
		if self.enabled:
			self.db_set("enabled", 0)
		return {"enabled": 0, "skill": self.name}

	@frappe.whitelist()
	def enable(self) -> dict:
		"""Re-enable a disabled skill."""
		frappe.only_for(["AI Manager", "System Manager"])
		if not self.enabled:
			self.db_set("enabled", 1)
		return {"enabled": 1, "skill": self.name}
