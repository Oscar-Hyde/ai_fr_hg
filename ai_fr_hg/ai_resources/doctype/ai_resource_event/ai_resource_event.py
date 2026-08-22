# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class AIResourceEvent(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		action: DF.Data
		details: DF.Code | None
		download: DF.Link | None
		install: DF.Link | None
		message: DF.SmallText
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
		severity: DF.Literal["Info", "Warning", "Critical"]
		user: DF.Link | None
		version: DF.Data | None
	# end: auto-generated types

	def validate(self):
		self.action = (self.action or "").strip()
		self.message = (self.message or "").strip()
		if self.download:
			self.resource = self.resource or frappe.db.get_value("AI Resource Download", self.download, "resource")

	def after_insert(self):
		if not self.user:
			self.user = frappe.session.user
			self.db_set("user", frappe.session.user, update_modified=False)
