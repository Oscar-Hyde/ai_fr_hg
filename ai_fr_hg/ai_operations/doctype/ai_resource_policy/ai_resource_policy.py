# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint


class AIResourcePolicy(Document):
	_DOCTYPE_NAME = "AI Resource Policy"

	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		from ai_fr_hg.ai_operations.doctype.ai_policy_model.ai_policy_model import AIPolicyModel

		allow_document_upload: DF.Check
		allow_model_management: DF.Check
		allow_pipeline_execution: DF.Check
		allow_tools: DF.Check
		allowed_models: DF.Table[AIPolicyModel]
		enabled: DF.Check
		max_concurrent_requests: DF.Int
		max_documents_per_day: DF.Int
		max_requests_per_hour: DF.Int
		max_tokens_per_day: DF.Int
		notes: DF.SmallText | None
		policy_name: DF.Data
		priority: DF.Int
		role: DF.Link | None
		user: DF.Link | None
	# end: auto-generated types

	def validate(self):
		if not self.role and not self.user:
			frappe.throw(_("A policy must apply to either a role or a user."))
		if self.role and self.user:
			frappe.throw(_("A policy applies to either a role or a user, not both."))

		for field in (
			"max_requests_per_hour",
			"max_tokens_per_day",
			"max_documents_per_day",
			"max_concurrent_requests",
		):
			if cint(self.get(field)) < 0:
				frappe.throw(_("{0} cannot be negative.").format(_(self.meta.get_label(field))))
