# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class AIServiceHealthLog(Document):
	_DOCTYPE_NAME = "AI Service Health Log"

	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		available_models: DF.Int
		checked_on: DF.Datetime | None
		details: DF.Code | None
		error_message: DF.SmallText | None
		latency_ms: DF.Int
		model: DF.Link | None
		provider: DF.Link | None
		status: DF.Literal["Online", "Degraded", "Offline"]
	# end: auto-generated types
