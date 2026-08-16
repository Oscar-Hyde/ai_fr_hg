# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class AIAuditLog(Document):
	_DOCTYPE_NAME = "AI Audit Log"

	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		action: DF.Data
		category: DF.Literal["Access", "Configuration", "Execution", "Data", "Security"]
		details: DF.Code | None
		ip_address: DF.Data | None
		message: DF.SmallText | None
		reference_doctype: DF.Link | None
		reference_name: DF.DynamicLink | None
		severity: DF.Literal["Info", "Warning", "Critical"]
		site_user_agent: DF.SmallText | None
		user: DF.Link | None
	# end: auto-generated types
