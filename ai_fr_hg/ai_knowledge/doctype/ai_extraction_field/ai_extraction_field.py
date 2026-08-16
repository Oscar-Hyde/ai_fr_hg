# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class AIExtractionField(Document):
	_DOCTYPE_NAME = "AI Extraction Field"

	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		description: DF.SmallText | None
		enum_values: DF.SmallText | None
		field_name: DF.Data
		field_type: DF.Literal["String", "Number", "Integer", "Boolean", "Date", "Array", "Object"]
		label: DF.Data | None
		parent: DF.Data
		parentfield: DF.Data
		parenttype: DF.Data
		required: DF.Check
	# end: auto-generated types
