# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Frappe integration coverage for this DocType and its canonical domain services."""

import frappe

from ai_fr_hg.tests.integration_test_case import AIPlatformTestCase


class TestExtractionSchema(AIPlatformTestCase):
	def test_json_schema_is_generated(self):
		doc = frappe.get_doc(
			{
				"doctype": "AI Extraction Schema",
				"schema_name": "Test Invoice Schema",
				"enabled": 1,
				"extraction_fields": [
					{"field_name": "invoice_number", "field_type": "String", "required": 1},
					{"field_name": "total", "field_type": "Number"},
					{"field_name": "paid", "field_type": "Boolean"},
				],
			}
		)
		doc.insert(ignore_permissions=True)

		schema = frappe.parse_json(doc.json_schema)
		self.assertEqual(schema["type"], "object")
		self.assertEqual(schema["properties"]["invoice_number"]["type"], "string")
		self.assertEqual(schema["properties"]["total"]["type"], "number")
		self.assertEqual(schema["properties"]["paid"]["type"], "boolean")
		self.assertIn("invoice_number", schema["required"])

	def test_duplicate_field_names_rejected(self):
		doc = frappe.get_doc(
			{
				"doctype": "AI Extraction Schema",
				"schema_name": "Duplicate Field Schema",
				"extraction_fields": [
					{"field_name": "amount", "field_type": "Number"},
					{"field_name": "amount", "field_type": "String"},
				],
			}
		)
		with self.assertRaises(frappe.ValidationError):
			doc.insert(ignore_permissions=True)

	def test_invalid_field_name_rejected(self):
		doc = frappe.get_doc(
			{
				"doctype": "AI Extraction Schema",
				"schema_name": "Invalid Field Schema",
				"extraction_fields": [{"field_name": "total amount!", "field_type": "Number"}],
			}
		)
		with self.assertRaises(frappe.ValidationError):
			doc.insert(ignore_permissions=True)
