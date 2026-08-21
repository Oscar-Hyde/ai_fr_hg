# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Frappe integration coverage for the AI Pattern Entity enhancement layer."""

import frappe

from ai_fr_hg.tests.integration_test_case import AIPlatformTestCase

ENTITY_CONTENT = (
	"From alice@corp.example to bob@example.net.\n"
	"Portal: https://corp.example/invoice/INV-2024-0817 (mirror www.corp.example).\n"
	"Support line +359 88 123 4567. Gateway 10.0.0.12.\n"
	"Attachment sha256 5d41402abc4b2a76b9719d911017c592 verified.\n"
	"Issued 2024-03-05, due 25/12/2024. Balance $1,234.50."
)


def _rows(document):
	return frappe.get_all(
		"AI Pattern Entity",
		filters={"document": document},
		fields=[
			"name",
			"entity_type",
			"value",
			"normalized_value",
			"occurrences",
			"knowledge_base",
			"source_checksum",
		],
		order_by="entity_type asc",
	)


class TestPatternEntityScan(AIPlatformTestCase):
	def test_scan_creates_rows_from_stored_content(self):
		from ai_fr_hg.ai.patterns import scan_document

		document = self.make_document("Pattern Scan Source", ENTITY_CONTENT)
		frappe.db.set_value("AI Document", document.name, "checksum", "deadbeef", update_modified=False)

		result = scan_document(document.name)

		self.assertEqual(result["removed"], 0)
		self.assertEqual(result["total"], len(_rows(document.name)))
		self.assertGreater(result["total"], 0)
		self.assertIn("email", result["by_type"])

		rows = {(row.entity_type, row.normalized_value) for row in _rows(document.name)}
		self.assertIn(("email", "alice@corp.example"), rows)
		self.assertIn(("identifier", "inv-2024-0817"), rows)
		self.assertIn(("date", "2024-12-25"), rows)
		for row in _rows(document.name):
			self.assertEqual(row.source_checksum, "deadbeef")
			self.assertEqual(
				row.knowledge_base or frappe.db.get_value("AI Pattern Entity", row.name, "knowledge_base"),
				self.knowledge_base.name,
			)

	def test_rescan_is_idempotent_and_updates_in_place(self):
		from ai_fr_hg.ai.patterns import scan_document

		document = self.make_document("Pattern Rescan Source", ENTITY_CONTENT)
		first = scan_document(document.name)
		before = sorted(row.name for row in _rows(document.name))

		second = scan_document(document.name)
		after = sorted(row.name for row in _rows(document.name))

		self.assertEqual(second["created"], 0)
		self.assertEqual(second["removed"], 0)
		self.assertEqual(second["total"], first["total"])
		self.assertEqual(before, after)

	def test_zero_result_scan_is_durable_and_not_rescanned(self):
		from ai_fr_hg.ai.patterns import scan_document, scan_pending_documents

		document = self.make_document("Empty Pattern Scan", "Nothing but plain words.")
		frappe.db.set_value(
			"AI Document",
			document.name,
			{"status": "Indexed", "checksum": "empty-scan"},
			update_modified=False,
		)
		result = scan_document(document.name)
		self.assertEqual(result["total"], 0)
		self.assertEqual(
			frappe.db.get_value("AI Document", document.name, "pattern_scan_checksum"),
			"empty-scan",
		)
		self.assertEqual(scan_pending_documents(), [])

	def test_rescan_prunes_entities_removed_from_content(self):
		from ai_fr_hg.ai.patterns import scan_document

		document = self.make_document("Pattern Prune Source", ENTITY_CONTENT)
		scan_document(document.name)
		before_count = len(_rows(document.name))
		self.assertGreater(before_count, 0)

		# Content changes only through the document's own write path; the
		# pattern layer just observes the new state.
		frappe.db.set_value(
			"AI Document", document.name, "content", "Nothing but plain words.", update_modified=False
		)
		result = scan_document(document.name)

		self.assertEqual(result["total"], 0)
		self.assertEqual(result["removed"], before_count)
		self.assertEqual(_rows(document.name), [])

	def test_document_trash_cascades_pattern_rows(self):
		from ai_fr_hg.ai.patterns import scan_document

		document = self.make_document("Pattern Cascade Source", ENTITY_CONTENT)
		scan_document(document.name)
		self.assertTrue(_rows(document.name))

		frappe.delete_doc("AI Document", document.name, ignore_permissions=True)
		self.assertEqual(_rows(document.name), [])

	def test_permission_rules_mirror_chunk_access(self):
		from ai_fr_hg.ai.patterns import scan_document
		from ai_fr_hg.utils.permissions import has_document_permission, pattern_entity_query

		document = self.make_document("Pattern Permission Source", ENTITY_CONTENT)
		scan_document(document.name)
		row = frappe.get_doc("AI Pattern Entity", _rows(document.name)[0].name)

		# Managers see everything; everyone else filters by knowledge base.
		self.assertEqual(pattern_entity_query("Administrator"), "")
		self.assertIn("`tabAI Pattern Entity`", pattern_entity_query("someone@example.com"))

		self.assertTrue(has_document_permission(row, "read", user="Administrator"))
		# The test knowledge base is public, so plain users may read but never
		# write machine-authored analysis rows.
		self.assertTrue(has_document_permission(row, "read", user="someone@example.com"))
		self.assertFalse(has_document_permission(row, "write", user="someone@example.com"))

	def test_explorer_denies_unauthorized_knowledge_base(self):
		from ai_fr_hg.api.knowledge import explore_pattern_entities

		email = "pat04-stranger@example.com"
		if not frappe.db.exists("User", email):
			frappe.get_doc(
				{
					"doctype": "User",
					"email": email,
					"first_name": "Pattern Stranger",
					"send_welcome_email": 0,
					"roles": [{"role": "AI User"}],
				}
			).insert(ignore_permissions=True)
		private_name = "PAT-04 Private KB"
		if not frappe.db.exists("AI Knowledge Base", private_name):
			frappe.get_doc(
				{
					"doctype": "AI Knowledge Base",
					"knowledge_base_name": private_name,
					"enabled": 1,
					"is_public": 0,
					"chunk_size": 400,
					"chunk_overlap": 40,
					"embedding_model": self.embedding_model.name,
				}
			).insert(ignore_permissions=True)
		frappe.set_user(email)
		try:
			with self.assertRaises(frappe.PermissionError):
				explore_pattern_entities(knowledge_base=private_name, limit=10, offset=0)
		finally:
			frappe.set_user("Administrator")

	def test_explorer_lists_across_documents_with_pagination(self):
		from ai_fr_hg.ai.patterns import scan_document
		from ai_fr_hg.api.knowledge import explore_pattern_entities

		first = self.make_document("Explorer One", ENTITY_CONTENT)
		second = self.make_document("Explorer Two", "Contact zed@corp.example about INV-2099-0001.")
		scan_document(first.name)
		scan_document(second.name)
		page = explore_pattern_entities(limit=2, offset=0)
		self.assertEqual(page["limit"], 2)
		self.assertEqual(len(page["entities"]), 2)
		self.assertTrue(page["entity_counts"])
		emails = explore_pattern_entities(entity_type="email", limit=50, offset=0)
		self.assertTrue(emails["entities"])
		self.assertTrue({row["entity_type"] for row in emails["entities"]} <= {"email"})

	def test_api_scan_and_listing(self):
		from ai_fr_hg.api.knowledge import get_pattern_entities, scan_pattern_entities

		document = self.make_document("Pattern API Source", ENTITY_CONTENT)
		result = scan_pattern_entities(document.name)
		self.assertEqual(result["total"], len(_rows(document.name)))

		listing = get_pattern_entities(document.name)
		self.assertEqual(listing["document"], document.name)
		self.assertEqual(sum(listing["entity_counts"].values()), len(listing["entities"]))
		self.assertGreaterEqual(listing["entity_counts"].get("email", 0), 2)

		emails = get_pattern_entities(document.name, entity_type="email")
		self.assertTrue(emails["entities"])
		self.assertTrue({row["entity_type"] for row in emails["entities"]} <= {"email"})

	def test_scheduler_backfill_is_opt_in_and_idempotent(self):
		from ai_fr_hg.ai.patterns import scan_pending_documents
		from ai_fr_hg.tasks import scan_pending_pattern_entities

		document = self.make_document("Pattern Scheduler Source", ENTITY_CONTENT)
		frappe.db.set_value(
			"AI Document", document.name, {"status": "Indexed", "checksum": "cafebabe"}, update_modified=False
		)

		settings = frappe.get_cached_doc("AI Platform Settings")
		original_enabled, original_scan = settings.platform_enabled, settings.auto_scan_patterns
		frappe.db.set_single_value("AI Platform Settings", "platform_enabled", 1)
		try:
			# Opt-out by default: nothing is scanned until explicitly enabled.
			frappe.db.set_single_value("AI Platform Settings", "auto_scan_patterns", 0)
			scan_pending_pattern_entities()
			self.assertEqual(_rows(document.name), [])

			frappe.db.set_single_value("AI Platform Settings", "auto_scan_patterns", 1)
			scan_pending_pattern_entities()
			rows = _rows(document.name)
			self.assertEqual(len(rows), len({(r.entity_type, r.normalized_value) for r in rows}))
			self.assertTrue(all(row.source_checksum == "cafebabe" for row in rows))

			# The scan marker makes the next pass a no-op for fresh documents.
			self.assertEqual(scan_pending_documents(), [])
		finally:
			frappe.db.set_single_value("AI Platform Settings", "auto_scan_patterns", original_scan)
			frappe.db.set_single_value("AI Platform Settings", "platform_enabled", original_enabled)

	def test_controller_keeps_canonical_identity(self):
		from ai_fr_hg.ai.patterns import scan_document

		document = self.make_document("Pattern Controller Source", ENTITY_CONTENT)
		scan_document(document.name)

		row = frappe.new_doc("AI Pattern Entity")
		row.document = document.name
		row.entity_type = "email"
		row.value = "  New.User@Example.COM "
		row.occurrences = 0
		row.insert(ignore_permissions=True)

		self.assertEqual(row.normalized_value, "new.user@example.com")
		self.assertEqual(row.occurrences, 1)
		self.assertEqual(row.knowledge_base, self.knowledge_base.name)
