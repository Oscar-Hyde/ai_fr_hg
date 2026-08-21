# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Frappe integration coverage for this DocType and its canonical domain services."""

from unittest.mock import patch

import frappe

from ai_fr_hg.tests.integration_test_case import AIPlatformTestCase


class TestLogging(AIPlatformTestCase):
	def test_redaction_masks_configured_patterns(self):
		from ai_fr_hg.ai.logging import clear_pattern_cache, redact

		settings = frappe.get_single("AI Platform Settings")
		original = settings.redact_patterns
		settings.redact_patterns = r"\b\d{4}[- ]?\d{4}[- ]?\d{4}[- ]?\d{4}\b"
		settings.save(ignore_permissions=True)
		clear_pattern_cache()

		try:
			redacted = redact("Card 4111 1111 1111 1111 was used.")
			self.assertNotIn("4111 1111 1111 1111", redacted)
			self.assertIn("REDACTED", redacted)
		finally:
			settings.redact_patterns = original
			settings.save(ignore_permissions=True)
			clear_pattern_cache()

	def test_malformed_embedding_closes_execution_log_as_failed(self):
		from types import SimpleNamespace

		from ai_fr_hg.ai.engine import run_embedding
		from ai_fr_hg.ai.exceptions import ProviderError

		document = self.make_document("Malformed Embedding Audit", "Audit provider failures.")
		provider = SimpleNamespace(embed=lambda texts, model: [[1.0, 2.0]])
		with (
			patch(
				"ai_fr_hg.ai.engine.get_settings",
				# `request_timeout` bounds the GOV-01 concurrency lease the
				# embedding path now takes, so the stub must carry it.
				return_value=SimpleNamespace(platform_enabled=1, request_timeout=120),
			),
			patch("ai_fr_hg.ai.engine.get_provider", return_value=provider),
			self.assertRaises(ProviderError),
		):
			run_embedding(
				["first", "second"],
				model=self.embedding_model.name,
				reference_doctype="AI Document",
				reference_name=document.name,
			)

		log = frappe.get_all(
			"AI Execution Log",
			filters={
				"operation": "Embedding",
				"reference_doctype": "AI Document",
				"reference_name": document.name,
			},
			fields=["status", "error_message", "finished_at"],
			order_by="creation desc",
			limit=1,
		)[0]
		self.assertEqual(log.status, "Failed")
		self.assertTrue(log.error_message)
		self.assertTrue(log.finished_at)

	def test_audit_log_is_written(self):
		from ai_fr_hg.ai.logging import write_audit_log

		write_audit_log(
			action="Unit Test Action",
			category="Configuration",
			message="Written by the test suite.",
		)
		self.assertTrue(frappe.db.exists("AI Audit Log", {"action": "Unit Test Action"}))


class TestExecutionLogAPI(AIPlatformTestCase):
	def test_log_purge_rejects_non_positive_retention(self):
		from ai_fr_hg.api.admin import purge_logs

		for days in (0, -1):
			with self.assertRaises(frappe.ValidationError):
				purge_logs("AI Execution Log", days)
