# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime


class AIResourceRepository(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		description: DF.SmallText | None
		enabled: DF.Check
		is_builtin: DF.Check
		is_default: DF.Check
		last_synced: DF.Datetime | None
		offline_supported: DF.Check
		priority: DF.Int
		repository_name: DF.Data
		repository_type: DF.Literal["Built-in", "HTTP", "File", "Enterprise"]
		requires_authorization: DF.Check
		source_url: DF.Data | None
	# end: auto-generated types

	def validate(self):
		self.repository_name = (self.repository_name or "").strip()

	def sync(self):
		"""Refresh the catalog from this repository (invoked through the API shell)."""
		from ai_fr_hg.ai.resources.catalog import refresh_builtin_catalog

		if not self.is_builtin:
			frappe.throw("Only the built-in repository auto-syncs in this release.")
		result = refresh_builtin_catalog(user=frappe.session.user)
		frappe.db.set_value("AI Resource Repository", self.name, "last_synced", now_datetime())
		return result
