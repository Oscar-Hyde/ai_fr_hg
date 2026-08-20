# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Frappe integration coverage for this DocType and its canonical domain services."""

from unittest.mock import patch

import frappe

from ai_fr_hg.ai.vector import encode_vector, normalize
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

	def test_scoped_retrieval_ignores_similarity_threshold(self):
		"""Attach→ask must not drop the uploaded file because cosine is low."""
		from ai_fr_hg.ai.knowledge import index_document, retrieve

		document = self.make_document(
			"Threshold Policy",
			"Refunds are allowed within thirty days of purchase with the original receipt. " * 12,
		)
		with stub_embeddings():
			index_document(document.name)

		results = retrieve(
			"What is the refund policy?",
			knowledge_bases=[self.knowledge_base.name],
			search_type="Hybrid",
			top_k=5,
			similarity_threshold=0.99,
			documents=[document.name],
		)
		self.assertTrue(results)
		self.assertTrue(all(result.document == document.name for result in results))
		self.assertTrue(any("Refunds" in result.content for result in results))

	def _insert_chunk(self, document, content, index, embedding, *, knowledge_base=None, model=None):
		row = frappe.get_doc(
			{
				"doctype": "AI Document Chunk",
				"document": document.name,
				"knowledge_base": knowledge_base or document.knowledge_base,
				"chunk_index": index,
				"content": content,
				"checksum": frappe.generate_hash(f"{content}:{index}", length=32),
				"embedding": encode_vector(embedding) if embedding else None,
				"embedding_model": model or self.embedding_model.name,
				"embedding_dimensions": len(embedding) if embedding else 0,
				"embedding_format": "Base64 Float32",
				"embedding_norm": 1.0,
				"character_count": len(content),
				"token_count": max(1, len(content) // 4),
			}
		)
		row.flags.ignore_permissions = True
		row.insert(ignore_permissions=True)
		return row

	def test_semantic_search_finds_the_only_relevant_chunk_beyond_200(self):
		"""RET-01: the needle beyond the old 200-row cap must still be found."""
		from ai_fr_hg.ai.knowledge import retrieve

		document = self.make_document("Corpus Boundary", "Needle document body. " * 8)
		needle = normalize([1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0])
		filler = normalize([0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0])
		self._insert_chunk(document, "THE UNIQUE NEEDLE PASSAGE about zircon refunds.", 0, needle)
		for index in range(1, 221):
			self._insert_chunk(document, f"unrelated filler passage number {index}", index, filler)

		query = "THE UNIQUE NEEDLE PASSAGE about zircon refunds."

		def fake_embed(texts, model=None, operation="Embedding", **kwargs):
			return [list(needle) for _ in texts]

		with (
			patch("ai_fr_hg.ai.knowledge.run_embedding", side_effect=fake_embed),
			patch("ai_fr_hg.ai.engine.run_embedding", side_effect=fake_embed),
		):
			results = retrieve(
				query,
				knowledge_bases=[self.knowledge_base.name],
				search_type="Semantic",
				top_k=5,
				similarity_threshold=0,
				log=False,
			)
		self.assertTrue(results)
		self.assertTrue(any("UNIQUE NEEDLE PASSAGE" in result.content for result in results))

	def test_keyword_search_finds_the_only_relevant_chunk_beyond_500(self):
		"""RET-02: a high-score match outside the old 500-row LIKE cap is found."""
		from ai_fr_hg.ai.knowledge import retrieve

		document = self.make_document("Keyword Corpus", "Keyword document body. " * 8)
		self._insert_chunk(
			document,
			"alpha uniqueneedletermxyz appears only here",
			0,
			normalize([0.1] * 8),
		)
		for index in range(1, 521):
			self._insert_chunk(document, f"alpha filler passage {index}", index, normalize([0.2] * 8))

		results = retrieve(
			"alpha uniqueneedletermxyz",
			knowledge_bases=[self.knowledge_base.name],
			search_type="Keyword",
			top_k=5,
			log=False,
		)
		self.assertTrue(results)
		self.assertTrue(any("uniqueneedletermxyz" in result.content for result in results))

	def test_mixed_embedding_models_are_grouped_not_compared(self):
		"""RET-03: incompatible dimensions are never scored against one query vector."""
		from ai_fr_hg.ai.knowledge import retrieve

		other_model = self.ensure_model("Test Embedding Model Dim4", "Embedding")
		frappe.db.set_value("AI Model", other_model.name, "embedding_dimensions", 4)
		frappe.db.set_value("AI Model", self.embedding_model.name, "embedding_dimensions", 8)

		kb_b = frappe.get_doc(
			{
				"doctype": "AI Knowledge Base",
				"knowledge_base_name": "Mixed Model KB B",
				"enabled": 1,
				"is_public": 1,
				"chunk_size": 400,
				"chunk_overlap": 40,
				"embedding_model": other_model.name,
			}
		).insert(ignore_permissions=True)

		doc_a = self.make_document("Mixed A", "alpha corpus in eight dimensions. " * 8)
		doc_b = frappe.get_doc(
			{
				"doctype": "AI Document",
				"title": "Mixed B",
				"knowledge_base": kb_b.name,
				"source_type": "Text",
				"content": "beta corpus in four dimensions. " * 8,
				"status": "Draft",
			}
		)
		doc_b.flags.skip_auto_process = True
		doc_b.insert(ignore_permissions=True)

		vec8 = normalize([1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0])
		vec4 = normalize([1.0, 0.0, 0.0, 0.0])
		self._insert_chunk(doc_a, "eight-dim filler", 0, vec8, model=self.embedding_model.name)
		self._insert_chunk(
			doc_b,
			"four-dim NEEDLE mixed-model zircon",
			0,
			vec4,
			knowledge_base=kb_b.name,
			model=other_model.name,
		)

		def fake_embed(texts, model=None, operation="Embedding", **kwargs):
			if model == other_model.name:
				return [list(vec4) for _ in texts]
			return [list(vec8) for _ in texts]

		with (
			patch("ai_fr_hg.ai.knowledge.run_embedding", side_effect=fake_embed),
			patch("ai_fr_hg.ai.engine.run_embedding", side_effect=fake_embed),
		):
			results, diagnostics = retrieve(
				"NEEDLE mixed-model zircon",
				knowledge_bases=[self.knowledge_base.name, kb_b.name],
				search_type="Semantic",
				top_k=5,
				similarity_threshold=0,
				log=False,
				with_diagnostics=True,
			)
		self.assertTrue(any("NEEDLE mixed-model" in result.content for result in results))
		self.assertTrue(diagnostics["mixed_embedding_models"])
		dims = {item["dimensions"] for item in diagnostics["embedding_models"]}
		self.assertIn(4, dims)
		self.assertIn(8, dims)

	def test_kb_threshold_and_weight_change_results(self):
		"""RET-04: per-KB threshold and agent weight actually affect ranking."""
		from ai_fr_hg.ai.knowledge import retrieve

		kb_low = frappe.get_doc(
			{
				"doctype": "AI Knowledge Base",
				"knowledge_base_name": "Policy Low Threshold",
				"enabled": 1,
				"is_public": 1,
				"chunk_size": 400,
				"chunk_overlap": 40,
				"embedding_model": self.embedding_model.name,
				"top_k": 6,
				"similarity_threshold": 0.05,
			}
		).insert(ignore_permissions=True)
		kb_high = frappe.get_doc(
			{
				"doctype": "AI Knowledge Base",
				"knowledge_base_name": "Policy High Threshold",
				"enabled": 1,
				"is_public": 1,
				"chunk_size": 400,
				"chunk_overlap": 40,
				"embedding_model": self.embedding_model.name,
				"top_k": 6,
				"similarity_threshold": 0.99,
			}
		).insert(ignore_permissions=True)

		doc_low = frappe.get_doc(
			{
				"doctype": "AI Document",
				"title": "Low Threshold Doc",
				"knowledge_base": kb_low.name,
				"source_type": "Text",
				"content": "sharedtopic low-threshold body. " * 8,
				"status": "Draft",
			}
		)
		doc_low.flags.skip_auto_process = True
		doc_low.insert(ignore_permissions=True)
		doc_high = frappe.get_doc(
			{
				"doctype": "AI Document",
				"title": "High Threshold Doc",
				"knowledge_base": kb_high.name,
				"source_type": "Text",
				"content": "sharedtopic high-threshold body. " * 8,
				"status": "Draft",
			}
		)
		doc_high.flags.skip_auto_process = True
		doc_high.insert(ignore_permissions=True)

		strong = normalize([1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0])
		weak = normalize([0.2, 0.98, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0])
		self._insert_chunk(doc_low, "sharedtopic low", 0, weak, knowledge_base=kb_low.name)
		self._insert_chunk(doc_high, "sharedtopic high UNIQUEHIGH", 0, strong, knowledge_base=kb_high.name)

		def fake_embed(texts, model=None, operation="Embedding", **kwargs):
			return [list(strong) for _ in texts]

		with (
			patch("ai_fr_hg.ai.knowledge.run_embedding", side_effect=fake_embed),
			patch("ai_fr_hg.ai.engine.run_embedding", side_effect=fake_embed),
		):
			strict = retrieve(
				"sharedtopic",
				knowledge_bases=[kb_low.name, kb_high.name],
				search_type="Semantic",
				top_k=5,
				log=False,
			)
			overridden = retrieve(
				"sharedtopic",
				knowledge_bases=[kb_low.name, kb_high.name],
				search_type="Semantic",
				top_k=5,
				similarity_threshold=0,
				log=False,
			)
			weighted = retrieve(
				"sharedtopic",
				knowledge_bases=[kb_low.name, kb_high.name],
				search_type="Keyword",
				top_k=2,
				weights={kb_low.name: 10.0, kb_high.name: 0.01},
				log=False,
			)

		self.assertTrue(any("UNIQUEHIGH" in result.content for result in strict))
		self.assertTrue(any(result.knowledge_base == kb_low.name for result in overridden))
		self.assertEqual(weighted[0].knowledge_base, kb_low.name)

	def test_folder_scope_does_not_match_sibling_prefix(self):
		"""RET-07: Home/A must not retrieve documents in Home/AB."""
		from ai_fr_hg.ai.knowledge import retrieve

		in_a = self.make_document("Folder A Doc", "folder-a-only zircon policy. " * 10)
		in_ab = self.make_document("Folder AB Doc", "folder-ab-only zircon policy. " * 10)
		frappe.db.set_value("AI Document", in_a.name, {"folder": "Home/A", "source_folder": "Home/A"})
		frappe.db.set_value("AI Document", in_ab.name, {"folder": "Home/AB", "source_folder": "Home/AB"})
		vec = normalize([1.0] * 8)
		self._insert_chunk(in_a, "folder-a-only zircon policy", 0, vec)
		self._insert_chunk(in_ab, "folder-ab-only zircon policy", 0, vec)

		results = retrieve(
			"zircon policy",
			knowledge_bases=[self.knowledge_base.name],
			search_type="Keyword",
			top_k=10,
			folder="Home/A",
			log=False,
		)
		self.assertTrue(results)
		self.assertTrue(all(result.document == in_a.name for result in results))
		self.assertFalse(any(result.document == in_ab.name for result in results))

	def test_build_context_truncates_oversized_first_block(self):
		"""RET-06: an oversized first passage still yields useful context."""
		from ai_fr_hg.ai.knowledge import RetrievedChunk, build_context

		results = [
			RetrievedChunk(
				chunk="c1",
				document="DOC-1",
				document_title="Huge",
				knowledge_base=self.knowledge_base.name,
				content="x" * 20000,
				score=1.0,
			)
		]
		context = build_context(results, max_characters=800)
		self.assertTrue(context)
		self.assertIn("[1]", context)
		self.assertLessEqual(len(context), 800)

	def test_search_api_returns_diagnostics(self):
		from ai_fr_hg.ai.knowledge import index_document
		from ai_fr_hg.api.knowledge import search

		document = self.make_document(
			"Diagnostics Policy",
			"Refunds are allowed within thirty days of purchase with the original receipt. " * 12,
		)
		with stub_embeddings():
			index_document(document.name)
		payload = search(
			"refund policy",
			knowledge_bases=[self.knowledge_base.name],
			top_k=5,
			search_type="Keyword",
		)
		self.assertIn("diagnostics", payload)
		self.assertGreaterEqual(payload["diagnostics"]["corpus_size"], 1)
		self.assertEqual(payload["diagnostics"]["reranker"], "unsupported")
		self.assertIn("retrieval_strategy", payload["diagnostics"])


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
