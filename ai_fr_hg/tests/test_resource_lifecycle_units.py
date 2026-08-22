# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Pure unit tests for the resource marketplace engine.

These tests need no database or running runtime. Frappe is present inside the
bench, but the assertions target the pure helpers so they stay fast.
"""

import json
from unittest.mock import patch

try:
	from frappe.tests import UnitTestCase
except ImportError:
	from unittest import TestCase as UnitTestCase

from ai_fr_hg.ai.resources.catalog import expand_resource_code, normalize_catalog_filters
from ai_fr_hg.ai.resources.installers import get_installers
from ai_fr_hg.ai.resources.verification import compute_package_digest, sign_package, verify_signature


class TestResourceCatalogHelpers(UnitTestCase):
	def test_expand_resource_code_is_filesystem_safe(self):
		self.assertEqual(expand_resource_code("ai-prompt-essentials"), "ai-prompt-essentials")
		self.assertEqual(expand_resource_code("../etc/passwd"), "etcpasswd")
		self.assertEqual(expand_resource_code(""), "resource")

	def test_normalize_catalog_filters_keeps_safe_keys(self):
		filters = normalize_catalog_filters(
			{
				"resource_type": "AI Prompt Template",
				"category": "Prompt Templates",
				"enabled": 1,
				"evil": "SELECT * FROM Users",
				"repository": "",
			}
		)
		self.assertNotIn("evil", filters)
		self.assertNotIn("repository", filters)
		self.assertEqual(filters["resource_type"], "AI Prompt Template")

	def test_version_constraint_semantics(self):
		from ai_fr_hg.ai.resources.catalog import _satisfies

		self.assertTrue(_satisfies(">=17.0.0", "17.0.0"))
		self.assertTrue(_satisfies(">=17.0.0", "17.2.1"))
		self.assertFalse(_satisfies(">=17.0.0", "16.8.0"))
		self.assertTrue(_satisfies(">=3.14,<3.15", "3.14.0"))


class TestResourceVerification(UnitTestCase):
	def test_checksum_and_signature_round_trip(self):
		payload = json.dumps({"resource_code": "demo"}).encode("utf-8")
		with patch("ai_fr_hg.ai.resources.verification.frappe.get_site_config", return_value={"ai_resource_signing_key": "test-key"}):
			digest = compute_package_digest(payload)
			self.assertTrue(digest["verified"])
			self.assertTrue(verify_signature(digest["sha256"], digest["signature"]))
			self.assertFalse(verify_signature(digest["sha256"], "0000000000000000000000000000000000000000000000"))

	def test_signature_rejects_forged_checksum(self):
		payload = b"hello"
		with patch("ai_fr_hg.ai.resources.verification.frappe.get_site_config", return_value={"ai_resource_signing_key": "test-key"}):
			good_signature = sign_package("checksum-abc")
			self.assertTrue(verify_signature("checksum-abc", good_signature))
			self.assertFalse(verify_signature("checksum-def", good_signature))


class TestResourceInstallers(UnitTestCase):
	def test_all_planned_resource_types_have_installers(self):
		installers = get_installers()
		for expected in (
			"Translation Package",
			"Translation Memory Pack",
			"AI Model",
			"AI Prompt Template",
			"AI Workflow Template",
			"Agent Capability",
			"Language Pack",
			"Knowledge Resource",
		):
			self.assertIn(expected, installers)

	def test_installer_callables_are_importable(self):
		installers = get_installers()
		for resource_type, callback in installers.items():
			with self.subTest(resource_type=resource_type):
				self.assertTrue(callable(callback))

	def test_package_bundles_exist(self):
		from ai_fr_hg.ai.resources.paths import bundles_dir

		files = list(bundles_dir().glob("*.json"))
		self.assertGreaterEqual(len(files), 6)
		for path in files:
			manifest = json.loads(path.read_text(encoding="utf-8"))
			self.assertEqual(manifest.get("schema"), "ai-resource-package-v1")
			self.assertTrue(manifest.get("resource_code"))
