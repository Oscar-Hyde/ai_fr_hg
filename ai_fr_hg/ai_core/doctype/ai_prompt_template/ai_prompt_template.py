# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class AIPromptTemplate(Document):
	_DOCTYPE_NAME = "AI Prompt Template"

	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		from ai_fr_hg.ai_core.doctype.ai_prompt_variable.ai_prompt_variable import AIPromptVariable

		category: DF.Literal[
			"General", "Summarization", "Extraction", "Classification", "Comparison", "RAG", "Agent", "Custom"
		]
		description: DF.SmallText | None
		enabled: DF.Check
		json_schema: DF.Code | None
		model: DF.Link | None
		output_format: DF.Literal["Text", "Markdown", "JSON"]
		system_prompt: DF.Code | None
		template_name: DF.Data
		user_prompt: DF.Code
		variables: DF.Table[AIPromptVariable]
	# end: auto-generated types

	def validate(self):
		self.validate_template()
		self.validate_schema()

	def validate_template(self):
		"""Fail at save time rather than at run time on a bad Jinja template."""
		from frappe.utils.jinja import get_jenv
		from jinja2 import TemplateSyntaxError

		jenv = get_jenv()
		for fieldname in ("system_prompt", "user_prompt"):
			source = self.get(fieldname)
			if not source:
				continue
			try:
				jenv.parse(source)
			except TemplateSyntaxError as exc:
				frappe.throw(
					_("{0} has a template error on line {1}: {2}").format(
						_(self.meta.get_label(fieldname)), exc.lineno, exc.message
					)
				)

	def validate_schema(self):
		if self.output_format != "JSON" or not self.json_schema:
			return
		import json

		try:
			parsed = json.loads(self.json_schema)
		except ValueError as exc:
			frappe.throw(_("JSON Schema is not valid JSON: {0}").format(str(exc)))
		if not isinstance(parsed, dict):
			frappe.throw(_("JSON Schema must be a JSON object."))

	@frappe.whitelist()
	def preview(self, context: str | None = None) -> dict:
		"""Render this template against sample values without calling a model."""
		import json

		from ai_fr_hg.ai.intelligence import render_prompt_template

		values = {}
		if context:
			try:
				values = json.loads(context)
			except ValueError:
				values = {}
		for row in self.variables or []:
			values.setdefault(row.variable, row.default_value or f"<{row.variable}>")

		return render_prompt_template(self.name, values)
