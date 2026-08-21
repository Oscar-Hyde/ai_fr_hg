# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Dependency-free AUTO-01/AUTO-02 and TASK-02 contract tests."""

from unittest import TestCase

from ai_fr_hg.ai.automation_utils import (
	event_revision_key,
	sanitize_snapshot,
	source_field_error,
)


class TestSourceFieldContract(TestCase):
	def test_missing_child_sensitive_and_table_are_rejected(self):
		self.assertEqual(source_field_error("roles.role", "Data", exists=True), "child_table_path")
		self.assertEqual(source_field_error("ghost", "Data", exists=False), "missing")
		self.assertEqual(source_field_error("api_key", "Data", exists=True), "sensitive")
		self.assertEqual(source_field_error("password", "Password", exists=True), "disallowed_type")
		self.assertEqual(source_field_error("items", "Table", exists=True), "disallowed_type")
		self.assertIsNone(source_field_error("description", "Small Text", exists=True))
		self.assertIsNone(source_field_error(None, None, exists=False))


class TestSnapshotSanitization(TestCase):
	def test_strips_secrets_and_child_tables(self):
		payload = {
			"doctype": "ToDo",
			"name": "TODO-1",
			"description": "keep me",
			"api_key": "secret",
			"password": "nope",
			"assignments": [{"user": "x"}],
			"_user_tags": "x",
		}
		clean = sanitize_snapshot(payload, denied_fields={"private_note"})
		self.assertEqual(clean["description"], "keep me")
		self.assertNotIn("api_key", clean)
		self.assertNotIn("password", clean)
		self.assertNotIn("assignments", clean)
		self.assertEqual(event_revision_key("Rule", "ToDo", "TODO-1", "2026-01-01"), "Rule::ToDo::TODO-1::2026-01-01")
