# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Frappe integration coverage for this DocType and its canonical domain services."""

import frappe

from ai_fr_hg.tests.integration_test_case import AIPlatformTestCase


class TestPlatformSettingsAPI(AIPlatformTestCase):
	def test_get_system_status(self):
		from ai_fr_hg.api.admin import get_system_status

		status = get_system_status()
		self.assertIn("checks", status)
		self.assertTrue(all("label" in check for check in status["checks"]))

	def test_system_status_rejects_authenticated_non_manager(self):
		from ai_fr_hg.api.admin import get_system_status

		user = frappe.get_doc(
			{
				"doctype": "User",
				"email": "ai-platform-non-manager@example.com",
				"first_name": "AI Platform Non Manager",
				"enabled": 1,
				"send_welcome_email": 0,
			}
		).insert(ignore_permissions=True)
		user.add_roles("AI User")
		frappe.set_user(user.name)
		try:
			with self.assertRaises(frappe.PermissionError):
				get_system_status()
		finally:
			frappe.set_user("Administrator")

	def test_default_chat_model_rejects_embedding_model(self):
		settings = frappe.get_single("AI Platform Settings")
		settings.default_chat_model = self.embedding_model.name
		with self.assertRaises(frappe.ValidationError):
			settings.validate_model_types()

	def test_platform_metrics(self):
		from ai_fr_hg.ai.monitoring import get_platform_metrics

		metrics = get_platform_metrics()
		self.assertIn("providers", metrics)
		self.assertIn("knowledge", metrics)
		self.assertIn("activity_24h", metrics)

	def test_application_level_encryption_cannot_be_enabled(self):
		settings = frappe.get_single("AI Platform Settings")
		settings.encrypt_documents = 1

		with self.assertRaises(frappe.ValidationError):
			settings.validate_unsupported_settings()

	def test_unsupported_control_patch_is_idempotent_and_preserves_legacy_model(self):
		from ai_fr_hg.patches.v0_0_14_disable_unsupported_controls import execute

		frappe.db.set_single_value("AI Platform Settings", "encrypt_documents", 1)
		frappe.db.set_value(
			"AI Model",
			self.chat_model.name,
			{"model_type": "Reranker", "enabled": 1, "is_default": 1},
			update_modified=False,
		)

		execute()
		execute()

		self.assertEqual(frappe.db.get_single_value("AI Platform Settings", "encrypt_documents"), 0)
		legacy = frappe.db.get_value(
			"AI Model",
			self.chat_model.name,
			["model_type", "enabled", "is_default", "last_error"],
			as_dict=True,
		)
		self.assertEqual(legacy.model_type, "Reranker")
		self.assertEqual(legacy.enabled, 0)
		self.assertEqual(legacy.is_default, 0)
		self.assertIn("no supported execution path", legacy.last_error)


class TestStorageFolderSetting(AIPlatformTestCase):
	"""FILE-04: the storage folder is a real File folder identity, validated."""

	def test_storage_folder_must_be_a_real_folder(self):
		settings = frappe.get_single("AI Platform Settings")
		previous = settings.storage_folder

		settings.storage_folder = "NoSuchFolderAnywhere"
		with self.assertRaises(frappe.ValidationError):
			settings.validate_storage_folder()

		# A File record that is not a folder is also rejected.
		file_doc = frappe.get_doc(
			{
				"doctype": "File",
				"file_name": "storage-folder-validation.txt",
				"file_url": "/files/storage-folder-validation.txt",
				"is_folder": 0,
				"folder": "Home",
			}
		)
		file_doc.insert(ignore_permissions=True)
		try:
			settings.storage_folder = file_doc.name
			with self.assertRaises(frappe.ValidationError):
				settings.validate_storage_folder()
		finally:
			frappe.delete_doc("File", file_doc.name, force=True, ignore_permissions=True)

		# Restore previous value for isolation.
		settings.db_set("storage_folder", previous)

	def test_default_folder_requires_write_access_on_the_configured_folder(self):
		from ai_fr_hg.ai.folders import get_default_folder

		private_folder = frappe.get_doc(
			{
				"doctype": "File",
				"file_name": "ManagerOnlyStorage",
				"is_folder": 1,
				"folder": "Home",
				"is_private": 1,
			}
		)
		private_folder.insert(ignore_permissions=True)
		settings = frappe.get_single("AI Platform Settings")
		previous = settings.storage_folder
		settings.db_set("storage_folder", private_folder.name)

		user = frappe.get_doc(
			{
				"doctype": "User",
				"email": "storage-folder-user@example.com",
				"first_name": "Storage Folder User",
				"enabled": 1,
				"send_welcome_email": 0,
				"roles": [{"role": "AI User"}],
			}
		).insert(ignore_permissions=True)
		try:
			frappe.set_user(user.name)
			# The configured folder is manager-owned/private; the default must
			# not silently point an AI User at it.
			chosen = get_default_folder(user=user.name)
			self.assertNotEqual(chosen, private_folder.name)
			self.assertTrue(chosen.startswith("Home"))
		finally:
			frappe.set_user("Administrator")
			settings.db_set("storage_folder", previous)
			frappe.delete_doc("File", private_folder.name, force=True, ignore_permissions=True)


class TestStorageFolderSchemaContract(AIPlatformTestCase):
	"""FILE-04: the storage folder schema must not re-introduce the legacy shape.

	The historical default ``AI Platform`` (a bare name, not a File identity)
	broke installation once the setting gained server-side validation: the
	default flowed into the Single DocType on first save, before any folder
	existed. The schema contract now forbids that shape.
	"""

	def test_storage_folder_is_a_file_link_without_a_short_default(self):
		from frappe.model.meta import get_meta

		field = get_meta("AI Platform Settings").get_field("storage_folder")
		self.assertEqual(field.fieldtype, "Link")
		self.assertEqual(field.options, "File")
		self.assertIn(
			field.default,
			(None, "", "Home/AI Platform"),
			"the legacy short default 'AI Platform' must not return",
		)

	def test_legacy_short_storage_value_is_rejected_until_normalized(self):
		settings = frappe.get_single("AI Platform Settings")
		previous = settings.storage_folder
		settings.storage_folder = "AI Platform"
		with self.assertRaises(frappe.ValidationError):
			settings.validate_storage_folder()
		settings.db_set("storage_folder", previous)
