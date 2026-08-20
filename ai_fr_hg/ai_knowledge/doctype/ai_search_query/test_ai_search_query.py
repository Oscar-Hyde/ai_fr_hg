# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""SEC-07: search telemetry is redacted, bounded, and policy-controlled."""

import json

import frappe
from frappe.utils import cint

from ai_fr_hg.tests.integration_test_case import AIPlatformTestCase


class TestSearchTelemetry(AIPlatformTestCase):
	def setUp(self):
		self.settings = frappe.get_doc("AI Platform Settings")
		self.previous_patterns = self.settings.redact_patterns
		self.previous_flag = cint(
			frappe.db.get_single_value("AI Platform Settings", "log_search_queries", cache=False) or 1
		)

	def tearDown(self):
		self.settings.db_set("redact_patterns", self.previous_patterns)
		self.settings.db_set("log_search_queries", 1 if self.previous_flag else 0)

	def log_search(self, query, results):
		from ai_fr_hg.ai.knowledge import _log_search_job

		_log_search_job(
			query,
			[self.knowledge_base.name],
			"Hybrid",
			results,
			result_count=len(results),
			top_score=results[0]["score"] if results else 0,
			duration_ms=12,
			user="Administrator",
		)

	def newest_query_row(self):
		return frappe.get_all(
			"AI Search Query",
			fields=["name", "query", "results"],
			order_by="creation desc",
			limit=1,
		)[0]

	def test_query_and_snippets_are_redacted_before_persistence(self):
		self.settings.db_set("redact_patterns", r"client-\d{6}\b")
		secret = "client-123456"
		content = f"The account {secret} holds the balance. " + "padding words " * 40
		self.log_search(
			f"what does {secret} owe?",
			[
				{
					"chunk": "CHUNK-1",
					"document": "DOC-1",
					"document_title": f"Statement {secret}",
					"content": content,
					"score": 0.91,
				}
			],
		)

		row = self.newest_query_row()
		self.assertNotIn(secret, row.query)
		self.assertIn("[REDACTED]", row.query)

		stored = json.loads(row.results)
		self.assertNotIn(secret, json.dumps(stored))
		self.assertLessEqual(len(stored[0]["snippet"]), 200)
		self.assertLess(len(stored[0]["snippet"]), len(content), "full content must never be stored")
		self.assertEqual(stored[0]["chunk"], "CHUNK-1")
		self.assertEqual(stored[0]["document"], "DOC-1")

	def test_full_result_content_is_never_persisted(self):
		content = "SENTENCE. " * 300  # far beyond the snippet budget
		self.log_search(
			"plain query",
			[
				{
					"chunk": "CHUNK-2",
					"document": "DOC-2",
					"document_title": "Long",
					"content": content,
					"score": 0.5,
				}
			],
		)
		stored = json.loads(self.newest_query_row().results)
		self.assertLessEqual(len(stored[0]["snippet"]), 200)
		self.assertLess(len(stored[0]["snippet"]), len(content))
		self.assertNotIn(content, stored[0]["snippet"])

	def test_telemetry_can_be_disabled(self):
		self.settings.db_set("log_search_queries", 0)
		before = frappe.db.count("AI Search Query")
		self.log_search("do not store", [])
		self.assertEqual(frappe.db.count("AI Search Query"), before)

	def test_retention_window_is_registered_with_frappe(self):
		# Frappe's native log-clearing mechanism owns the retention window.
		from ai_fr_hg.hooks import default_log_clearing_doctypes

		self.assertEqual(default_log_clearing_doctypes.get("AI Search Query"), 30)
