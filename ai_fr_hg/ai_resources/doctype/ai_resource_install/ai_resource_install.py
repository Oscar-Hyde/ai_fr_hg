# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime


class AIResourceInstall(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		activated: DF.Check
		activated_on: DF.Datetime | None
		download: DF.Link | None
		health_status: DF.Literal["Unknown", "Healthy", "Degraded", "Error"]
		installed_by: DF.Link | None
		installed_on: DF.Datetime | None
		is_active: DF.Check
		last_checked: DF.Datetime | None
		last_used: DF.Datetime | None
		naming_series: DF.Literal["RESINS-.YYYY.-.#####"]
		package_size_mb: DF.Float
		previous_version: DF.Data | None
		publisher: DF.Data | None
		removed_by: DF.Link | None
		removed_on: DF.Datetime | None
		repository: DF.Link | None
		requires_update: DF.Check
		resource: DF.Link
		resource_code: DF.Data
		resource_name: DF.Data
		resource_type: DF.Literal[
			"Translation Package",
			"Translation Memory Pack",
			"AI Model",
			"AI Prompt Template",
			"AI Workflow Template",
			"Agent Capability",
			"Language Pack",
			"Knowledge Resource",
			"AI Extension",
		]
		sha256: DF.Data | None
		signature: DF.Data | None
		source_url: DF.Data | None
		status: DF.Literal[
			"Installing",
			"Registering",
			"Active",
			"Update Available",
			"Update Failed",
			"Rollback Pending",
			"Superseded",
			"Removed",
			"Failed",
		]
		target_records: DF.Code | None
		use_count: DF.Int
		version: DF.Data
	# end: auto-generated types

	def validate(self):
		if self.status == "Active":
			self.is_active = 1
			self.activated = 1

	def mark_used(self):
		"""Record a single actual usage of this resource."""
		self.use_count = (self.use_count or 0) + 1
		self.last_used = now_datetime()
		self.save()

	def remove(self):
		"""Deactivate this install and its target records."""
		from ai_fr_hg.ai.resources.lifecycle import uninstall_resource

		return uninstall_resource(self.name, user=frappe.session.user)
