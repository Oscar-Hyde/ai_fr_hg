# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Frappe integration coverage for this DocType and its canonical domain services."""

from unittest.mock import patch

import frappe

from ai_fr_hg.tests.integration_test_case import AIPlatformTestCase, stub_embeddings


class TestIndexing(AIPlatformTestCase):
	def test_document_is_chunked_and_embedded(self):
		from ai_fr_hg.ai.knowledge import index_document

		content = "\n\n".join(
			f"Section {i}. The quick brown fox jumps over the lazy dog repeatedly. " * 5 for i in range(6)
		)
		document = self.make_document("Indexing Test Document", content)

		with stub_embeddings():
			index_document(document.name)

		document.reload()
		self.assertEqual(document.status, "Indexed")
		self.assertGreater(document.chunk_count, 1)
		self.assertEqual(document.chunk_count, document.embedded_chunk_count)

		chunks = frappe.get_all(
			"AI Document Chunk",
			filters={"document": document.name},
			fields=["name", "embedding", "chunk_index", "embedding_dimensions"],
			order_by="chunk_index asc",
		)
		self.assertEqual(len(chunks), document.chunk_count)
		for chunk in chunks:
			self.assertTrue(chunk.embedding)
			self.assertEqual(chunk.embedding_dimensions, 8)

	def test_embedding_batch_is_not_partially_written_when_one_vector_is_malformed(self):
		from ai_fr_hg.ai.exceptions import DocumentProcessingError
		from ai_fr_hg.ai.knowledge import embed_chunks, index_document

		document = self.make_document(
			"Atomic Embedding Document",
			"\n\n".join(f"Section {i}. Distinct enterprise content. " * 12 for i in range(8)),
		)
		index_document(document.name, embed=False)
		chunks = frappe.get_all(
			"AI Document Chunk",
			filters={"document": document.name},
			pluck="name",
			order_by="chunk_index asc",
		)
		self.assertGreaterEqual(len(chunks), 2)
		vectors = [[1.0] * 8 for _ in chunks]
		vectors[1] = [1.0] * 7

		with (
			patch("ai_fr_hg.ai.knowledge.EMBED_BATCH_SIZE", len(chunks)),
			patch("ai_fr_hg.ai.knowledge.run_embedding", return_value=vectors),
			self.assertRaises(DocumentProcessingError),
		):
			embed_chunks(chunks, model=self.embedding_model.name)

		self.assertEqual(
			frappe.db.count(
				"AI Document Chunk", {"name": ["in", chunks], "embedding": ["!=", ""]}
			),
			0,
		)

	def test_missing_and_stale_embeddings_are_backfilled(self):
		from ai_fr_hg.ai.knowledge import index_document

		document = self.make_document("Embedding Backfill Document", "Backfill this content. " * 80)
		index_document(document.name, embed=False)
		chunks = frappe.get_all(
			"AI Document Chunk",
			filters={"document": document.name},
			pluck="name",
			order_by="chunk_index asc",
		)
		self.assertTrue(chunks)

		with stub_embeddings() as first_embed:
			index_document(document.name)
		self.assertEqual(sum(len(call.args[0]) for call in first_embed.call_args_list), len(chunks))

		stale = chunks[0]
		frappe.db.set_value(
			"AI Document Chunk",
			stale,
			{"embedding_model": None, "embedding_dimensions": 0},
			update_modified=False,
		)
		with stub_embeddings() as second_embed:
			index_document(document.name)
		self.assertEqual(sum(len(call.args[0]) for call in second_embed.call_args_list), 1)
		self.assertEqual(
			frappe.db.get_value("AI Document Chunk", stale, "embedding_model"), self.embedding_model.name
		)
		document.reload()
		self.assertEqual(document.chunk_count, len(chunks))
		self.assertEqual(document.embedded_chunk_count, len(chunks))

	def test_reindexing_replaces_chunks(self):
		from ai_fr_hg.ai.knowledge import index_document

		document = self.make_document("Reindex Document", "Original content here. " * 40)

		with stub_embeddings():
			index_document(document.name)
		document.reload()
		first_count = document.chunk_count

		document.db_set("content", "Completely different content now. " * 80)
		with stub_embeddings():
			index_document(document.name, force=True)
		document.reload()

		self.assertGreater(document.chunk_count, 0)
		total = frappe.db.count("AI Document Chunk", {"document": document.name})
		self.assertEqual(total, document.chunk_count)
		self.assertNotEqual((first_count, total), (0, 0))

	def test_knowledge_base_stats_update(self):
		from ai_fr_hg.ai.knowledge import index_document, update_knowledge_base_stats

		document = self.make_document("Stats Document", "Statistics content. " * 50)
		with stub_embeddings():
			index_document(document.name)

		update_knowledge_base_stats(self.knowledge_base.name)
		kb = frappe.get_doc("AI Knowledge Base", self.knowledge_base.name)
		self.assertGreater(kb.chunk_count, 0)
		self.assertGreater(kb.document_count, 0)


class TestIngestionWait(AIPlatformTestCase):
	def test_wait_returns_immediately_for_indexed_document(self):
		from ai_fr_hg.ai.ingestion import wait_for_indexed

		document = self.make_document("Wait Indexed Doc", "content")
		document.db_set("status", "Indexed", update_modified=False)

		with patch("ai_fr_hg.ai.ingestion.time.sleep") as sleep:
			statuses = wait_for_indexed([document.name], timeout=5)

		sleep.assert_not_called()
		self.assertEqual(statuses.get(document.name), "Indexed")

	def test_wait_respects_timeout(self):
		from ai_fr_hg.ai.ingestion import wait_for_indexed

		document = self.make_document("Wait Queued Doc", "content")
		document.db_set("status", "Queued", update_modified=False)

		# A zero deadline must return after its first status read without sleep.
		# Do not patch ``time.monotonic``: it is a process-global stdlib module and
		# changing it can stall Frappe/Redis internals running alongside tests.
		with patch("ai_fr_hg.ai.ingestion.time.sleep") as sleep:
			statuses = wait_for_indexed([document.name], timeout=0)

		sleep.assert_not_called()
		self.assertEqual(statuses.get(document.name), "Queued")

	def test_prepare_uses_extracted_text_without_waiting(self):
		from ai_fr_hg.ai.ingestion import prepare_documents_for_turn

		indexed = self.make_document("Prepare Indexed Doc", "indexed body")
		indexed.db_set("status", "Indexed", update_modified=False)
		draft = self.make_document("Prepare Draft Doc", "draft body ready now")
		draft.db_set("status", "Draft", update_modified=False)

		with patch("ai_fr_hg.ai.ingestion.wait_for_indexed") as wait:
			ready, extra = prepare_documents_for_turn([indexed.name, draft.name])

		wait.assert_not_called()
		self.assertEqual(ready, [indexed.name])
		self.assertIn("draft body ready now", extra)
		self.assertNotIn("indexed body", extra)


class TestDocumentAPI(AIPlatformTestCase):
	def test_get_supported_formats(self):
		from ai_fr_hg.api.knowledge import get_supported_formats

		formats = get_supported_formats()
		self.assertIn("pdf", formats["extensions"])
		self.assertTrue(formats["by_reader"])
