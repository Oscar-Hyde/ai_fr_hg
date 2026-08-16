# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class AITool(Document):
	_DOCTYPE_NAME = "AI Tool"

	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		from ai_fr_hg.ai_conversation.doctype.ai_tool_parameter.ai_tool_parameter import AIToolParameter
		from ai_fr_hg.ai_conversation.doctype.ai_tool_role.ai_tool_role import AIToolRole

		allowed_roles: DF.Table[AIToolRole]
		description: DF.SmallText
		enabled: DF.Check
		handler: DF.Data | None
		is_readonly_tool: DF.Check
		json_schema: DF.Code | None
		max_runtime_seconds: DF.Int
		parameters: DF.Table[AIToolParameter]
		pipeline: DF.Link | None
		requires_approval: DF.Check
		target_doctype: DF.Link | None
		target_report: DF.Link | None
		tool_name: DF.Data
		tool_type: DF.Literal[
			"Builtin", "DocType Query", "DocType Action", "Report", "Server Method", "Pipeline"
		]
	# end: auto-generated types

	def validate(self):
		self.validate_name()
		self.validate_target()
		self.json_schema = frappe.as_json(self.build_schema())

	def validate_name(self):
		"""Model runtimes require snake_case function names."""
		import re

		if not re.fullmatch(r"[a-z][a-z0-9_]{1,63}", self.tool_name or ""):
			frappe.throw(
				_(
					"Tool Name must be lowercase snake_case, 2-64 characters, "
					"starting with a letter. Example: search_knowledge_base"
				)
			)

	def validate_target(self):
		required = {
			"Builtin": "handler",
			"Server Method": "handler",
			"DocType Query": "target_doctype",
			"DocType Action": "target_doctype",
			"Report": "target_report",
			"Pipeline": "pipeline",
		}.get(self.tool_type)

		if required and not self.get(required):
			frappe.throw(
				_("{0} is required for a {1} tool.").format(_(self.meta.get_label(required)), self.tool_type)
			)

		if self.tool_type == "DocType Action":
			self.is_readonly_tool = 0
		if self.tool_type == "Builtin" and self.handler:
			from ai_fr_hg.ai.tools import get_builtin_handlers

			if self.handler not in get_builtin_handlers():
				frappe.throw(
					_("{0} is not a known built-in handler. Available: {1}").format(
						self.handler, ", ".join(sorted(get_builtin_handlers()))
					)
				)

	def build_schema(self) -> dict:
		from ai_fr_hg.ai.tools import build_tool_schema

		return build_tool_schema(self)

	def autoname(self):
		self.name = self.tool_name
