# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Integration coverage for the native mixed AI Document Tree View service."""

from unittest.mock import patch

import frappe

from ai_fr_hg.ai.document_tree import (
	_files_in_folders,
	_preflight_bulk_snapshot,
	_preflight_subtree,
	_subtree_state,
	bulk_move_nodes,
	copy_document,
	copy_folder,
	create_folder,
	delete_folder,
	get_children,
	move_document,
	move_folder,
	rename_document,
)
from ai_fr_hg.ai.exceptions import CircularFolderError, DocumentFetchError, FolderAlreadyExistsError
from ai_fr_hg.ai.folders import bulk_move as bulk_move_files
from ai_fr_hg.ai.folders import copy_file as copy_physical_file
from ai_fr_hg.ai.folders import delete_file as delete_physical_file
from ai_fr_hg.tests.integration_test_case import AIPlatformTestCase


class TestAIDocumentTree(AIPlatformTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		self.prefix = f"tree-{frappe.generate_hash(length=8)}"
		self.root = create_folder(self.prefix, "Home")["name"]

	def make_tree_document(self, title: str, folder: str | None = None):
		doc = frappe.get_doc(
			{
				"doctype": "AI Document",
				"title": title,
				"organization_name": title,
				"knowledge_base": self.knowledge_base.name,
				"source_type": "Text",
				"content": f"Content for {title}",
				"folder": folder or self.root,
				"source_folder": folder or self.root,
				"status": "Draft",
			}
		)
		doc.flags.skip_auto_process = True
		doc.insert()
		return doc

	def test_lifecycle_keeps_one_identity_on_move_and_rejects_same_location_collision(self):
		source = create_folder("Source", self.root)["name"]
		destination = create_folder("Destination", self.root)["name"]
		document = self.make_tree_document("Contract.txt", source)
		self.make_tree_document("Contract.txt", destination)

		with self.assertRaises(FolderAlreadyExistsError):
			move_document(document.name, destination)
		self.assertEqual(frappe.db.get_value("AI Document", document.name, "folder"), source)

		rename_document(document.name, "Source Contract.txt")
		result = move_document(document.name, destination)
		self.assertEqual(result["name"], document.name)
		self.assertEqual(frappe.db.get_value("AI Document", document.name, "folder"), destination)
		self.assertEqual(frappe.db.count("AI Document", {"name": document.name}), 1)

	def test_move_of_shared_file_source_transfers_native_attachment_owner(self):
		source = create_folder("Shared Move Source", self.root)["name"]
		destination = create_folder("Shared Move Destination", self.root)["name"]
		file_doc = frappe.get_doc(
			{
				"doctype": "File",
				"file_name": "shared-move.txt",
				"folder": source,
				"is_private": 1,
				"content": "shared move source",
			}
		)
		file_doc.insert()

		documents = []
		for index in range(2):
			document = frappe.get_doc(
				{
					"doctype": "AI Document",
					"title": f"Shared Move {index}",
					"organization_name": f"shared-move-{index}.txt",
					"knowledge_base": self.knowledge_base.name,
					"source_type": "File",
					"source_file": file_doc.file_url,
					"source_file_record": file_doc.name,
					"folder": source,
					"source_folder": source,
					"status": "Draft",
				}
			)
			document.flags.skip_auto_process = True
			document.insert()
			documents.append(document.name)
		frappe.db.set_value(
			"File",
			file_doc.name,
			{
				"attached_to_doctype": "AI Document",
				"attached_to_name": documents[0],
				"attached_to_field": "source_file",
			},
			update_modified=False,
		)

		move_document(documents[0], destination)
		moved_file = frappe.db.get_value("AI Document", documents[0], "source_file_record")
		self.assertNotEqual(moved_file, file_doc.name)
		self.assertEqual(frappe.db.get_value("File", moved_file, "folder"), destination)
		self.assertEqual(frappe.db.get_value("File", file_doc.name, "attached_to_name"), documents[1])
		self.assertEqual(
			frappe.db.get_value("AI Document", documents[1], "source_file_record"),
			file_doc.name,
		)

	def test_copy_creates_new_identity_and_does_not_clone_processing_derivatives(self):
		document = self.make_tree_document("Indexed Contract.txt")
		frappe.db.set_value(
			"AI Document",
			document.name,
			{"status": "Indexed", "summary": "source summary", "chunk_count": 1, "embedded_chunk_count": 1},
			update_modified=False,
		)
		frappe.get_doc(
			{
				"doctype": "AI Document Chunk",
				"document": document.name,
				"knowledge_base": self.knowledge_base.name,
				"chunk_index": 0,
				"content": "source chunk",
				"character_count": 12,
			}
		).insert()

		copy = copy_document(document.name, self.root)
		self.assertNotEqual(copy["name"], document.name)
		self.assertEqual(copy["title"], "Indexed Contract (Copy).txt")
		self.assertEqual(frappe.db.get_value("AI Document", copy["name"], "copied_from"), document.name)
		self.assertEqual(frappe.db.get_value("AI Document", copy["name"], "status"), "Draft")
		self.assertEqual(frappe.db.count("AI Document Chunk", {"document": copy["name"]}), 0)
		self.assertEqual(frappe.db.count("AI Document Chunk", {"document": document.name}), 1)
		self.assertEqual(frappe.db.get_value("AI Document", document.name, "summary"), "source summary")

		copy_doc = frappe.get_doc("AI Document", copy["name"])
		copy_doc.copied_from = copy_doc.name
		with self.assertRaises(frappe.ValidationError):
			copy_doc.save()
		self.assertEqual(frappe.db.get_value("AI Document", copy["name"], "copied_from"), document.name)

		forged = frappe.get_doc(
			{
				"doctype": "AI Document",
				"title": "Forged Copy",
				"organization_name": "Forged Copy",
				"knowledge_base": self.knowledge_base.name,
				"source_type": "Text",
				"content": "not copied by the canonical service",
				"folder": self.root,
				"source_folder": self.root,
				"status": "Draft",
				"copied_from": document.name,
				"copied_on": frappe.utils.now_datetime(),
			}
		)
		# A client-controlled document flag is not an authorization capability.
		forged.flags.allow_copy_provenance = True
		forged.flags.skip_auto_process = True
		with self.assertRaises(frappe.PermissionError):
			forged.insert()

	def test_file_ingestion_flags_cannot_bypass_canonical_source_ownership(self):
		document = self.make_tree_document("Attachment Target.txt")
		frappe.db.set_value("AI Document", document.name, "status", "Archived", update_modified=False)
		file_doc = frappe.get_doc(
			{
				"doctype": "File",
				"file_name": "canonical-source.txt",
				"folder": self.root,
				"is_private": 1,
				"content": "canonical bytes",
				"attached_to_doctype": "AI Document",
				"attached_to_name": document.name,
				"attached_to_field": "source_file",
			}
		)
		# A payload flag is not the process-local tree-copy capability.
		file_doc.flags.skip_ai_document_ingestion = True
		file_doc.insert()
		self.assertEqual(
			frappe.db.get_value("AI Document", document.name, "source_file_record"),
			file_doc.name,
		)

		other = create_folder("Attachment Copy", self.root)["name"]
		copy_physical_file(
			file_doc.name,
			other,
			attached_to_doctype="AI Document",
			attached_to_name=document.name,
			attached_to_field="source_file",
		)
		# Byte-level deduplication and another attachment must not replace the
		# stable source File identity or move the AI Document.
		self.assertEqual(
			frappe.db.get_value("AI Document", document.name, "source_file_record"),
			file_doc.name,
		)
		self.assertEqual(frappe.db.get_value("AI Document", document.name, "folder"), self.root)

	def test_ingestion_uses_exact_file_identity_and_rejects_ambiguous_legacy_url(self):
		from ai_fr_hg.ai.ingestion import ingest_file

		established_folder = create_folder("Established Source", self.root)["name"]
		upload_folder = create_folder("Fresh Upload", self.root)["name"]
		established_file = frappe.get_doc(
			{
				"doctype": "File",
				"file_name": "established.txt",
				"folder": established_folder,
				"is_private": 1,
				"content": "identical deduplicated bytes",
			}
		).insert()
		established_document = frappe.get_doc(
			{
				"doctype": "AI Document",
				"title": "Established Document",
				"organization_name": "established.txt",
				"knowledge_base": self.knowledge_base.name,
				"source_type": "File",
				"source_file": established_file.file_url,
				"source_file_record": established_file.name,
				"folder": established_folder,
				"source_folder": established_folder,
				"status": "Draft",
			}
		)
		established_document.flags.skip_auto_process = True
		established_document.insert()
		fresh_file = frappe.get_doc(
			{
				"doctype": "File",
				"file_name": "fresh-upload.txt",
				"folder": upload_folder,
				"is_private": 1,
				"content": "identical deduplicated bytes",
			}
		).insert()
		self.assertNotEqual(fresh_file.name, established_file.name)
		self.assertEqual(fresh_file.file_url, established_file.file_url)

		with self.assertRaises(DocumentFetchError):
			ingest_file(
				fresh_file.file_url,
				self.knowledge_base.name,
				title="Ambiguous Legacy Upload",
			)

		with patch("ai_fr_hg.ai.ingestion.enqueue_processing") as enqueue:
			created_name = ingest_file(
				fresh_file.file_url,
				self.knowledge_base.name,
				title="Fresh Exact Upload",
				file_record=fresh_file.name,
			)
		created = frappe.get_doc("AI Document", created_name)
		self.assertEqual(created.source_file_record, fresh_file.name)
		self.assertEqual(created.folder, upload_folder)
		self.assertEqual(frappe.db.get_value("File", established_file.name, "folder"), established_folder)
		self.assertEqual(
			frappe.db.get_value("AI Document", established_document.name, "source_file_record"),
			established_file.name,
		)
		enqueue.assert_called_once_with(created.name, requested_by="Administrator")

	def test_same_name_is_allowed_in_different_folders_but_not_the_same_folder(self):
		other = create_folder("Other", self.root)["name"]
		self.make_tree_document("Duplicate.txt", self.root)
		self.make_tree_document("Duplicate.txt", other)
		with self.assertRaises(frappe.DuplicateEntryError):
			self.make_tree_document("Duplicate.txt", self.root)

	def test_recursive_copy_preserves_structure_and_generates_collision_safe_root(self):
		source = create_folder("Project", self.root)["name"]
		child = create_folder("Child", source)["name"]
		self.make_tree_document("Root.txt", source)
		self.make_tree_document("Child.txt", child)

		first = copy_folder(source, self.root, enqueue=False)
		second = copy_folder(source, self.root, enqueue=False)
		self.assertEqual(first["name"], f"{self.root}/Project (Copy)")
		self.assertEqual(second["name"], f"{self.root}/Project (Copy 2)")
		self.assertTrue(frappe.db.exists("File", f"{first['name']}/Child"))
		self.assertEqual(
			frappe.db.count("AI Document", {"folder": ["like", f"{first['name']}%"]}),
			2,
		)

	def test_move_rejects_self_and_descendant_destinations_without_mutation(self):
		source = create_folder("Circular", self.root)["name"]
		child = create_folder("Child", source)["name"]
		with self.assertRaises(CircularFolderError):
			move_folder(source, child)
		self.assertTrue(frappe.db.exists("File", source))
		self.assertTrue(frappe.db.exists("File", child))

	def test_recursive_delete_runs_document_hooks_and_removes_complete_subtree(self):
		source = create_folder("Delete", self.root)["name"]
		child = create_folder("Child", source)["name"]
		document = self.make_tree_document("Delete.txt", child)
		frappe.get_doc(
			{
				"doctype": "AI Document Chunk",
				"document": document.name,
				"knowledge_base": self.knowledge_base.name,
				"chunk_index": 0,
				"content": "delete chunk",
			}
		).insert()

		result = delete_folder(source, recursive=True, enqueue=False)
		self.assertEqual(result["document_count"], 1)
		self.assertFalse(frappe.db.exists("File", source))
		self.assertFalse(frappe.db.exists("AI Document", document.name))
		self.assertEqual(frappe.db.count("AI Document Chunk", {"document": document.name}), 0)

	def test_recursive_delete_removes_a_represented_source_file_via_framework_policy(self):
		source = create_folder("Delete File Source", self.root)["name"]
		file_doc = frappe.get_doc(
			{
				"doctype": "File",
				"file_name": "represented.txt",
				"folder": source,
				"is_private": 1,
				"content": "represented source",
			}
		)
		file_doc.insert()
		document = frappe.get_doc(
			{
				"doctype": "AI Document",
				"title": "Represented Source",
				"organization_name": "represented.txt",
				"knowledge_base": self.knowledge_base.name,
				"source_type": "File",
				"source_file": file_doc.file_url,
				"source_file_record": file_doc.name,
				"folder": source,
				"status": "Draft",
			}
		)
		document.flags.skip_auto_process = True
		document.insert()

		result = delete_folder(source, recursive=True, enqueue=False)
		self.assertEqual(result["document_count"], 1)
		self.assertEqual(result["file_count"], 1)
		self.assertFalse(frappe.db.exists("AI Document", document.name))
		self.assertFalse(frappe.db.exists("File", file_doc.name))
		self.assertFalse(frappe.db.exists("File", source))

	def test_recursive_delete_orders_shared_in_subtree_source_file_after_documents(self):
		source = create_folder("Shared Source Delete", self.root)["name"]
		file_doc = frappe.get_doc(
			{
				"doctype": "File",
				"file_name": "shared-source.txt",
				"folder": source,
				"is_private": 1,
				"content": "shared authoritative source",
			}
		)
		file_doc.insert()

		documents = []
		for index in range(2):
			document = frappe.get_doc(
				{
					"doctype": "AI Document",
					"title": f"Shared Source {index}",
					"organization_name": f"shared-source-{index}.txt",
					"knowledge_base": self.knowledge_base.name,
					"source_type": "File",
					"source_file": file_doc.file_url,
					"source_file_record": file_doc.name,
					"folder": source,
					"source_folder": source,
					"status": "Draft",
				}
			)
			document.flags.skip_auto_process = True
			document.insert()
			documents.append(document.name)
		frappe.db.set_value(
			"File",
			file_doc.name,
			{
				"attached_to_doctype": "AI Document",
				"attached_to_name": documents[0],
				"attached_to_field": "source_file",
			},
			update_modified=False,
		)

		result = delete_folder(source, recursive=True, enqueue=False)
		self.assertEqual(result["document_count"], 2)
		self.assertEqual(result["file_count"], 1)
		self.assertFalse(frappe.db.exists("AI Document", documents[0]))
		self.assertFalse(frappe.db.exists("AI Document", documents[1]))
		self.assertFalse(frappe.db.exists("File", file_doc.name))

	def test_recursive_delete_refreshes_each_knowledge_base_once(self):
		source = create_folder("Batched Statistics Delete", self.root)["name"]
		self.make_tree_document("Statistics One.txt", source)
		self.make_tree_document("Statistics Two.txt", source)

		with patch("ai_fr_hg.ai.knowledge.update_knowledge_base_stats") as update_stats:
			result = delete_folder(source, recursive=True, enqueue=False)

		self.assertEqual(result["document_count"], 2)
		update_stats.assert_called_once_with(self.knowledge_base.name)

	def test_recursive_delete_rejects_files_hidden_from_the_document_tree(self):
		source = create_folder("Unmanaged File", self.root)["name"]
		file_doc = frappe.get_doc(
			{
				"doctype": "File",
				"file_name": "unmanaged.txt",
				"folder": source,
				"is_private": 1,
				"content": "must not be silently deleted",
			}
		)
		file_doc.insert()

		with self.assertRaises(frappe.LinkExistsError):
			delete_folder(source, recursive=True, enqueue=False)
		self.assertTrue(frappe.db.exists("File", file_doc.name))
		self.assertTrue(frappe.db.exists("File", source))

	def test_generic_file_delete_cannot_orphan_an_authoritative_document_source(self):
		file_doc = frappe.get_doc(
			{
				"doctype": "File",
				"file_name": "linked-source.txt",
				"folder": self.root,
				"is_private": 1,
				"content": "authoritative source",
			}
		)
		file_doc.insert()
		document = frappe.get_doc(
			{
				"doctype": "AI Document",
				"title": "Linked Source",
				"organization_name": "linked-source.txt",
				"knowledge_base": self.knowledge_base.name,
				"source_type": "File",
				"source_file": file_doc.file_url,
				"source_file_record": file_doc.name,
				"folder": self.root,
				"status": "Draft",
			}
		)
		document.flags.skip_auto_process = True
		document.insert()

		with self.assertRaises(frappe.LinkExistsError):
			delete_physical_file(file_doc.name)
		self.assertTrue(frappe.db.exists("File", file_doc.name))
		self.assertEqual(
			frappe.db.get_value("AI Document", document.name, "source_file_record"),
			file_doc.name,
		)

	def test_bulk_move_rolls_back_every_item_when_a_later_collision_fails(self):
		source = create_folder("Bulk Source", self.root)["name"]
		destination = create_folder("Bulk Destination", self.root)["name"]
		first = self.make_tree_document("First.txt", source)
		second = self.make_tree_document("Second.txt", source)
		self.make_tree_document("Second.txt", destination)

		with self.assertRaises(FolderAlreadyExistsError):
			bulk_move_nodes(
				[f"document::{first.name}", f"document::{second.name}"],
				destination,
				enqueue=False,
			)
		self.assertEqual(frappe.db.get_value("AI Document", first.name, "folder"), source)
		self.assertEqual(frappe.db.get_value("AI Document", second.name, "folder"), source)

	def test_fail_closed_audit_rolls_back_mutation(self):
		destination = create_folder("Audit Destination", self.root)["name"]
		document = self.make_tree_document("Audited.txt", self.root)
		with patch(
			"ai_fr_hg.ai.document_tree.write_audit_log", side_effect=RuntimeError("audit unavailable")
		):
			with self.assertRaisesRegex(RuntimeError, "audit unavailable"):
				move_document(document.name, destination)
		self.assertEqual(frappe.db.get_value("AI Document", document.name, "folder"), self.root)

	def test_stale_mutation_is_rejected(self):
		destination = create_folder("Stale Destination", self.root)["name"]
		document = self.make_tree_document("Stale.txt", self.root)
		with self.assertRaises(frappe.TimestampMismatchError):
			move_document(document.name, destination, expected_modified="2000-01-01 00:00:00")
		self.assertEqual(frappe.db.get_value("AI Document", document.name, "folder"), self.root)

	def test_queued_subtree_state_rejects_a_new_descendant(self):
		source = create_folder("Queued Source", self.root)["name"]
		destination = create_folder("Queued Destination", self.root)["name"]
		self.make_tree_document("Original.txt", source)
		folders, documents = _preflight_subtree(source, "write")
		expected_state = _subtree_state(folders, documents, _files_in_folders(folders))
		self.make_tree_document("Late Insert.txt", source)

		with self.assertRaises(frappe.TimestampMismatchError):
			move_folder(
				source,
				destination,
				enqueue=False,
				_expected_subtree_state=expected_state,
			)
		self.assertTrue(frappe.db.exists("File", source))
		self.assertFalse(frappe.db.exists("File", f"{destination}/Queued Source"))

	def test_legacy_queued_folder_bulk_move_rejects_a_late_descendant(self):
		source = create_folder("Legacy Bulk Source", self.root)["name"]
		destination = create_folder("Legacy Bulk Destination", self.root)["name"]
		self.make_tree_document("Original Legacy.txt", source)
		with patch("ai_fr_hg.ai.folders.frappe.enqueue") as enqueue_mock:
			bulk_move_files([source], destination, enqueue=True)
		expected_state = enqueue_mock.call_args.kwargs["expected_state"]
		self.make_tree_document("Late Legacy.txt", source)

		with self.assertRaises(frappe.TimestampMismatchError):
			bulk_move_files(
				[source],
				destination,
				enqueue=False,
				_expected_state=expected_state,
			)
		self.assertTrue(frappe.db.exists("File", source))
		self.assertFalse(frappe.db.exists("File", f"{destination}/Legacy Bulk Source"))

	def test_queued_bulk_state_rejects_a_changed_selected_subtree(self):
		source = create_folder("Bulk Queued Source", self.root)["name"]
		destination = create_folder("Bulk Queued Destination", self.root)["name"]
		self.make_tree_document("Original Bulk.txt", source)
		nodes = [source]
		folders, documents, files = _preflight_bulk_snapshot(
			nodes,
			permission="write",
			file_permission="write",
			include_document_files=True,
		)
		expected_state = _subtree_state(folders, documents, files)
		self.make_tree_document("Late Bulk Insert.txt", source)

		with self.assertRaises(frappe.TimestampMismatchError):
			bulk_move_nodes(
				nodes,
				destination,
				enqueue=False,
				_expected_bulk_state=expected_state,
			)
		self.assertTrue(frappe.db.exists("File", source))

	def test_lazy_children_are_typed_permission_filtered_and_paginated(self):
		for index in range(12):
			self.make_tree_document(f"Page {index:02}.txt", self.root)
		children = get_children(parent=self.root, limit=10)
		document_nodes = [row for row in children if row["node_type"] == "document"]
		self.assertEqual(len(document_nodes), 10)
		self.assertTrue(all(row["value"].startswith("document::") for row in document_nodes))
		self.assertEqual(children[-1]["node_type"], "page")
		next_page = get_children(parent=children[-1]["value"], limit=10)
		self.assertEqual(len([row for row in next_page if row["node_type"] == "document"]), 2)

	def test_api_requires_authenticated_doctype_permission(self):
		frappe.set_user("Guest")
		try:
			with self.assertRaises(frappe.PermissionError):
				get_children(parent="Home")
		finally:
			frappe.set_user("Administrator")
