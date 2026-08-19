# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class AIFolderFavorite(Document):
	_DOCTYPE_NAME = "AI Folder Favorite"

	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		folder: DF.Link
		user: DF.Link
	# end: auto-generated types

	def validate(self):
		if not frappe.db.exists("File", self.folder):
			frappe.throw(_("Folder '{0}' does not exist.").format(self.folder))
		is_folder = frappe.db.get_value("File", self.folder, "is_folder")
		if not is_folder:
			frappe.throw(_("'{0}' is not a folder.").format(self.folder))
		# Prevent duplicates per user
		existing = frappe.db.get_value(
			"AI Folder Favorite",
			{"user": self.user, "folder": self.folder, "name": ["!=", self.name or ""]},
			"name",
		)
		if existing:
			frappe.throw(_("Folder '{0}' is already in your favorites.").format(self.folder))

	def before_insert(self):
		if not self.user:
			self.user = frappe.session.user

	def has_permission(self, ptype=None, user=None, debug=False):
		user = user or frappe.session.user
		if user in {"Administrator"} or "System Manager" in frappe.get_roles(user):
			return True
		# Users can only manage their own favorites
		if self.user:
			return self.user == user
		return True
