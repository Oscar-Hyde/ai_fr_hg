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
