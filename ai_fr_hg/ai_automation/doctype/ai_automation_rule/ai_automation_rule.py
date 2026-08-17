# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class AIAutomationRule(Document):
	_DOCTYPE_NAME = "AI Automation Rule"

	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		action_type: DF.Literal[
			"Run Pipeline", "Run Agent", "Summarize", "Classify", "Extract Data", "Ingest Document"
		]
		agent: DF.Link | None
		condition: DF.Code | None
		document_type: DF.Link
		enabled: DF.Check
		event: DF.Literal["after_insert", "on_update", "on_submit", "on_cancel", "on_trash"]
		extraction_schema: DF.Link | None
		failure_count: DF.Int
		knowledge_base: DF.Link | None
		last_error: DF.SmallText | None
		last_run_on: DF.Datetime | None
		pipeline: DF.Link | None
		prompt_template: DF.Link | None
		queue: DF.Literal["default", "short", "long"]
		rule_name: DF.Data
		run_count: DF.Int
		source_field: DF.Data | None
		target_field: DF.Data | None
	# end: auto-generated types

	def validate(self):
		self.validate_action()
		self.validate_condition()
		self.validate_target_field()

	def validate_action(self):
		required = {
			"Run Pipeline": "pipeline",
			"Run Agent": "agent",
			"Extract Data": "extraction_schema",
			"Ingest Document": "knowledge_base",
		}.get(self.action_type)

		if required and not self.get(required):
			frappe.throw(
				_("{0} is required for the {1} action.").format(
					_(self.meta.get_label(required)), self.action_type
				)
			)

		if self.document_type and self.document_type.startswith("AI "):
			frappe.throw(_("Automation rules cannot target the platform's own DocTypes; this would recurse."))

	def validate_condition(self):
		if not self.condition:
			return
		try:
			compile(self.condition, "<condition>", "eval")
		except SyntaxError as exc:
			frappe.throw(_("Condition has a syntax error: {0}").format(str(exc)))

	def validate_target_field(self):
		if not self.target_field or not self.document_type:
			return
		if not frappe.get_meta(self.document_type).has_field(self.target_field):
			frappe.throw(_("{0} has no field named {1}.").format(self.document_type, self.target_field))

	def on_update(self):
		from ai_fr_hg.ai.automation import clear_rule_cache

		clear_rule_cache()

	def on_trash(self):
		from ai_fr_hg.ai.automation import clear_rule_cache

		clear_rule_cache()

	@frappe.whitelist()
	def test_rule(self, docname: str) -> dict:
		"""Run this rule against an existing document."""
		from ai_fr_hg.ai.automation import execute_rule

		return execute_rule(self.name, self.document_type, docname)
