# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class AIToolParameter(Document):
	_DOCTYPE_NAME = "AI Tool Parameter"

	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		default_value: DF.Data | None
		description: DF.SmallText | None
		enum_values: DF.SmallText | None
		parameter: DF.Data
		parameter_type: DF.Literal["String", "Number", "Integer", "Boolean", "Array", "Object"]
		parent: DF.Data
		parentfield: DF.Data
		parenttype: DF.Data
		required: DF.Check
	# end: auto-generated types
