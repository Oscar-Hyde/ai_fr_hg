# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class AISearchQuery(Document):
	_DOCTYPE_NAME = "AI Search Query"

	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		duration_ms: DF.Int
		knowledge_base: DF.Link | None
		query: DF.SmallText
		result_count: DF.Int
		results: DF.Code | None
		search_type: DF.Literal["Semantic", "Keyword", "Hybrid"]
		top_score: DF.Float
		user: DF.Link | None
	# end: auto-generated types
