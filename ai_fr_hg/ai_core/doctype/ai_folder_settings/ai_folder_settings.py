# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class AIFolderSettings(Document):
	_DOCTYPE_NAME = "AI Folder Settings"

	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		access_policy: DF.Link | None
		color: DF.Data | None
		description: DF.SmallText | None
		folder: DF.Link
		is_archived: DF.Check
		knowledge_base: DF.Link | None
		knowledge_tag: DF.Data | None
		last_operation: DF.Data | None
		last_operation_by: DF.Link | None
		last_operation_on: DF.Datetime | None
	# end: auto-generated types

	def validate(self):
		self.validate_folder()

	def validate_folder(self):
		if not self.folder:
			frappe.throw(_("Folder is required."))
		if not frappe.db.exists("File", self.folder):
			frappe.throw(_("Folder '{0}' does not exist.").format(self.folder))
		is_folder = frappe.db.get_value("File", self.folder, "is_folder")
		if not is_folder:
			frappe.throw(_("'{0}' is not a folder.").format(self.folder))

	def before_save(self):
		# Ensure the user can write the underlying File folder
		if not frappe.has_permission("File", "write", doc=frappe.get_doc("File", self.folder)):
			frappe.throw(
				_("You cannot edit settings for folder '{0}' without write permission.").format(self.folder),
				frappe.PermissionError,
			)
