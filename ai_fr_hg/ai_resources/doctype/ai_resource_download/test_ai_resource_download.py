# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Integration coverage for the resource marketplace download/install path."""

import frappe
from frappe.utils import now_datetime

from ai_fr_hg.tests.integration_test_case import AIPlatformTestCase


class TestAIResourceDownload(AIPlatformTestCase):
	def setUp(self):
		super().setUp()
		from ai_fr_hg.ai.resources.catalog import refresh_builtin_catalog

		self.catalog = refresh_builtin_catalog(user="Administrator")
		self.resource = frappe.get_doc("AI Resource", "ai-prompt-essentials")

	def tearDown(self):
		for name in frappe.get_all("AI Resource Install", filters={"resource": self.resource.name}, pluck="name"):
			frappe.delete_doc("AI Resource Install", name, force=True, ignore_permissions=True, delete_permanently=True)
		for name in frappe.get_all("AI Resource Download", filters={"resource": self.resource.name}, pluck="name"):
			frappe.delete_doc("AI Resource Download", name, force=True, ignore_permissions=True, delete_permanently=True)
		for name in ("Executive Summary", "Grounded Answer"):
			if frappe.db.exists("AI Prompt Template", name):
				frappe.delete_doc("AI Prompt Template", name, force=True, ignore_permissions=True, delete_permanently=True)
		super().tearDown()

	def test_refresh_builtin_catalog_seeds_resources(self):
		self.assertGreaterEqual(self.catalog["created"] + self.catalog["updated"], 6)
		self.assertTrue(frappe.db.exists("AI Resource", "ai-prompt-essentials"))
		self.assertTrue(self.resource.is_builtin)
		self.assertTrue(self.resource.sha256)
		self.assertTrue(self.resource.signature)
		self.assertEqual(self.resource.resource_type, "AI Prompt Template")

	def test_install_manifest_activates_prompt_templates(self):
		from ai_fr_hg.ai.resources.download import activate_install, register_install
		from ai_fr_hg.ai.resources.install import install_manifest
		from ai_fr_hg.ai.resources.paths import bundle_path
		from ai_fr_hg.ai.resources.verification import validate_manifest

		manifest = validate_manifest(bundle_path(self.resource.resource_code).read_bytes())

		download = frappe.get_doc(
			{
				"doctype": "AI Resource Download",
				"resource": self.resource.name,
				"resource_code": self.resource.resource_code,
				"resource_name": self.resource.resource_name,
				"resource_type": self.resource.resource_type,
				"version": self.resource.version,
				"status": "Installing",
				"stage": "Installing package",
				"user": "Administrator",
				"started_at": now_datetime(),
			}
		)
		download.insert(ignore_permissions=True)

		install_name, targets = install_manifest(self.resource, manifest, download, "Administrator")
		register_install(install_name, targets, self.resource, manifest, download)
		activate_install(install_name, targets, self.resource)

		self.assertTrue(frappe.db.exists("AI Resource Install", install_name))
		install = frappe.get_doc("AI Resource Install", install_name)
		self.assertEqual(install.status, "Active")
		self.assertTrue(install.is_active)
		self.assertIn(("AI Prompt Template", "Executive Summary"), [(t.get("doctype"), t.get("name")) for t in targets])
		self.assertTrue(frappe.db.exists("AI Prompt Template", "Executive Summary"))

	def test_dependency_engine_lists_embedded_dependency(self):
		workflow = frappe.get_doc("AI Resource", "workflow-review-brief")
		codes = [row.resource_code for row in workflow.dependencies]
		self.assertIn("ai-prompt-essentials", codes)
