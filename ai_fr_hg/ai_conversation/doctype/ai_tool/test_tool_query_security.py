# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Security coverage for the generic document tools (SEC-02 / SEC-03).

Every test here drives the tools through the central safe-query mechanism in
``ai_fr_hg.ai.tools.query`` and verifies that row-level, field-level and
sensitive-field boundaries hold for non-manager users.
"""

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from ai_fr_hg.ai.tools import query as safe_query
from ai_fr_hg.tests.integration_test_case import AIPlatformTestCase


class TestGenericToolRowPermissions(AIPlatformTestCase):
	"""SEC-02: aggregate/list results respect the caller's row authority."""

	def make_ai_user(self, email, first_name):
		if frappe.db.exists("User", email):
			return frappe.get_doc("User", email)
		doc = frappe.get_doc(
			{
				"doctype": "User",
				"email": email,
				"first_name": first_name,
				"send_welcome_email": 0,
				"roles": [{"role": "AI User"}],
			}
		)
		doc.insert(ignore_permissions=True)
		return doc

	def make_conversation(self, title, user):
		doc = frappe.get_doc(
			{
				"doctype": "AI Conversation",
				"title": title,
				"user": user,
				"status": "Open",
			}
		)
		doc.insert(ignore_permissions=True)
		return doc

	def test_count_only_counts_rows_the_caller_can_list(self):
		from ai_fr_hg.ai.tools.builtin import count_documents, list_documents

		user_a = self.make_ai_user("tool-count-a@example.com", "Count A")
		user_b = self.make_ai_user("tool-count-b@example.com", "Count B")
		self.make_conversation("QuerySecurity A Conversation", user_a.name)
		self.make_conversation("QuerySecurity B Conversation", user_b.name)

		frappe.set_user(user_a.name)
		try:
			rows = list_documents(
				"AI Conversation",
				filters={"title": ["like", "QuerySecurity % Conversation"]},
				fields=["title", "user"],
			)
			self.assertTrue(rows, "the caller should see their own conversation")
			self.assertTrue(all(row["user"] == user_a.name for row in rows))
			self.assertNotIn(user_b.name, {row["user"] for row in rows})

			count = count_documents(
				"AI Conversation", filters={"title": ["like", "QuerySecurity % Conversation"]}
			)
			self.assertEqual(count["count"], len(rows))
		finally:
			frappe.set_user("Administrator")

	def test_count_without_read_permission_is_rejected(self):
		from ai_fr_hg.ai.tools.builtin import count_documents

		user = self.make_ai_user("tool-count-guest@example.com", "Count Guest")
		frappe.set_user(user.name)
		try:
			# AI Platform Settings is manager-only; a generic tool must refuse
			# the aggregate instead of returning it.
			self.assertFalse(frappe.has_permission("AI Platform Settings", "read"))
			with self.assertRaises(frappe.PermissionError):
				count_documents("AI Platform Settings")
		finally:
			frappe.set_user("Administrator")


class TestGenericToolFieldPermissions(AIPlatformTestCase):
	"""SEC-03: field-level projection and sensitive-field denial."""

	def make_ai_user(self, email, first_name):
		if frappe.db.exists("User", email):
			return frappe.get_doc("User", email)
		doc = frappe.get_doc(
			{
				"doctype": "User",
				"email": email,
				"first_name": first_name,
				"send_welcome_email": 0,
				"roles": [{"role": "AI User"}],
			}
		)
		doc.insert(ignore_permissions=True)
		return doc

	def test_password_fields_are_never_exposed(self):
		from ai_fr_hg.ai.tools.builtin import get_document

		user = self.make_ai_user("tool-fields@example.com", "Field User")
		frappe.set_user(user.name)
		try:
			result = get_document("AI Provider", self.provider.name)
			self.assertIn("provider_name", result)
			self.assertNotIn("api_key", result, "password fields must never reach a tool result")

			# An explicit request for the sensitive field must not override the deny rule.
			result = get_document("AI Provider", self.provider.name, fields=["provider_name", "api_key"])
			self.assertNotIn("api_key", result)
			self.assertIn("provider_name", result)
		finally:
			frappe.set_user("Administrator")

	def test_configured_sensitive_fields_are_denied(self):
		from ai_fr_hg.ai.tools.builtin import get_document

		settings = frappe.get_doc("AI Platform Settings")
		previous = settings.tool_sensitive_fields
		settings.db_set("tool_sensitive_fields", "AI Provider.base_url")
		user = self.make_ai_user("tool-fields@example.com", "Field User")
		try:
			frappe.set_user(user.name)
			result = get_document("AI Provider", self.provider.name)
			self.assertNotIn("base_url", result, "configured sensitive fields must be denied")
			self.assertIn("provider_name", result)
		finally:
			frappe.set_user("Administrator")
			settings.db_set("tool_sensitive_fields", previous)

	def test_filters_on_denied_fields_are_dropped_not_probed(self):
		from ai_fr_hg.ai.tools.builtin import list_documents

		user = self.make_ai_user("tool-fields@example.com", "Field User")
		frappe.set_user(user.name)
		try:
			# A filter on a denied field must not run (it would be a blind probe)
			# and must not crash the call.
			rows = list_documents("AI Provider", filters={"api_key": ["!=", ""]}, fields=["name"])
			self.assertIsInstance(rows, list)
			self.assertFalse(any("api_key" in row for row in rows))
		finally:
			frappe.set_user("Administrator")

	def test_unrequested_dump_is_bounded_to_readable_fields(self):
		from ai_fr_hg.ai.tools.builtin import get_document

		user = self.make_ai_user("tool-fields@example.com", "Field User")
		frappe.set_user(user.name)
		try:
			result = get_document("AI Provider", self.provider.name)
			permitted, deny = safe_query.readable_fields("AI Provider", user.name)
			allowed = (permitted - deny) | {"name"}
			for key in result:
				self.assertIn(key, allowed, f"unexpected field {key} in tool result")
			self.assertIn("name", result)
		finally:
			frappe.set_user("Administrator")


