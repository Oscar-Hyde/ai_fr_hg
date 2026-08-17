# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Frappe integration coverage for this DocType and its canonical domain services."""

from unittest.mock import patch

import frappe

from ai_fr_hg.tests.integration_test_case import AIPlatformTestCase, stub_embeddings


class TestKnowledgeBaseDocType(AIPlatformTestCase):
	def test_overlap_must_be_smaller_than_chunk_size(self):
		doc = frappe.get_doc(
			{
				"doctype": "AI Knowledge Base",
				"knowledge_base_name": "Bad Chunking KB",
				"chunk_size": 200,
				"chunk_overlap": 300,
			}
		)
		with self.assertRaises(frappe.ValidationError):
			doc.insert(ignore_permissions=True)

	def test_embedding_model_must_be_embedding_type(self):
		doc = frappe.get_doc(
			{
				"doctype": "AI Knowledge Base",
				"knowledge_base_name": "Wrong Model KB",
				"embedding_model": self.chat_model.name,
			}
		)
		with self.assertRaises(frappe.ValidationError):
			doc.insert(ignore_permissions=True)


class TestRetrieval(AIPlatformTestCase):
	def test_keyword_search_finds_matching_document(self):
		from ai_fr_hg.ai.knowledge import index_document, retrieve

		document = self.make_document(
			"Refund Policy",
			"Our refund policy allows returns within thirty days of purchase. "
			"Customers must present the original receipt to obtain a refund. " * 6,
		)
		with stub_embeddings():
			index_document(document.name)

		results = retrieve(
			"refund policy",
			knowledge_bases=[self.knowledge_base.name],
			search_type="Keyword",
			top_k=5,
		)
		self.assertTrue(results)
		self.assertTrue(any("refund" in result.content.lower() for result in results))

	def test_semantic_search_returns_results(self):
		from ai_fr_hg.ai.knowledge import index_document, retrieve

		document = self.make_document(
			"Shipping Guide", "Shipping takes three to five business days domestically. " * 20
		)
		with stub_embeddings():
			index_document(document.name)
			results = retrieve(
				"how long does delivery take",
				knowledge_bases=[self.knowledge_base.name],
				search_type="Semantic",
				top_k=5,
				similarity_threshold=0,
			)
		self.assertIsInstance(results, list)

	def test_build_context_includes_numbered_sources(self):
		from ai_fr_hg.ai.knowledge import RetrievedChunk, build_context

		results = [
			RetrievedChunk(
				chunk="c1",
				document="DOC-1",
				document_title="Handbook",
				knowledge_base=self.knowledge_base.name,
				content="Employees receive twenty days of leave.",
				score=0.9,
			),
			RetrievedChunk(
				chunk="c2",
				document="DOC-2",
				document_title="Policy",
				knowledge_base=self.knowledge_base.name,
				content="Overtime must be approved in advance.",
				score=0.8,
			),
		]
		context = build_context(results)

		self.assertIn("[1]", context)
		self.assertIn("[2]", context)
		self.assertIn("Handbook", context)
		self.assertIn("twenty days", context)

	def test_build_context_respects_character_budget(self):
		from ai_fr_hg.ai.knowledge import RetrievedChunk, build_context

		results = [
			RetrievedChunk(
				chunk=f"c{i}",
				document=f"DOC-{i}",
				document_title=f"Doc {i}",
				knowledge_base=self.knowledge_base.name,
				content="x" * 2000,
				score=1.0,
			)
			for i in range(20)
		]
		context = build_context(results, max_characters=3000)
		self.assertLessEqual(len(context), 3600)

	def test_retrieval_can_be_scoped_to_documents(self):
		"""`documents` must restrict results to the given records, so "answer
		from the file I just uploaded" does not draw on the whole knowledge base."""
		from ai_fr_hg.ai.knowledge import index_document, retrieve

		alpha = self.make_document("Alpha Doc", "The system policy covers apples and refunds. " * 30)
		beta = self.make_document("Beta Doc", "The system policy covers bananas and returns. " * 30)
		with stub_embeddings():
			index_document(alpha.name)
			index_document(beta.name)

		# Both documents mention "system policy", so unscoped retrieval returns
		# chunks from either; scoping to `alpha` must keep only its chunks.
		results = retrieve(
			"system policy",
			knowledge_bases=[self.knowledge_base.name],
			search_type="Keyword",
			top_k=10,
			documents=[alpha.name],
		)
		self.assertTrue(results)
		self.assertTrue(all(result.document == alpha.name for result in results))


