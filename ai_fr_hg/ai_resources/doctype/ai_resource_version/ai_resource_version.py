# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class AIResourceVersion(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		installed_on: DF.Datetime | None
		is_current: DF.Check
		is_installed: DF.Check
		publisher: DF.Data | None
		release_notes: DF.SmallText | None
		repository: DF.Link | None
		resource: DF.Link
		sha256: DF.Data | None
		signature: DF.Data | None
		size_mb: DF.Float
		source_url: DF.Data | None
		version: DF.Data
	# end: auto-generated types

	def validate(self):
		if not self.version:
			frappe.throw("Version is required.")
