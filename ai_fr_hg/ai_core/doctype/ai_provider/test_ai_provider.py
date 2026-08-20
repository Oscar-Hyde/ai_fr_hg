# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Frappe integration coverage for this DocType and its canonical domain services."""

from unittest.mock import patch

import frappe

from ai_fr_hg.tests.integration_test_case import AIPlatformTestCase


class TestProviderDocType(AIPlatformTestCase):
	def test_provider_is_created(self):
		self.assertTrue(frappe.db.exists("AI Provider", "Test Provider"))

	def test_base_url_must_be_http(self):
		doc = frappe.get_doc(
			{
				"doctype": "AI Provider",
				"provider_name": "Bad URL Provider",
				"provider_type": "Ollama",
				"base_url": "ftp://localhost:11434",
			}
		)
		with self.assertRaises(frappe.ValidationError):
			doc.insert(ignore_permissions=True)

	def test_trailing_slash_is_stripped(self):
		doc = frappe.get_doc(
			{
				"doctype": "AI Provider",
				"provider_name": "Slash Provider",
				"provider_type": "Ollama",
				"base_url": "http://localhost:11434/",
			}
		)
		doc.insert(ignore_permissions=True)
		self.assertEqual(doc.base_url, "http://localhost:11434")

	def test_invalid_extra_headers_rejected(self):
		doc = frappe.get_doc(
			{
				"doctype": "AI Provider",
				"provider_name": "Header Provider",
				"provider_type": "Ollama",
				"base_url": "http://localhost:11434",
				"extra_headers": "not json",
			}
		)
		with self.assertRaises(frappe.ValidationError):
			doc.insert(ignore_permissions=True)

	def test_only_one_default_provider(self):
		first = frappe.get_doc(
			{
				"doctype": "AI Provider",
				"provider_name": "Default One",
				"provider_type": "Ollama",
				"base_url": "http://localhost:11434",
				"is_default": 1,
			}
		).insert(ignore_permissions=True)

		second = frappe.get_doc(
			{
				"doctype": "AI Provider",
				"provider_name": "Default Two",
				"provider_type": "Ollama",
				"base_url": "http://localhost:11435",
				"is_default": 1,
			}
		).insert(ignore_permissions=True)

		self.assertEqual(frappe.db.get_value("AI Provider", first.name, "is_default"), 0)
		self.assertEqual(frappe.db.get_value("AI Provider", second.name, "is_default"), 1)


class TestProviderAPI(AIPlatformTestCase):
	def test_model_pull_records_durable_lifecycle_and_worker_failure(self):
		from types import SimpleNamespace

		from ai_fr_hg.api.admin import _pull_model_job

		adapter = SimpleNamespace(pull_model=lambda model: None)
		with (
			patch("ai_fr_hg.ai.providers.get_provider", return_value=adapter),
			patch("ai_fr_hg.ai.monitoring.sync_provider_models", return_value={"created": ["test"]}),
			patch("ai_fr_hg.ai.logging.write_audit_log") as audit,
			patch.object(frappe.db, "commit") as commit,
			patch("frappe.publish_realtime"),
		):
			_pull_model_job(self.provider.name, "test-pull-model", "Administrator")

		self.assertEqual(
			[item.kwargs["action"] for item in audit.call_args_list], ["Model Pull Started", "Model Pulled"]
		)
		self.assertTrue(all(item.kwargs["raise_on_error"] for item in audit.call_args_list))
		self.assertEqual(commit.call_count, 2)

		failing_adapter = SimpleNamespace(
			pull_model=lambda model: (_ for _ in ()).throw(RuntimeError("pull failed"))
		)
		with (
			patch("ai_fr_hg.ai.providers.get_provider", return_value=failing_adapter),
			patch("ai_fr_hg.ai.logging.write_audit_log") as failed_audit,
			patch.object(frappe.db, "commit") as failed_commit,
			patch.object(frappe.db, "rollback") as rollback,
			patch("frappe.publish_realtime"),
			patch("frappe.log_error"),
			self.assertRaisesRegex(RuntimeError, "pull failed"),
		):
			_pull_model_job(self.provider.name, "test-failing-pull-model", "Administrator")

		self.assertEqual(
			[item.kwargs["action"] for item in failed_audit.call_args_list],
			["Model Pull Started", "Model Pull Failed"],
		)
		self.assertTrue(all(item.kwargs["raise_on_error"] for item in failed_audit.call_args_list))
		self.assertEqual(failed_commit.call_count, 2)
		rollback.assert_called_once_with()
