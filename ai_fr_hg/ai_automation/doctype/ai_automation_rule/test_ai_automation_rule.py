# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Frappe integration coverage for this DocType and its canonical domain services."""

import frappe

from ai_fr_hg.tests.integration_test_case import AIPlatformTestCase


class TestAutomationRule(AIPlatformTestCase):
	def test_rule_cannot_target_platform_doctypes(self):
		doc = frappe.get_doc(
			{
				"doctype": "AI Automation Rule",
				"rule_name": "Recursive Rule",
				"document_type": "AI Document",
				"event": "on_update",
				"action_type": "Summarize",
			}
		)
		with self.assertRaises(frappe.ValidationError):
			doc.insert(ignore_permissions=True)

	def test_invalid_condition_rejected(self):
		doc = frappe.get_doc(
			{
				"doctype": "AI Automation Rule",
				"rule_name": "Bad Condition Rule",
				"document_type": "ToDo",
				"event": "on_update",
				"action_type": "Summarize",
				"condition": "doc.status ==",
			}
		)
		with self.assertRaises(frappe.ValidationError):
			doc.insert(ignore_permissions=True)

	def test_unknown_target_field_rejected(self):
		doc = frappe.get_doc(
			{
				"doctype": "AI Automation Rule",
				"rule_name": "Bad Target Rule",
				"document_type": "ToDo",
				"event": "on_update",
				"action_type": "Summarize",
				"target_field": "nonexistent_field",
			}
		)
		with self.assertRaises(frappe.ValidationError):
			doc.insert(ignore_permissions=True)

	def test_unknown_source_field_rejected(self):
		doc = frappe.get_doc(
			{
				"doctype": "AI Automation Rule",
				"rule_name": "Bad Source Rule",
				"document_type": "ToDo",
				"event": "on_update",
				"action_type": "Summarize",
				"source_field": "nonexistent_field",
			}
		)
		with self.assertRaises(frappe.ValidationError):
			doc.insert(ignore_permissions=True)

	def test_delete_event_cannot_write_target_field(self):
		doc = frappe.get_doc(
			{
				"doctype": "AI Automation Rule",
				"rule_name": "Trash Target Rule",
				"document_type": "ToDo",
				"event": "on_trash",
				"action_type": "Summarize",
				"target_field": "description",
			}
		)
		with self.assertRaises(frappe.ValidationError):
			doc.insert(ignore_permissions=True)

	def test_delete_event_uses_snapshot(self):
		from unittest.mock import patch

		from ai_fr_hg.ai.automation import execute_rule, trigger_rule

		todo = frappe.get_doc({"doctype": "ToDo", "description": "snapshot-secret"}).insert(
			ignore_permissions=True
		)
		rule = frappe.get_doc(
			{
				"doctype": "AI Automation Rule",
				"rule_name": "Trash Snapshot Rule",
				"document_type": "ToDo",
				"event": "on_trash",
				"action_type": "Summarize",
				"source_field": "description",
				"enabled": 1,
			}
		).insert(ignore_permissions=True)
		event = trigger_rule(rule.name, todo, method="on_trash", enqueue=False)
		todo.delete(ignore_permissions=True)
		self.assertFalse(frappe.db.exists("ToDo", todo.name))
		with patch("ai_fr_hg.ai.intelligence.summarize", return_value="ok") as mocked:
			outcome = execute_rule(rule.name, "ToDo", todo.name, event_name=event["event"])
		self.assertEqual(outcome["status"], "Success")
		mocked.assert_called()
		self.assertIn("snapshot-secret", mocked.call_args.args[0])
		event_doc = frappe.get_doc("AI Automation Event", event["event"])
		self.assertEqual(event_doc.status, "Success")
		self.assertIn("snapshot-secret", event_doc.snapshot)

	def test_counters_increment_atomically(self):
		from ai_fr_hg.ai.automation import _record_success

		rule = frappe.get_doc(
			{
				"doctype": "AI Automation Rule",
				"rule_name": "Counter Rule",
				"document_type": "ToDo",
				"event": "on_update",
				"action_type": "Summarize",
				"enabled": 1,
			}
		).insert(ignore_permissions=True)
		for _ in range(7):
			_record_success(rule.name)
		self.assertEqual(frappe.db.get_value("AI Automation Rule", rule.name, "run_count"), 7)

	def test_exact_revision_is_deduped(self):
		from ai_fr_hg.ai.automation import trigger_rule

		todo = frappe.get_doc({"doctype": "ToDo", "description": "once"}).insert(ignore_permissions=True)
		rule = frappe.get_doc(
			{
				"doctype": "AI Automation Rule",
				"rule_name": "Dedupe Rule",
				"document_type": "ToDo",
				"event": "on_update",
				"action_type": "Summarize",
				"enabled": 1,
				"coalesce_events": 0,
			}
		).insert(ignore_permissions=True)
		first = trigger_rule(rule.name, todo, method="on_update", enqueue=False)
		second = trigger_rule(rule.name, todo, method="on_update", enqueue=False)
		self.assertFalse(first.get("skipped"))
		self.assertTrue(second.get("skipped"))
		self.assertEqual(second.get("reason"), "duplicate_revision")
