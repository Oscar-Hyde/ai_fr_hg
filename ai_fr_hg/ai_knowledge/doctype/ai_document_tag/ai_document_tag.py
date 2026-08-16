# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class AIDocumentTag(Document):
	_DOCTYPE_NAME = "AI Document Tag"

	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		parent: DF.Data
		parentfield: DF.Data
		parenttype: DF.Data
		score: DF.Float
		source: DF.Literal["Manual", "AI"]
		tag: DF.Data
	# end: auto-generated types