class TestGenericToolQueryMechanics(IntegrationTestCase):
	"""Deterministic mechanics of the central safe-query module."""

	def _fake_meta(self, fields):
		meta = frappe._dict(name="Fake DocType")
		meta.fields = [
			frappe._dict(fieldname=name, fieldtype=fieldtype, permlevel=permlevel)
			for name, fieldtype, permlevel in fields
		]
		return meta

	def test_normalise_fields_respects_requested_allowlist_and_deny(self):
		meta = self._fake_meta(
			[
				("title", "Data", 0),
				("secret_note", "Data", 0),
				("api_key", "Password", 0),
				("restricted", "Data", 1),
				("body", "Long Text", 0),
			]
		)
		# `restricted` sits at permlevel 1, so it is never inside `permitted`.
		permitted = {"title", "secret_note", "api_key", "body"}
		deny = {"api_key", "secret_note"}
		projected = safe_query._normalise_fields(meta, ["title", "api_key", "body"], permitted, deny)
		self.assertEqual(set(projected), {"title", "body"})

		# An allowlist restricts further but never grants.
		projected = safe_query._normalise_fields(
			meta, None, permitted, deny, allowlist={"title", "restricted"}
		)
		self.assertEqual(set(projected), {"title"})

		# Restricted (permlevel 1) fields are outside `permitted`, so even an
		# explicit request cannot reach them.
		projected = safe_query._normalise_fields(meta, ["restricted"], permitted, deny)
		self.assertEqual(projected, ["name"])

	def test_normalise_filters_drops_denied_fields_and_bounds_values(self):
		from ai_fr_hg.ai.exceptions import ToolExecutionError

		readable = {"title", "body"}
		cleaned = safe_query._normalise_filters(
			{"title": "A", "api_key": ["!=", ""], "body": ["in", ["x", "y"]]}, "Fake DocType", readable
		)
		self.assertIn("title", cleaned)
		self.assertIn("body", cleaned)
		self.assertNotIn("api_key", cleaned, "filters on denied fields must be dropped")

		# Dict/aggregate filter syntax is never accepted from a tool.
		self.assertNotIn(
			"title",
			safe_query._normalise_filters({"title": {"like": "%x%"}}, "Fake DocType", readable),
		)

		with self.assertRaises(ToolExecutionError):
			safe_query._normalise_filters({"title": [str(i) for i in range(101)]}, "Fake DocType", readable)

		with self.assertRaises(ToolExecutionError):
			safe_query._normalise_filters({"title": "x" * 201}, "Fake DocType", readable)

	def test_denied_fieldnames_detect_types_and_names(self):
		meta = self._fake_meta(
			[
				("provider_name", "Data", 0),
				("api_key", "Password", 0),
				("session_key", "Data", 0),
				("client_secret", "Data", 0),
				("access_token", "Data", 0),
				("base_url", "Data", 0),
			]
		)
		with patch("ai_fr_hg.ai.tools.query.frappe.get_meta", return_value=meta):
			denied = safe_query.denied_fieldnames("Fake DocType")
		self.assertIn("api_key", denied)
		self.assertIn("session_key", denied)
		self.assertIn("client_secret", denied)
		self.assertIn("access_token", denied)
		self.assertNotIn("provider_name", denied)
		self.assertNotIn("base_url", denied)


class TestDoctypeActionFieldStripping(IntegrationTestCase):
	"""Write tools strip model-supplied values to writable, non-denied fields."""

	def test_sensitive_values_are_stripped_before_update(self):
		values = {"provider_name": "Renamed Provider", "api_key": "exfil", "base_url": "http://x"}
		cleaned = safe_query.safe_field_values("AI Provider", values, user="Administrator")
		self.assertNotIn("api_key", cleaned, "password fields must never be writable via tools")
		self.assertIn("provider_name", cleaned)
