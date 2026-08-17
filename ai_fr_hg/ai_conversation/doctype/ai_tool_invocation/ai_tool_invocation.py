# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class AIToolInvocation(Document):
	_DOCTYPE_NAME = "AI Tool Invocation"

	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		agent: DF.Link | None
		approved_at: DF.Datetime | None
		approved_by: DF.Link | None
		arguments: DF.Code | None
		conversation: DF.Link | None
		duration_ms: DF.Int
		error_message: DF.SmallText | None
		finished_at: DF.Datetime | None
		message: DF.Link | None
		pipeline_run: DF.Link | None
		rejected_at: DF.Datetime | None
		rejected_by: DF.Link | None
		requested_at: DF.Datetime | None
		result: DF.Code | None
		started_at: DF.Datetime | None
		status: DF.Literal["Pending Approval", "Running", "Success", "Failed", "Rejected"]
		tool: DF.Link
		user: DF.Link | None
	# end: auto-generated types
