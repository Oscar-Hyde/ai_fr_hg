# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Frappe integration coverage for this DocType and its canonical domain services."""

from unittest.mock import patch

import frappe

from ai_fr_hg.tests.integration_test_case import AIPlatformTestCase, stub_embeddings


class TestIngestionProgressCancellation(AIPlatformTestCase):
	def test_cancel_processing_is_durable_and_reprocessable(self):
		from ai_fr_hg.ai.ingestion import cancel_processing

		document = self.make_document("Cancellation Test Document", "cancel me")
		document.db_set(
			{
				"status": "Extracting",
				"processing_progress": 42,
				"processing_message": "Extracting",
				"processing_requested_by": frappe.session.user,
			},
			update_modified=False,
		)

		result = cancel_processing(document.name, requested_by=frappe.session.user)
		self.assertEqual(result["status"], "Cancelled")
		self.assertEqual(frappe.db.get_value("AI Document", document.name, "cancel_requested"), 1)
		self.assertEqual(frappe.db.get_value("AI Document", document.name, "status"), "Cancelled")

		# A new explicit process request clears the cancellation marker and starts
		# a recoverable queued attempt instead of leaving a terminal dead end.
		with patch("ai_fr_hg.ai.ingestion.frappe.enqueue") as enqueue:
			from ai_fr_hg.ai.ingestion import enqueue_processing

			enqueue_processing(document.name, requested_by=frappe.session.user)
		self.assertEqual(frappe.db.get_value("AI Document", document.name, "status"), "Queued")
		self.assertEqual(frappe.db.get_value("AI Document", document.name, "cancel_requested"), 0)
		self.assertEqual(frappe.db.get_value("AI Document", document.name, "processing_progress"), 0)
		enqueue.assert_called_once()

	def test_duplicate_cancel_is_idempotent(self):
		from ai_fr_hg.ai.ingestion import cancel_processing

		document = self.make_document("Duplicate Cancel Document", "cancel twice")
		document.db_set(
			{"status": "Queued", "processing_requested_by": frappe.session.user}, update_modified=False
		)
		first = cancel_processing(document.name)
		second = cancel_processing(document.name)
		self.assertTrue(first["cancelled"])
		self.assertFalse(second["cancelled"])
		self.assertEqual(second["status"], "Cancelled")

	def test_worker_observes_cancel_before_extraction(self):
		from ai_fr_hg.ai.ingestion import process_document

		document = self.make_document("Cancel Before Extract", "worker should stop")
		document.db_set(
			{
				"status": "Queued",
				"cancel_requested": 1,
				"processing_requested_by": frappe.session.user,
			},
			update_modified=False,
		)
		result = process_document(document.name, requested_by=frappe.session.user)
		self.assertEqual(result["status"], "Cancelled")
		document.reload()
		self.assertEqual(document.status, "Cancelled")

	def test_worker_observes_cancel_mid_extraction(self):
		from ai_fr_hg.ai.ingestion import process_document

		document = self.make_document("Cancel Mid Extract", "worker should stop after extract")
		document.db_set(
			{
				"status": "Queued",
				"processing_requested_by": frappe.session.user,
			},
			update_modified=False,
		)

		def extract_then_cancel(doc, authority):
			frappe.db.set_value("AI Document", doc.name, "cancel_requested", 1, update_modified=False)
			reader = type("R", (), {"label": "Text"})()
			result = frappe._dict(
				text=doc.content or "x",
				page_count=1,
				word_count=1,
				character_count=len(doc.content or "x"),
				metadata={},
				warnings=[],
			)
			return result, reader, b"x", "cancel.txt", "text/plain"

		with (
			patch("ai_fr_hg.ai.ingestion._extract_source", side_effect=extract_then_cancel),
			patch("ai_fr_hg.ai.ingestion.validate_source_access"),
		):
			result = process_document(document.name, requested_by=frappe.session.user)
		self.assertEqual(result["status"], "Cancelled")
		document.reload()
		self.assertEqual(document.status, "Cancelled")
		self.assertNotEqual(document.status, "Failed")

	def test_unrelated_user_cannot_cancel_processing(self):
		from ai_fr_hg.ai.exceptions import DocumentSourcePermissionError
		from ai_fr_hg.ai.ingestion import cancel_processing

		document = self.make_document("Foreign Cancel Document", "not yours")
		document.db_set(
			{"status": "Extracting", "processing_requested_by": frappe.session.user},
			update_modified=False,
		)
		email = "ing06-stranger@example.com"
		if not frappe.db.exists("User", email):
			frappe.get_doc(
				{
					"doctype": "User",
					"email": email,
					"first_name": "Stranger",
					"send_welcome_email": 0,
				}
			).insert(ignore_permissions=True)
		with self.assertRaises(DocumentSourcePermissionError):
			cancel_processing(document.name, requested_by=email)
		document.reload()
		self.assertEqual(document.status, "Extracting")

	def test_stale_in_flight_heartbeat_is_reaped_without_duplicate_start(self):
		from ai_fr_hg.ai.ingestion import process_pending_documents

		document = self.make_document("Stale Worker Document", "heartbeat expired")
		document.db_set(
			{
				"status": "Extracting",
				"processing_heartbeat": frappe.utils.add_to_date(frappe.utils.now_datetime(), minutes=-45),
				"processing_requested_by": frappe.session.user,
				"retry_count": 0,
			},
			update_modified=False,
		)

		with patch("ai_fr_hg.ai.ingestion.enqueue_processing") as enqueue:
			process_pending_documents()

		document.reload()
		self.assertEqual(document.status, "Failed")
		self.assertEqual(document.error_type, "StaleWorker")
		called = [
			call.args[0] if call.args else call.kwargs.get("document_name") for call in enqueue.call_args_list
		]
		self.assertEqual(called.count(document.name), 1)

	def test_live_heartbeat_is_not_reaped_or_requeued(self):
		"""ING-06: a worker still refreshing heartbeat must not be treated as dead."""
		from ai_fr_hg.ai.ingestion import process_pending_documents

		document = self.make_document("Live Worker Document", "heartbeat current")
		document.db_set(
			{
				"status": "Extracting",
				"processing_heartbeat": frappe.utils.now_datetime(),
				"processing_requested_by": frappe.session.user,
				"retry_count": 0,
			},
			update_modified=False,
		)

		with patch("ai_fr_hg.ai.ingestion.enqueue_processing") as enqueue:
			process_pending_documents()

		document.reload()
		self.assertEqual(document.status, "Extracting")
		self.assertNotEqual(document.error_type, "StaleWorker")
		called = [
			call.args[0] if call.args else call.kwargs.get("document_name") for call in enqueue.call_args_list
		]
		self.assertEqual(called.count(document.name), 0)

	def test_in_flight_enqueue_does_not_start_a_second_job(self):
		"""ING-06: recovery never duplicates while status is still in-flight."""
		from ai_fr_hg.ai.ingestion import enqueue_processing

		document = self.make_document("In Flight No Duplicate", "still extracting")
		document.db_set(
			{
				"status": "Chunking",
				"processing_job_id": f"ai-document::{document.name}",
				"processing_requested_by": frappe.session.user,
				"processing_heartbeat": frappe.utils.now_datetime(),
			},
			update_modified=False,
		)
		with patch("ai_fr_hg.ai.ingestion.frappe.enqueue") as enqueue:
			result = enqueue_processing(document.name, requested_by=frappe.session.user)
		self.assertEqual(result["status"], "Chunking")
		enqueue.assert_not_called()
		document.reload()
		self.assertEqual(document.status, "Chunking")


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
			frappe.db.count("AI Document Chunk", {"name": ["in", chunks], "embedding": ["!=", ""]}),
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

	def test_prepare_labels_and_stores_document_language(self):
		from ai_fr_hg.ai.ingestion import prepare_documents_for_turn

		english = self.make_document(
			"English Draft",
			"This is the report for the project and the team with the results from the meeting.",
		)
		english.db_set("status", "Draft", update_modified=False)
		bulgarian = self.make_document(
			"Bulgarian Draft",
			"Това е документ за България и за обработката на файловете, които са качени към базата.",  # noqa: RUF001 - Bulgarian fixture
		)
		bulgarian.db_set("status", "Draft", update_modified=False)

		with patch("ai_fr_hg.ai.ingestion.wait_for_indexed") as wait:
			_ready, extra = prepare_documents_for_turn([english.name, bulgarian.name])

		wait.assert_not_called()
		self.assertIn("language=English", extra)
		self.assertIn("language=Bulgarian", extra)
		self.assertEqual(frappe.db.get_value("AI Document", english.name, "language"), "en")
		self.assertEqual(frappe.db.get_value("AI Document", bulgarian.name, "language"), "bg")


class TestDocumentAPI(AIPlatformTestCase):
	def test_get_supported_formats(self):
		from ai_fr_hg.api.knowledge import get_supported_formats

		formats = get_supported_formats()
		self.assertIn("pdf", formats["extensions"])
		self.assertIn("eml", formats["extensions"])
		self.assertNotIn("msg", formats["extensions"])
		self.assertTrue(formats["by_reader"])

	def test_folder_source_is_rejected_server_side(self):
		document = frappe.get_doc(
			{
				"doctype": "AI Document",
				"title": "Unsupported Folder Source",
				"knowledge_base": self.knowledge_base.name,
				"source_type": "Folder",
			}
		)

		with self.assertRaises(frappe.ValidationError):
			document.validate_source()
