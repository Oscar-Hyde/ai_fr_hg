# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class AIKnowledgeBaseRole(Document):
	_DOCTYPE_NAME = "AI Knowledge Base Role"

	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		can_write: DF.Check
		parent: DF.Data
		parentfield: DF.Data
		parenttype: DF.Data
		role: DF.Link
	# end: auto-generated types
