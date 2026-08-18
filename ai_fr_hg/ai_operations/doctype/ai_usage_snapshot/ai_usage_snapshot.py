# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class AIUsageSnapshot(Document):
	_DOCTYPE_NAME = "AI Usage Snapshot"

	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		average_latency_ms: DF.Float
		document_count: DF.Int
		failure_count: DF.Int
		model: DF.Link | None
		request_count: DF.Int
		snapshot_date: DF.Date
		total_tokens: DF.Float
		user: DF.Link | None
	# end: auto-generated types
