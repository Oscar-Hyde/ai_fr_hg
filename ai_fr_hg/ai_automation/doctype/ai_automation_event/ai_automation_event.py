# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class AIAutomationEvent(Document):
	_DOCTYPE_NAME = "AI Automation Event"

	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		document_modified: DF.Datetime | None
		error_message: DF.SmallText | None
		event: DF.Literal["after_insert", "on_update", "on_submit", "on_cancel", "on_trash"]
		finished_on: DF.Datetime | None
		requested_by: DF.Link | None
		revision_key: DF.Data | None
		rule: DF.Link
		snapshot: DF.Code | None
		source_doctype: DF.Data
		source_name: DF.Data
		started_on: DF.Datetime | None
		status: DF.Literal["Queued", "Running", "Success", "Failed", "Skipped", "Coalesced"]
	# end: auto-generated types
