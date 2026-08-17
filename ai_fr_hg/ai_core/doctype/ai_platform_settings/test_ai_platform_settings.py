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

	def test_platform_metrics(self):
		from ai_fr_hg.ai.monitoring import get_platform_metrics

		metrics = get_platform_metrics()
		self.assertIn("providers", metrics)
		self.assertIn("knowledge", metrics)
		self.assertIn("activity_24h", metrics)
