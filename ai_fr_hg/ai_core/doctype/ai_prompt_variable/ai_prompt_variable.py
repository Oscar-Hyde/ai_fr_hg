# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class AIPromptVariable(Document):
	_DOCTYPE_NAME = "AI Prompt Variable"

	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		default_value: DF.Data | None
		description: DF.SmallText | None
		label: DF.Data | None
		parent: DF.Data
		parentfield: DF.Data
		parenttype: DF.Data
		required: DF.Check
		variable: DF.Data
		variable_type: DF.Literal["String", "Number", "Boolean", "JSON", "Document"]
	# end: auto-generated types
