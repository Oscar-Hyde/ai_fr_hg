# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class AIModelVersion(Document):
	_DOCTYPE_NAME = "AI Model Version"

	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		digest: DF.Data | None
		is_active: DF.Check
		notes: DF.SmallText | None
		parent: DF.Data
		parentfield: DF.Data
		parenttype: DF.Data
		pulled_on: DF.Datetime | None
		size_bytes: DF.Float
		version: DF.Data
	# end: auto-generated types
