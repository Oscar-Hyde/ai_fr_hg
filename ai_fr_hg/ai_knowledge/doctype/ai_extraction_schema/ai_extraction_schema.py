# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class AIExtractionSchema(Document):
	_DOCTYPE_NAME = "AI Extraction Schema"

	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		from ai_fr_hg.ai_knowledge.doctype.ai_extraction_field.ai_extraction_field import AIExtractionField

		description: DF.SmallText | None
		enabled: DF.Check
		extraction_fields: DF.Table[AIExtractionField]
		instructions: DF.Text | None
		json_schema: DF.Code | None
		model: DF.Link | None
		schema_name: DF.Data
		strict: DF.Check
		target_doctype: DF.Link | None
	# end: auto-generated types

	def validate(self):
		self.validate_field_names()
		self.json_schema = frappe.as_json(self.build_schema())

	def validate_field_names(self):
		seen = set()
		for row in self.extraction_fields:
			name = (row.field_name or "").strip()
			if not name:
				frappe.throw(_("Row {0}: Field Name is required.").format(row.idx))
			if not name.replace("_", "").isalnum():
				frappe.throw(
					_("Row {0}: Field Name {1} may only contain letters, numbers and underscores.").format(
						row.idx, name
					)
				)
			if name in seen:
				frappe.throw(_("Row {0}: Field Name {1} is duplicated.").format(row.idx, name))
			seen.add(name)
			row.field_name = name

	def build_schema(self) -> dict:
		from ai_fr_hg.ai.intelligence import build_json_schema

		return build_json_schema(self)

	@frappe.whitelist()
	def test_extraction(self, text: str) -> dict:
		"""Run this schema against sample text."""
		from ai_fr_hg.ai.intelligence import extract_data

		return extract_data(text, schema=self.name)