class TestKnowledgeBaseAPI(AIPlatformTestCase):
	def test_malformed_knowledge_import_fails_before_mutation_or_queueing(self):
		from ai_fr_hg.api.admin import import_knowledge_base

		file_doc = frappe.get_doc(
			{
				"doctype": "File",
				"file_name": "malformed-ai-knowledge-export.json",
				"is_private": 1,
				"content": frappe.as_json(
					{
						"knowledge_base": {"name": self.knowledge_base.name},
						"documents": [{"title": "Missing Content"}],
					}
				),
			}
		).insert(ignore_permissions=True)
		before = frappe.db.count("AI Document", {"knowledge_base": self.knowledge_base.name})
		with (
			patch("frappe.enqueue") as enqueue,
			self.assertRaises(frappe.ValidationError),
		):
			import_knowledge_base(file_doc.file_url, self.knowledge_base.name)

		self.assertEqual(
			frappe.db.count("AI Document", {"knowledge_base": self.knowledge_base.name}),
			before,
		)
		enqueue.assert_not_called()

	def test_document_queue_and_reindex_write_provenance(self):
		from ai_fr_hg.ai.ingestion import enqueue_processing
		from ai_fr_hg.api.knowledge import reindex_knowledge_base

		knowledge_base = frappe.get_doc(
			{
				"doctype": "AI Knowledge Base",
				"knowledge_base_name": "Queued Provenance Knowledge Base",
				"enabled": 1,
				"is_public": 1,
				"chunk_size": 400,
				"chunk_overlap": 40,
			}
		).insert(ignore_permissions=True)
		document = frappe.get_doc(
			{
				"doctype": "AI Document",
				"title": "Queued Provenance",
				"knowledge_base": knowledge_base.name,
				"source_type": "Text",
				"content": "Queue this document with strict provenance.",
				"status": "Draft",
			}
		)
		document.flags.skip_auto_process = True
		document.insert(ignore_permissions=True)
		with patch("frappe.enqueue"):
			queued = enqueue_processing(document.name)

		self.assertEqual(queued["status"], "Queued")
		self.assertTrue(
			frappe.db.exists(
				"AI Audit Log",
				{
					"action": "Document Processing Queued",
					"reference_doctype": "AI Document",
					"reference_name": document.name,
				},
			)
		)

		frappe.db.set_value("AI Document", document.name, "status", "Indexed")
		with patch(
			"ai_fr_hg.ai_knowledge.doctype.ai_document.ai_document.AIDocument.reprocess",
			return_value={"document": document.name, "status": "Queued"},
		):
			result = reindex_knowledge_base(knowledge_base.name)

		self.assertEqual(result["queued"], 1)
		self.assertEqual(
			frappe.db.get_value("AI Knowledge Base", knowledge_base.name, "index_status"),
			"Indexing",
		)
		self.assertTrue(
			frappe.db.exists(
				"AI Audit Log",
				{
					"action": "Knowledge Base Reindex Queued",
					"reference_doctype": "AI Knowledge Base",
					"reference_name": knowledge_base.name,
				},
			)
		)

	def test_get_knowledge_overview(self):
		from ai_fr_hg.api.knowledge import get_knowledge_overview

		overview = get_knowledge_overview()
		self.assertIn("totals", overview)
		self.assertIn("knowledge_bases", overview)
