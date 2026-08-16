# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class AIAgentKnowledgeBase(Document):
	_DOCTYPE_NAME = "AI Agent Knowledge Base"

	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		knowledge_base: DF.Link
		parent: DF.Data
		parentfield: DF.Data
		parenttype: DF.Data
		weight: DF.Float
	# end: auto-generated types
