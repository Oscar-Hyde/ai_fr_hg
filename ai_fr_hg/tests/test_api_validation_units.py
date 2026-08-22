# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Shared API validation bounds (limits, ids, lists, enums, idempotency).

These run inside the bench test context (Frappe provides ValidationError and
``frappe.throw``); no database rows are touched.
"""

try:
	from frappe.tests import UnitTestCase
except ImportError:
	from unittest import TestCase as UnitTestCase

import frappe

from ai_fr_hg.utils import api_validation as v


class TestBoundedScalars(UnitTestCase):
	def test_bounded_text_requires_and_caps(self):
		self.assertEqual(v.bounded_text(" ok ", label="Title", max_length=10), " ok ")
		with self.assertRaises(frappe.ValidationError):
			v.bounded_text("", label="Query", max_length=10, required=True)
		with self.assertRaises(frappe.ValidationError):
			v.bounded_text("x" * 11, label="Title", max_length=10)

	def test_bounded_integer_defaults_and_caps(self):
		self.assertEqual(v.bounded_integer(None, label="top_k", default=10, maximum=100), 10)
		self.assertEqual(v.bounded_integer(0, label="top_k", default=10, maximum=100), 10)
		self.assertEqual(v.bounded_integer(9999, label="top_k", default=10, maximum=100), 100)
		with self.assertRaises(frappe.ValidationError):
			v.bounded_integer(-1, label="limit", default=10, maximum=100)

	def test_pagination(self):
		limit, offset = v.pagination("50", "10", default_limit=20, hard_limit=100)
		self.assertEqual((limit, offset), (50, 10))
		limit, offset = v.pagination(99999, 5, default_limit=20, hard_limit=100)
		self.assertEqual(limit, 100)
		with self.assertRaises(frappe.ValidationError):
			v.pagination(10, -5, default_limit=20, hard_limit=100)

	def test_enum_choice(self):
		self.assertEqual(v.enum_choice("Hybrid", allowed=("Hybrid", "Semantic"), label="Type"), "Hybrid")
		self.assertIsNone(v.enum_choice(None, allowed=("Hybrid", "Semantic"), label="Type"))
		self.assertEqual(
			v.enum_choice(None, allowed=("Hybrid", "Semantic"), label="Type", default="Hybrid"), "Hybrid"
		)
		with self.assertRaises(frappe.ValidationError):
			v.enum_choice("Magic", allowed=("Hybrid", "Semantic"), label="Type")


class TestBoundedCollections(UnitTestCase):
	def test_bounded_list_accepts_json_comma_and_list(self):
		self.assertEqual(v.bounded_list('["KB-A", "KB-B"]', label="KB", max_items=25), ["KB-A", "KB-B"])
		self.assertEqual(v.bounded_list("KB-A, KB-B", label="KB", max_items=25), ["KB-A", "KB-B"])
		self.assertEqual(v.bounded_list(["KB-A", "KB-A"], label="KB", max_items=25), ["KB-A"])
		with self.assertRaises(frappe.ValidationError):
			v.bounded_list([str(i) for i in range(26)], label="KB", max_items=25)
		with self.assertRaises(frappe.ValidationError):
			v.bounded_list('{"not": "a list"}', label="KB", max_items=25)

	def test_identifiers(self):
		self.assertEqual(v.valid_identifier("Home/My Folder", label="Folder"), "Home/My Folder")
		self.assertEqual(v.valid_identifier("ACC-2026-001", label="Document"), "ACC-2026-001")
		with self.assertRaises(frappe.ValidationError):
			v.valid_identifier("DROP TABLE x;", label="Document")
		with self.assertRaises(frappe.ValidationError):
			v.valid_identifier("", label="Document", required=True)

	def test_idempotency_keys(self):
		self.assertIsNone(v.idempotency_key(None))
		self.assertEqual(v.idempotency_key("run-2026-08-20_01"), "run-2026-08-20_01")
		with self.assertRaises(frappe.ValidationError):
			v.idempotency_key("bad key with spaces")
		with self.assertRaises(frappe.ValidationError):
			v.idempotency_key("x" * 65)

	def test_payload_bytes_are_bounded(self):
		self.assertEqual(v.bounded_payload('{"a": 1}', label="Payload", max_bytes=64), '{"a": 1}')
		with self.assertRaises(frappe.ValidationError):
			v.bounded_payload("x" * 65, label="Payload", max_bytes=64)


class TestModelNameValidation(UnitTestCase):
	def test_accepts_model_labels_and_runtime_names(self):
		self.assertEqual(v.valid_model_name("qwen2.5:0.5b"), "qwen2.5:0.5b")
		self.assertEqual(v.valid_model_name("Local Chat (Small)"), "Local Chat (Small)")
		self.assertEqual(v.valid_model_name("nomic-embed-text"), "nomic-embed-text")
		self.assertEqual(v.valid_model_name("acme.local/models/phi3:mini"), "acme.local/models/phi3:mini")

	def test_rejects_empty_oversized_and_control_characters(self):
		with self.assertRaises(frappe.ValidationError):
			v.valid_model_name("")
		with self.assertRaises(frappe.ValidationError):
			v.valid_model_name("x" * 256)
		with self.assertRaises(frappe.ValidationError):
			v.valid_model_name("bad\u0000model")
