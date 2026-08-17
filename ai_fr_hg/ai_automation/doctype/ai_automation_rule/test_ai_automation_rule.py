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
