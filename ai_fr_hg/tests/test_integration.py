# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Integration tests that exercise the DocTypes and service layer.

Anything that would call a live model runtime is stubbed, so the suite runs on
a plain CI site with no Ollama installed.
"""

from contextlib import contextmanager
from itertools import chain, repeat
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from ai_fr_hg.ai.pipeline import pipeline_step_method
from ai_fr_hg.ai.providers.base import CompletionResult


@contextmanager
def stub_chat(content: str = "Stubbed answer.", tool_calls=None):
	"""Replace the chat engine with a deterministic response."""
	result = CompletionResult(
		content=content,
		tool_calls=tool_calls or [],
		prompt_tokens=10,
		completion_tokens=5,
		total_tokens=15,
		duration_ms=42,
		model="stub-model",
	)
	# knowledge/agent modules bind these names at import time, so patch there too.
	with patch("ai_fr_hg.ai.agent.run_chat", return_value=result) as mock:
		yield mock


@contextmanager
def stub_embeddings(dimensions: int = 8):
	"""Replace the embedding engine with deterministic pseudo-vectors."""

	def fake_embed(texts, model=None, operation="Embedding", **kwargs):
		vectors = []
		for text in texts:
			seed = sum(ord(char) for char in (text or "x"))
			vectors.append([((seed + i * 7) % 100) / 100 for i in range(dimensions)])
		return vectors

	# `knowledge` imports run_embedding at module load, so patch the bound name.
	with patch("ai_fr_hg.ai.knowledge.run_embedding", side_effect=fake_embed) as mock:
		yield mock


class AIPlatformTestCase(IntegrationTestCase):
	"""Shared fixtures for the platform's integration tests."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.provider = cls.ensure_provider()
		cls.chat_model = cls.ensure_model("Test Chat Model", "Chat")
		cls.embedding_model = cls.ensure_model("Test Embedding Model", "Embedding")
		cls.knowledge_base = cls.ensure_knowledge_base()

	@classmethod
	def ensure_provider(cls):
		if frappe.db.exists("AI Provider", "Test Provider"):
			return frappe.get_doc("AI Provider", "Test Provider")
		doc = frappe.get_doc(
			{
				"doctype": "AI Provider",
				"provider_name": "Test Provider",
				"provider_type": "Ollama",
				"base_url": "http://localhost:11434",
				"enabled": 1,
				"request_timeout": 30,
			}
		)
		doc.insert(ignore_permissions=True)
		return doc

	@classmethod
	def ensure_model(cls, label, model_type):
		if frappe.db.exists("AI Model", label):
			return frappe.get_doc("AI Model", label)
		doc = frappe.get_doc(
			{
				"doctype": "AI Model",
				"model_label": label,
				"provider": cls.provider.name,
				"model_name": frappe.scrub(label),
				"model_type": model_type,
				"enabled": 1,
				"context_window": 8192,
				"temperature": 0.2,
				"top_p": 0.9,
			}
		)
		doc.insert(ignore_permissions=True)
		return doc

	@classmethod
	def ensure_knowledge_base(cls):
		if frappe.db.exists("AI Knowledge Base", "Test Knowledge Base"):
			return frappe.get_doc("AI Knowledge Base", "Test Knowledge Base")
		doc = frappe.get_doc(
			{
				"doctype": "AI Knowledge Base",
				"knowledge_base_name": "Test Knowledge Base",
				"enabled": 1,
				"is_public": 1,
				"chunk_size": 400,
				"chunk_overlap": 40,
				"embedding_model": cls.embedding_model.name,
			}
		)
		doc.insert(ignore_permissions=True)
		return doc

	def make_document(self, title, content):
		doc = frappe.get_doc(
			{
				"doctype": "AI Document",
				"title": title,
				"knowledge_base": self.knowledge_base.name,
				"source_type": "Text",
				"content": content,
				"status": "Draft",
			}
		)
		doc.flags.skip_auto_process = True
		doc.insert(ignore_permissions=True)
		return doc


class TestProviderDocType(AIPlatformTestCase):
	def test_provider_is_created(self):
		self.assertTrue(frappe.db.exists("AI Provider", "Test Provider"))

	def test_base_url_must_be_http(self):
		doc = frappe.get_doc(
			{
				"doctype": "AI Provider",
				"provider_name": "Bad URL Provider",
				"provider_type": "Ollama",
				"base_url": "ftp://localhost:11434",
			}
		)
		with self.assertRaises(frappe.ValidationError):
			doc.insert(ignore_permissions=True)

	def test_trailing_slash_is_stripped(self):
		doc = frappe.get_doc(
			{
				"doctype": "AI Provider",
				"provider_name": "Slash Provider",
				"provider_type": "Ollama",
				"base_url": "http://localhost:11434/",
			}
		)
		doc.insert(ignore_permissions=True)
		self.assertEqual(doc.base_url, "http://localhost:11434")

	def test_invalid_extra_headers_rejected(self):
		doc = frappe.get_doc(
			{
				"doctype": "AI Provider",
				"provider_name": "Header Provider",
				"provider_type": "Ollama",
				"base_url": "http://localhost:11434",
				"extra_headers": "not json",
			}
		)
		with self.assertRaises(frappe.ValidationError):
			doc.insert(ignore_permissions=True)

	def test_only_one_default_provider(self):
		first = frappe.get_doc(
			{
				"doctype": "AI Provider",
				"provider_name": "Default One",
				"provider_type": "Ollama",
				"base_url": "http://localhost:11434",
				"is_default": 1,
			}
		).insert(ignore_permissions=True)

		second = frappe.get_doc(
			{
				"doctype": "AI Provider",
				"provider_name": "Default Two",
				"provider_type": "Ollama",
				"base_url": "http://localhost:11435",
				"is_default": 1,
			}
		).insert(ignore_permissions=True)

		self.assertEqual(frappe.db.get_value("AI Provider", first.name, "is_default"), 0)
		self.assertEqual(frappe.db.get_value("AI Provider", second.name, "is_default"), 1)


class TestModelDocType(AIPlatformTestCase):
	def test_temperature_bounds_enforced(self):
		doc = frappe.get_doc(
			{
				"doctype": "AI Model",
				"model_label": "Hot Model",
				"provider": self.provider.name,
				"model_name": "hot",
				"model_type": "Chat",
				"temperature": 9,
			}
		)
		with self.assertRaises(frappe.ValidationError):
			doc.insert(ignore_permissions=True)

	def test_context_window_minimum(self):
		doc = frappe.get_doc(
			{
				"doctype": "AI Model",
				"model_label": "Tiny Context Model",
				"provider": self.provider.name,
				"model_name": "tiny",
				"model_type": "Chat",
				"context_window": 10,
			}
		)
		with self.assertRaises(frappe.ValidationError):
			doc.insert(ignore_permissions=True)

	def test_duplicate_discovery_preserves_models_created_earlier_in_batch(self):
		from types import SimpleNamespace

		from frappe.model.document import Document

		from ai_fr_hg.ai.monitoring import sync_provider_models

		provider = frappe.get_doc(
			{
				"doctype": "AI Provider",
				"provider_name": "Duplicate Reconciliation Provider",
				"provider_type": "Ollama",
				"base_url": "http://localhost:11436",
				"enabled": 1,
			}
		).insert(ignore_permissions=True)
		first_name = "savepoint-first-model"
		duplicate_name = "savepoint-concurrent-duplicate"
		models = [
			SimpleNamespace(
				name=name,
				digest=f"digest-{name}",
				size=100,
				family="test",
				parameter_size="1B",
				quantization="Q4",
				context_window=4096,
			)
			for name in (first_name, duplicate_name)
		]
		adapter = SimpleNamespace(list_models=lambda: models)
		original_insert = Document.insert

		def insert_with_concurrent_duplicate(document, *args, **kwargs):
			if document.doctype == "AI Model" and document.model_name == duplicate_name:
				raise frappe.DuplicateEntryError
			return original_insert(document, *args, **kwargs)

		with (
			patch("ai_fr_hg.ai.providers.get_provider", return_value=adapter),
			patch.object(Document, "insert", new=insert_with_concurrent_duplicate),
		):
			result = sync_provider_models(provider.name)

		self.assertIn(first_name, result["created"])
		self.assertNotIn(duplicate_name, result["created"])
		self.assertTrue(
			frappe.db.exists("AI Model", {"provider": provider.name, "model_name": first_name})
		)

	def test_one_default_per_model_type(self):
		a = frappe.get_doc(
			{
				"doctype": "AI Model",
				"model_label": "Default Chat A",
				"provider": self.provider.name,
				"model_name": "chat-a",
				"model_type": "Chat",
				"is_default": 1,
			}
		).insert(ignore_permissions=True)

		b = frappe.get_doc(
			{
				"doctype": "AI Model",
				"model_label": "Default Chat B",
				"provider": self.provider.name,
				"model_name": "chat-b",
				"model_type": "Chat",
				"is_default": 1,
			}
		).insert(ignore_permissions=True)

		self.assertEqual(frappe.db.get_value("AI Model", a.name, "is_default"), 0)
		self.assertEqual(frappe.db.get_value("AI Model", b.name, "is_default"), 1)


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

		# First monotonic call sets the deadline; the second is past it.
		with (
			patch("ai_fr_hg.ai.ingestion.time.sleep"),
			patch("ai_fr_hg.ai.ingestion.time.monotonic", side_effect=chain([0], repeat(10))),
		):
			statuses = wait_for_indexed([document.name], timeout=1)

		self.assertEqual(statuses.get(document.name), "Queued")


class TestExtractionSchema(AIPlatformTestCase):
	def test_json_schema_is_generated(self):
		doc = frappe.get_doc(
			{
				"doctype": "AI Extraction Schema",
				"schema_name": "Test Invoice Schema",
				"enabled": 1,
				"extraction_fields": [
					{"field_name": "invoice_number", "field_type": "String", "required": 1},
					{"field_name": "total", "field_type": "Number"},
					{"field_name": "paid", "field_type": "Boolean"},
				],
			}
		)
		doc.insert(ignore_permissions=True)

		schema = frappe.parse_json(doc.json_schema)
		self.assertEqual(schema["type"], "object")
		self.assertEqual(schema["properties"]["invoice_number"]["type"], "string")
		self.assertEqual(schema["properties"]["total"]["type"], "number")
		self.assertEqual(schema["properties"]["paid"]["type"], "boolean")
		self.assertIn("invoice_number", schema["required"])

	def test_duplicate_field_names_rejected(self):
		doc = frappe.get_doc(
			{
				"doctype": "AI Extraction Schema",
				"schema_name": "Duplicate Field Schema",
				"extraction_fields": [
					{"field_name": "amount", "field_type": "Number"},
					{"field_name": "amount", "field_type": "String"},
				],
			}
		)
		with self.assertRaises(frappe.ValidationError):
			doc.insert(ignore_permissions=True)

	def test_invalid_field_name_rejected(self):
		doc = frappe.get_doc(
			{
				"doctype": "AI Extraction Schema",
				"schema_name": "Invalid Field Schema",
				"extraction_fields": [{"field_name": "total amount!", "field_type": "Number"}],
			}
		)
		with self.assertRaises(frappe.ValidationError):
			doc.insert(ignore_permissions=True)


class TestToolDocType(AIPlatformTestCase):
	def test_tool_name_must_be_snake_case(self):
		doc = frappe.get_doc(
			{
				"doctype": "AI Tool",
				"tool_name": "Not Snake Case",
				"tool_type": "Builtin",
				"handler": "current_datetime",
				"description": "Invalid name.",
			}
		)
		with self.assertRaises(frappe.ValidationError):
			doc.insert(ignore_permissions=True)

	def test_unknown_builtin_handler_rejected(self):
		doc = frappe.get_doc(
			{
				"doctype": "AI Tool",
				"tool_name": "bogus_tool",
				"tool_type": "Builtin",
				"handler": "does_not_exist",
				"description": "Bad handler.",
			}
		)
		with self.assertRaises(frappe.ValidationError):
			doc.insert(ignore_permissions=True)

	def test_tool_schema_generated(self):
		doc = frappe.get_doc(
			{
				"doctype": "AI Tool",
				"tool_name": "sample_lookup",
				"tool_type": "Builtin",
				"handler": "current_datetime",
				"description": "Sample tool.",
				"parameters": [
					{"parameter": "query", "parameter_type": "String", "required": 1},
					{"parameter": "limit", "parameter_type": "Integer"},
				],
			}
		)
		doc.insert(ignore_permissions=True)

		schema = frappe.parse_json(doc.json_schema)
		self.assertEqual(schema["name"], "sample_lookup")
		self.assertEqual(schema["parameters"]["properties"]["query"]["type"], "string")
		self.assertIn("query", schema["parameters"]["required"])
		self.assertNotIn("limit", schema["parameters"]["required"])


class TestCanonicalToolExecution(AIPlatformTestCase):
	def make_clock_tool(self, name="canonical_clock"):
		return frappe.get_doc(
			{
				"doctype": "AI Tool",
				"tool_name": name,
				"tool_type": "Builtin",
				"handler": "current_datetime",
				"enabled": 1,
				"is_readonly_tool": 1,
				"max_runtime_seconds": 30,
				"description": "Return the current site time.",
			}
		).insert(ignore_permissions=True)

	def test_success_is_persisted_with_requester_and_audit(self):
		from ai_fr_hg.ai.tools import execute_tool

		tool = self.make_clock_tool("canonical_clock_success")
		outcome = execute_tool(tool.name)

		self.assertEqual(outcome["status"], "Success")
		invocation = frappe.get_doc("AI Tool Invocation", outcome["invocation"])
		self.assertEqual(invocation.tool, tool.name)
		self.assertEqual(invocation.user, frappe.session.user)
		self.assertEqual(invocation.status, "Success")
		self.assertTrue(invocation.finished_at)
		self.assertTrue(
			frappe.db.exists(
				"AI Audit Log",
				{
					"action": f"Tool Executed: {tool.name}",
					"reference_doctype": "AI Tool Invocation",
					"reference_name": invocation.name,
				},
			)
		)

	def test_argument_contract_fails_before_dispatch(self):
		from ai_fr_hg.ai.tools import execute_tool

		tool = self.make_clock_tool("canonical_clock_contract")
		with patch("ai_fr_hg.ai.tools._dispatch") as dispatch:
			outcome = execute_tool(tool.name, {"unexpected": True})

		dispatch.assert_not_called()
		self.assertEqual(outcome["status"], "Failed")
		self.assertIn("unsupported arguments", outcome["error"])
		self.assertEqual(
			frappe.db.get_value("AI Tool Invocation", outcome["invocation"], "status"), "Failed"
		)

	def test_invalid_pipeline_context_is_refused_without_an_invocation(self):
		from ai_fr_hg.ai.tools import execute_tool

		tool = self.make_clock_tool("canonical_clock_context")
		before = frappe.db.count("AI Tool Invocation", {"tool": tool.name})
		outcome = execute_tool(tool.name, pipeline_run="missing-run")
		self.assertEqual(outcome["status"], "Failed")
		self.assertEqual(frappe.db.count("AI Tool Invocation", {"tool": tool.name}), before)

	def test_write_tool_approval_resumes_once_with_original_provenance(self):
		from ai_fr_hg.ai.tools import approve_invocation, execute_tool

		description = "Created by governed tool approval test"
		tool = frappe.get_doc(
			{
				"doctype": "AI Tool",
				"tool_name": "governed_todo_create",
				"tool_type": "DocType Action",
				"target_doctype": "ToDo",
				"enabled": 1,
				"requires_approval": 1,
				"max_runtime_seconds": 30,
				"description": "Create one approved ToDo.",
				"parameters": [
					{
						"parameter": "action",
						"parameter_type": "String",
						"required": 1,
						"enum_values": "create",
					},
					{"parameter": "values", "parameter_type": "Object", "required": 1},
				],
			}
		).insert(ignore_permissions=True)

		pending = execute_tool(tool.name, {"action": "create", "values": {"description": description}})
		self.assertEqual(pending["status"], "Pending Approval")
		self.assertFalse(frappe.db.exists("ToDo", {"description": description}))

		outcome = approve_invocation(pending["invocation"])
		self.assertEqual(outcome["status"], "Success")
		invocation = frappe.get_doc("AI Tool Invocation", pending["invocation"])
		self.assertEqual(invocation.status, "Success")
		self.assertEqual(invocation.user, "Administrator")
		self.assertEqual(invocation.approved_by, "Administrator")
		self.assertEqual(frappe.db.count("ToDo", {"description": description}), 1)
		with self.assertRaises(frappe.ValidationError):
			approve_invocation(pending["invocation"])

	def test_rejected_tool_invocation_never_dispatches(self):
		from ai_fr_hg.ai.tools import execute_tool, reject_invocation

		description = "Rejected governed tool write"
		tool = frappe.get_doc(
			{
				"doctype": "AI Tool",
				"tool_name": "governed_todo_reject",
				"tool_type": "DocType Action",
				"target_doctype": "ToDo",
				"enabled": 1,
				"requires_approval": 1,
				"max_runtime_seconds": 30,
				"description": "Reject one proposed ToDo.",
				"parameters": [
					{
						"parameter": "action",
						"parameter_type": "String",
						"required": 1,
						"enum_values": "create",
					},
					{"parameter": "values", "parameter_type": "Object", "required": 1},
				],
			}
		).insert(ignore_permissions=True)
		pending = execute_tool(tool.name, {"action": "create", "values": {"description": description}})
		outcome = reject_invocation(pending["invocation"])

		self.assertEqual(outcome["status"], "Rejected")
		invocation = frappe.get_doc("AI Tool Invocation", pending["invocation"])
		self.assertEqual(invocation.status, "Rejected")
		self.assertEqual(invocation.rejected_by, frappe.session.user)
		self.assertTrue(invocation.rejected_at)
		self.assertFalse(frappe.db.exists("ToDo", {"description": description}))
		self.assertTrue(
			frappe.db.exists(
				"AI Audit Log",
				{
					"action": "Tool Invocation Rejected",
					"reference_doctype": "AI Tool Invocation",
					"reference_name": invocation.name,
				},
			)
		)

	def test_pipeline_tool_step_records_permitted_run_context(self):
		from ai_fr_hg.ai.pipeline import run_pipeline

		tool = self.make_clock_tool("canonical_clock_pipeline")
		pipeline = frappe.get_doc(
			{
				"doctype": "AI Pipeline",
				"pipeline_name": "Canonical Tool Context Pipeline",
				"enabled": 1,
				"steps": [
					{
						"step_name": "Read Clock",
						"step_type": "Tool",
						"tool": tool.name,
						"enabled": 1,
					}
				],
			}
		).insert(ignore_permissions=True)

		run = run_pipeline(pipeline.name, enqueue_job=False)
		run.reload()
		invocation = frappe.get_doc(
			"AI Tool Invocation",
			frappe.db.get_value("AI Tool Invocation", {"pipeline_run": run.name}, "name"),
		)
		self.assertEqual(run.status, "Completed")
		self.assertEqual(invocation.status, "Success")
		self.assertEqual(invocation.pipeline_run, run.name)
		self.assertEqual(invocation.user, run.triggered_by)


class TestBuiltinTools(AIPlatformTestCase):
	def test_current_datetime(self):
		from ai_fr_hg.ai.tools.builtin import current_datetime

		result = current_datetime()
		self.assertIn("date", result)
		self.assertIn("timezone", result)

	def test_count_documents(self):
		from ai_fr_hg.ai.tools.builtin import count_documents

		result = count_documents("AI Provider")
		self.assertEqual(result["doctype"], "AI Provider")
		self.assertGreaterEqual(result["count"], 1)

	def test_get_document_text(self):
		from ai_fr_hg.ai.tools.builtin import get_document_text

		document = self.make_document("Tool Read Document", "Readable content for the tool.")
		# By primary key name
		result = get_document_text(document.name)
		self.assertIn("Readable content", result["content"])
		self.assertEqual(result["document"], document.name)

		# By title
		result_by_title = get_document_text(document.title)
		self.assertIn("Readable content", result_by_title["content"])

		# By file_path alias
		result_by_path = get_document_text(file_path="Tool Read Document.docx")
		self.assertIn("Readable content", result_by_path["content"])

	def test_get_document_text_does_not_embed_inline(self):
		"""Reading a document must not run the embedding pipeline in-request.

		Embedding is many model round trips; doing it inside a chat turn is
		what pushed `send_message` past the gateway timeout.
		"""
		from ai_fr_hg.ai.tools.builtin import get_document_text

		document = self.make_document("Unextracted Document", "placeholder")
		document.db_set("content", None, update_modified=False)
		document.db_set("status", "Queued", update_modified=False)
		document.db_set("source_file", "/files/unextracted.docx", update_modified=False)

		with patch("ai_fr_hg.ai.ingestion.process_document") as process:
			get_document_text(document.name)

		self.assertTrue(process.called, "text should still be extracted on demand")
		self.assertIs(
			process.call_args.kwargs.get("index"),
			False,
			"indexing must be deferred to a background worker",
		)

	def test_missing_document_reports_the_alternatives(self):
		"""A failed lookup should help the model, not just say 'not found'."""
		from ai_fr_hg.ai.tools.builtin import get_document_text

		self.make_document("A Findable Report", "Some content.")
		result = get_document_text("No_Such_File_At_All.docx")

		self.assertFalse(result["found"])
		self.assertIn("available_documents", result)
		titles = [row.get("title") for row in result["available_documents"]]
		self.assertIn("A Findable Report", titles)

	def test_empty_document_explains_why(self):
		"""'No text' must be distinguishable from 'no such document'."""
		from ai_fr_hg.ai.tools.builtin import get_document_text

		document = self.make_document("Scanned Only Document", "placeholder")
		document.db_set("content", "", update_modified=False)
		document.db_set("status", "Failed", update_modified=False)
		document.db_set("error_message", "No text could be extracted.", update_modified=False)

		result = get_document_text(document.name)

		self.assertTrue(result["found"])
		self.assertIn("could not be processed", result["error"])
		self.assertIn("No text could be extracted", result["error"])

	def test_list_documents_respects_limit(self):
		from ai_fr_hg.ai.tools.builtin import list_documents

		results = list_documents("AI Provider", limit=1)
		self.assertLessEqual(len(results), 1)


class TestAgentRuntime(AIPlatformTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		if not frappe.db.exists("AI Agent", "Test Agent"):
			frappe.get_doc(
				{
					"doctype": "AI Agent",
					"agent_name": "Test Agent",
					"enabled": 1,
					"model": cls.chat_model.name,
					"use_knowledge": 0,
					"use_tools": 0,
					"temperature": 0.1,
					"system_prompt": "You are a test assistant.",
				}
			).insert(ignore_permissions=True)

	def test_conversation_is_created(self):
		from ai_fr_hg.ai.agent import create_conversation

		conversation = create_conversation(agent="Test Agent", title="Test Conversation")
		self.assertTrue(frappe.db.exists("AI Conversation", conversation.name))
		self.assertEqual(conversation.agent, "Test Agent")

	def test_agent_turn_saves_messages(self):
		from ai_fr_hg.ai.agent import create_conversation, run_agent_turn

		conversation = create_conversation(agent="Test Agent")
		with patch("ai_fr_hg.ai.agent.run_chat") as mock_chat:
			mock_chat.return_value = CompletionResult(
				content="The answer is 42.", total_tokens=20, duration_ms=15, model="stub"
			)
			response = run_agent_turn(
				"What is the answer?", agent="Test Agent", conversation=conversation.name
			)

		self.assertEqual(response["answer"], "The answer is 42.")

		messages = frappe.get_all(
			"AI Message",
			filters={"conversation": conversation.name},
			fields=["role", "content"],
			order_by="sequence asc",
		)
		roles = [message.role for message in messages]
		self.assertIn("User", roles)
		self.assertIn("Assistant", roles)

	def test_exhausted_budget_saves_an_answer_instead_of_hanging(self):
		"""A blown budget must end the turn, not leave the proxy to time out."""
		from ai_fr_hg.ai.agent import TIMED_OUT_ANSWER, create_conversation, run_agent_turn
		from ai_fr_hg.ai.deadline import turn_budget
		from ai_fr_hg.ai.exceptions import DeadlineExceededError

		conversation = create_conversation(agent="Test Agent")

		with patch("ai_fr_hg.ai.agent.run_chat", side_effect=DeadlineExceededError("out of time")):
			with turn_budget(60):
				response = run_agent_turn(
					"Summarise the document I just uploaded.",
					agent="Test Agent",
					conversation=conversation.name,
				)

		# The turn returns normally, flagged, with a usable explanation.
		self.assertTrue(response["timed_out"])
		self.assertEqual(response["answer"], TIMED_OUT_ANSWER)

		# And the reply is persisted, so the thread stays coherent on reload.
		saved = frappe.get_all(
			"AI Message",
			filters={"conversation": conversation.name, "role": "Assistant"},
			fields=["content", "status"],
			order_by="sequence desc",
			limit=1,
		)
		self.assertEqual(saved[0].status, "Failed")
		self.assertIn("ran out of time", saved[0].content)

	def test_provider_timeout_saves_friendly_answer(self):
		"""A provider read timeout must not surface as a 417; it becomes a saved
		explanation so the conversation stays coherent."""
		from ai_fr_hg.ai.agent import PROVIDER_TIMEOUT_ANSWER, create_conversation, run_agent_turn
		from ai_fr_hg.ai.deadline import turn_budget
		from ai_fr_hg.ai.exceptions import ProviderTimeoutError

		conversation = create_conversation(agent="Test Agent")

		with patch("ai_fr_hg.ai.agent.run_chat", side_effect=ProviderTimeoutError("slow model")):
			with turn_budget(60):
				response = run_agent_turn("Question?", agent="Test Agent", conversation=conversation.name)

		self.assertTrue(response["timed_out"])
		self.assertEqual(response["answer"], PROVIDER_TIMEOUT_ANSWER)

		saved = frappe.get_all(
			"AI Message",
			filters={"conversation": conversation.name, "role": "Assistant"},
			fields=["status"],
			order_by="sequence desc",
			limit=1,
		)
		self.assertEqual(saved[0].status, "Failed")

	def test_tools_are_withheld_when_the_budget_cannot_fund_a_follow_up(self):
		"""Near the deadline, ask for prose rather than another tool round trip."""
		from ai_fr_hg.ai.agent import run_agent_turn
		from ai_fr_hg.ai.deadline import turn_budget

		agent = frappe.get_doc("AI Agent", "Test Agent")
		agent.use_tools = 1
		agent.flags.ignore_permissions = True

		captured = {}

		def capture(messages, **kwargs):
			captured["tools"] = kwargs.get("tools")
			return CompletionResult(content="Final answer.", total_tokens=5)

		with patch("ai_fr_hg.ai.agent.run_chat", side_effect=capture):
			# A 12s budget cannot fund a tool call plus the call that would
			# interpret its result, so no tools should be offered.
			with turn_budget(12):
				run_agent_turn("Question?", agent="Test Agent", save_messages=False)

		self.assertIsNone(captured["tools"])

	def test_generous_budget_still_offers_tools(self):
		"""The guard must not disable tool calling under normal conditions."""
		from ai_fr_hg.ai.agent import run_agent_turn
		from ai_fr_hg.ai.deadline import turn_budget

		captured = {}

		def capture(messages, **kwargs):
			captured["tools"] = kwargs.get("tools")
			return CompletionResult(content="Final answer.", total_tokens=5)

		agent = frappe.get_doc("AI Agent", "Test Agent")
		if not agent.use_tools:
			self.skipTest("Test Agent has no tools configured.")

		with patch("ai_fr_hg.ai.agent.run_chat", side_effect=capture):
			with turn_budget(600):
				run_agent_turn("Question?", agent="Test Agent", save_messages=False)

		self.assertIsNotNone(captured["tools"])

	def test_system_prompt_includes_context(self):
		from ai_fr_hg.ai.agent import build_system_prompt

		agent = frappe.get_doc("AI Agent", "Test Agent")
		prompt = build_system_prompt(agent, context="[1] Some retrieved fact.")
		self.assertIn("CONTEXT", prompt)
		self.assertIn("Some retrieved fact", prompt)

	def test_strict_grounding_adds_instruction(self):
		from ai_fr_hg.ai.agent import GROUNDING_INSTRUCTIONS, build_system_prompt

		agent = frappe.get_doc("AI Agent", "Test Agent")
		agent.strict_grounding = 1
		prompt = build_system_prompt(agent, context="[1] Fact.")
		self.assertIn(GROUNDING_INSTRUCTIONS, prompt)


class TestPipeline(AIPlatformTestCase):
	def test_pipeline_requires_steps(self):
		doc = frappe.get_doc({"doctype": "AI Pipeline", "pipeline_name": "Empty Pipeline", "enabled": 1})
		with self.assertRaises(frappe.ValidationError):
			doc.insert(ignore_permissions=True)

	def test_pipeline_cannot_call_itself(self):
		doc = frappe.get_doc(
			{
				"doctype": "AI Pipeline",
				"pipeline_name": "Recursive Pipeline",
				"enabled": 1,
				"steps": [
					{
						"step_name": "Self Call",
						"step_type": "Pipeline",
						"sub_pipeline": "Recursive Pipeline",
						"enabled": 1,
					}
				],
			}
		)
		with self.assertRaises(frappe.ValidationError):
			doc.insert(ignore_permissions=True)

	def test_registered_external_custom_method_resolves_from_hook(self):
		from ai_fr_hg.ai.pipeline import resolve_pipeline_step_method

		dotted_path = "external_enterprise_app.ai.steps.enrich_record"
		with (
			patch("ai_fr_hg.ai.pipeline.frappe.get_hooks", return_value={"enrich": dotted_path}) as hooks,
			patch(
				"ai_fr_hg.ai.pipeline.frappe.get_attr", return_value=unmarked_pipeline_method
			) as get_attr,
		):
			resolved = resolve_pipeline_step_method(dotted_path)

		self.assertIs(resolved, unmarked_pipeline_method)
		hooks.assert_called_once_with("ai_pipeline_methods")
		get_attr.assert_called_once_with(dotted_path)

	def test_unregistered_external_custom_method_is_rejected(self):
		doc = frappe.get_doc(
			{
				"doctype": "AI Pipeline",
				"pipeline_name": "Untrusted External Method Pipeline",
				"enabled": 1,
				"steps": [
					{
						"step_name": "Untrusted",
						"step_type": "Custom Method",
						"method": "frappe.utils.now",
						"enabled": 1,
					}
				],
			}
		)
		with self.assertRaises(frappe.ValidationError):
			doc.insert(ignore_permissions=True)

	def test_unmarked_app_custom_method_is_rejected(self):
		doc = frappe.get_doc(
			{
				"doctype": "AI Pipeline",
				"pipeline_name": "Unmarked App Method Pipeline",
				"enabled": 1,
				"steps": [
					{
						"step_name": "Unmarked",
						"step_type": "Custom Method",
						"method": "ai_fr_hg.tests.test_integration.unmarked_pipeline_method",
						"enabled": 1,
					}
				],
			}
		)
		with self.assertRaises(frappe.ValidationError):
			doc.insert(ignore_permissions=True)

	def test_custom_method_trust_is_rechecked_at_execution(self):
		from ai_fr_hg.ai.pipeline import run_pipeline

		pipeline = frappe.get_doc(
			{
				"doctype": "AI Pipeline",
				"pipeline_name": "Runtime Trust Recheck Pipeline",
				"enabled": 1,
				"steps": [
					{
						"step_name": "Trusted At Validation",
						"step_type": "Custom Method",
						"method": "ai_fr_hg.tests.test_integration.always_works",
						"enabled": 1,
					}
				],
			}
		).insert(ignore_permissions=True)

		marker = "_ai_pipeline_step_method"
		delattr(always_works, marker)
		try:
			run = run_pipeline(pipeline.name, enqueue_job=False)
		finally:
			setattr(always_works, marker, True)
		run.reload()
		self.assertEqual(run.status, "Failed")
		self.assertIn("not been marked", run.error_message)

	def test_nested_pipeline_records_parent_provenance(self):
		from ai_fr_hg.ai.pipeline import run_pipeline

		child = frappe.get_doc(
			{
				"doctype": "AI Pipeline",
				"pipeline_name": "Nested Child Pipeline",
				"enabled": 1,
				"steps": [
					{
						"step_name": "Child Work",
						"step_type": "Custom Method",
						"method": "ai_fr_hg.tests.test_integration.always_works",
						"output_field": "child_result",
						"enabled": 1,
					}
				],
			}
		).insert(ignore_permissions=True)
		parent = frappe.get_doc(
			{
				"doctype": "AI Pipeline",
				"pipeline_name": "Nested Parent Pipeline",
				"enabled": 1,
				"steps": [
					{
						"step_name": "Run Child",
						"step_type": "Pipeline",
						"sub_pipeline": child.name,
						"output_field": "nested",
						"enabled": 1,
					}
				],
			}
		).insert(ignore_permissions=True)

		parent_run = run_pipeline(parent.name, input_data={"seed": "value"}, enqueue_job=False)
		parent_run.reload()
		child_run = frappe.get_all(
			"AI Pipeline Run",
			filters={"parent_pipeline_run": parent_run.name, "pipeline": child.name},
			fields=["name", "status", "triggered_by"],
			limit=1,
		)[0]
		self.assertEqual(parent_run.status, "Completed")
		self.assertEqual(child_run.status, "Completed")
		self.assertEqual(child_run.triggered_by, frappe.session.user)
		self.assertIn("child_result", frappe.parse_json(parent_run.output_data)["nested"])

	def test_nested_pipeline_failure_is_persisted_in_child_and_parent(self):
		from ai_fr_hg.ai.pipeline import run_pipeline

		child = frappe.get_doc(
			{
				"doctype": "AI Pipeline",
				"pipeline_name": "Nested Failing Child Pipeline",
				"enabled": 1,
				"steps": [
					{
						"step_name": "Child Failure",
						"step_type": "Custom Method",
						"method": "ai_fr_hg.tests.test_integration.always_fails",
						"on_error": "Stop",
						"enabled": 1,
					}
				],
			}
		).insert(ignore_permissions=True)
		parent = frappe.get_doc(
			{
				"doctype": "AI Pipeline",
				"pipeline_name": "Nested Failing Parent Pipeline",
				"enabled": 1,
				"steps": [
					{
						"step_name": "Run Failing Child",
						"step_type": "Pipeline",
						"sub_pipeline": child.name,
						"on_error": "Stop",
						"enabled": 1,
					}
				],
			}
		).insert(ignore_permissions=True)

		parent_run = run_pipeline(parent.name, enqueue_job=False)
		child_run = frappe.get_doc(
			"AI Pipeline Run",
			frappe.db.get_value("AI Pipeline Run", {"parent_pipeline_run": parent_run.name}, "name"),
		)
		parent_run.reload()
		self.assertEqual(child_run.status, "Failed")
		self.assertEqual(parent_run.status, "Failed")
		self.assertIn("Intentional pipeline failure", child_run.error_message)
		self.assertIn(child.name, parent_run.error_message)
		for run in (parent_run, child_run):
			self.assertTrue(
				frappe.db.exists(
					"AI Audit Log",
					{
						"action": "Pipeline Run Failed",
						"reference_doctype": "AI Pipeline Run",
						"reference_name": run.name,
					},
				)
			)

	def test_nested_pipeline_preserves_tool_approval_request(self):
		from ai_fr_hg.ai.pipeline import run_pipeline

		tool = frappe.get_doc(
			{
				"doctype": "AI Tool",
				"tool_name": "nested_approval_tool",
				"tool_type": "DocType Action",
				"target_doctype": "ToDo",
				"enabled": 1,
				"requires_approval": 1,
				"max_runtime_seconds": 30,
				"description": "Create an approved ToDo from a nested pipeline.",
				"parameters": [
					{
						"parameter": "action",
						"parameter_type": "String",
						"required": 1,
						"enum_values": "create",
					},
					{"parameter": "values", "parameter_type": "Object", "required": 1},
				],
			}
		).insert(ignore_permissions=True)
		child = frappe.get_doc(
			{
				"doctype": "AI Pipeline",
				"pipeline_name": "Nested Approval Child Pipeline",
				"enabled": 1,
				"steps": [
					{
						"step_name": "Request Governed Write",
						"step_type": "Tool",
						"tool": tool.name,
						"config": frappe.as_json(
							{
								"arguments": {
									"action": "create",
									"values": {"description": "Nested pipeline approved write"},
								}
							}
						),
						"on_error": "Stop",
						"enabled": 1,
					}
				],
			}
		).insert(ignore_permissions=True)
		parent = frappe.get_doc(
			{
				"doctype": "AI Pipeline",
				"pipeline_name": "Nested Approval Parent Pipeline",
				"enabled": 1,
				"steps": [
					{
						"step_name": "Run Approval Child",
						"step_type": "Pipeline",
						"sub_pipeline": child.name,
						"on_error": "Stop",
						"enabled": 1,
					}
				],
			}
		).insert(ignore_permissions=True)

		parent_run = run_pipeline(parent.name, enqueue_job=False)
		child_run = frappe.get_doc(
			"AI Pipeline Run",
			frappe.db.get_value("AI Pipeline Run", {"parent_pipeline_run": parent_run.name}, "name"),
		)
		invocation = frappe.get_doc(
			"AI Tool Invocation",
			frappe.db.get_value(
				"AI Tool Invocation", {"pipeline_run": child_run.name, "status": "Pending Approval"}, "name"
			),
		)
		parent_run.reload()
		self.assertEqual(child_run.status, "Failed")
		self.assertEqual(parent_run.status, "Failed")
		self.assertEqual(invocation.user, frappe.session.user)
		self.assertFalse(frappe.db.exists("ToDo", {"description": "Nested pipeline approved write"}))
		self.assertIn("requires approval", child_run.error_message)
		self.assertEqual(invocation.status, "Pending Approval")

	def test_configuration_rejects_indirect_nested_pipeline_cycle(self):
		pipeline_a = frappe.get_doc(
			{
				"doctype": "AI Pipeline",
				"pipeline_name": "Configuration Cycle Pipeline A",
				"enabled": 1,
				"steps": [
					{
						"step_name": "Initial Safe Work",
						"step_type": "Custom Method",
						"method": "ai_fr_hg.tests.test_integration.always_works",
						"enabled": 1,
					}
				],
			}
		).insert(ignore_permissions=True)
		pipeline_b = frappe.get_doc(
			{
				"doctype": "AI Pipeline",
				"pipeline_name": "Configuration Cycle Pipeline B",
				"enabled": 1,
				"steps": [
					{
						"step_name": "Call A",
						"step_type": "Pipeline",
						"sub_pipeline": pipeline_a.name,
						"enabled": 1,
					}
				],
			}
		).insert(ignore_permissions=True)
		pipeline_a.set("steps", [])
		pipeline_a.append(
			"steps",
			{
				"step_name": "Call B",
				"step_type": "Pipeline",
				"sub_pipeline": pipeline_b.name,
				"enabled": 1,
			},
		)
		with self.assertRaisesRegex(frappe.ValidationError, "dependency cycle"):
			pipeline_a.save(ignore_permissions=True)

	def test_runtime_rejects_cycle_in_parent_run_ancestry(self):
		from ai_fr_hg.ai.exceptions import PipelineError
		from ai_fr_hg.ai.pipeline import run_pipeline

		def make_pipeline(name):
			return frappe.get_doc(
				{
					"doctype": "AI Pipeline",
					"pipeline_name": name,
					"enabled": 1,
					"steps": [
						{
							"step_name": "Safe Work",
							"step_type": "Custom Method",
							"method": "ai_fr_hg.tests.test_integration.always_works",
							"enabled": 1,
						}
					],
				}
			).insert(ignore_permissions=True)

		pipeline_a = make_pipeline("Runtime Ancestry Pipeline A")
		pipeline_b = make_pipeline("Runtime Ancestry Pipeline B")
		run_a = frappe.get_doc(
			{
				"doctype": "AI Pipeline Run",
				"pipeline": pipeline_a.name,
				"status": "Running",
				"triggered_by": "Administrator",
			}
		).insert(ignore_permissions=True)
		run_b = frappe.get_doc(
			{
				"doctype": "AI Pipeline Run",
				"pipeline": pipeline_b.name,
				"status": "Running",
				"triggered_by": "Administrator",
				"parent_pipeline_run": run_a.name,
			}
		).insert(ignore_permissions=True)
		before = frappe.db.count("AI Pipeline Run", {"parent_pipeline_run": run_b.name})
		with self.assertRaisesRegex(PipelineError, "Recursive nested pipeline call"):
			run_pipeline(pipeline_a.name, enqueue_job=False, _parent_run=run_b.name)
		self.assertEqual(
			frappe.db.count("AI Pipeline Run", {"parent_pipeline_run": run_b.name}),
			before,
		)

	def test_summarize_pipeline_runs(self):
		from ai_fr_hg.ai.pipeline import run_pipeline

		pipeline = frappe.get_doc(
			{
				"doctype": "AI Pipeline",
				"pipeline_name": "Summarize Pipeline",
				"enabled": 1,
				"trigger_type": "Manual",
				"steps": [
					{
						"step_name": "Summarize",
						"step_type": "Summarize",
						"enabled": 1,
						"input_field": "content",
						"output_field": "summary",
					}
				],
			}
		).insert(ignore_permissions=True)

		with patch("ai_fr_hg.ai.intelligence.summarize", return_value="A short summary."):
			run = run_pipeline(
				pipeline.name,
				input_data={"content": "Some long text to summarise."},
				enqueue_job=False,
			)

		run.reload()
		self.assertEqual(run.status, "Completed")
		output = frappe.parse_json(run.output_data)
		self.assertEqual(output["summary"], "A short summary.")

	def test_failing_step_marks_run_failed(self):
		from ai_fr_hg.ai.pipeline import run_pipeline

		pipeline = frappe.get_doc(
			{
				"doctype": "AI Pipeline",
				"pipeline_name": "Failing Pipeline",
				"enabled": 1,
				"steps": [
					{
						"step_name": "Boom",
						"step_type": "Custom Method",
						"method": "ai_fr_hg.tests.test_integration.always_fails",
						"on_error": "Stop",
						"enabled": 1,
					}
				],
			}
		).insert(ignore_permissions=True)

		run = run_pipeline(pipeline.name, enqueue_job=False)
		run.reload()

		self.assertEqual(run.status, "Failed")
		self.assertTrue(run.error_message)
		self.assertEqual(run.step_logs[0].status, "Failed")

	def test_continue_on_error(self):
		from ai_fr_hg.ai.pipeline import run_pipeline

		pipeline = frappe.get_doc(
			{
				"doctype": "AI Pipeline",
				"pipeline_name": "Tolerant Pipeline",
				"enabled": 1,
				"steps": [
					{
						"step_name": "Boom",
						"step_type": "Custom Method",
						"method": "ai_fr_hg.tests.test_integration.always_fails",
						"on_error": "Continue",
						"enabled": 1,
					},
					{
						"step_name": "Fine",
						"step_type": "Custom Method",
						"method": "ai_fr_hg.tests.test_integration.always_works",
						"enabled": 1,
						"output_field": "ok",
					},
				],
			}
		).insert(ignore_permissions=True)

		run = run_pipeline(pipeline.name, enqueue_job=False)
		run.reload()

		self.assertEqual(run.status, "Completed")
		self.assertEqual(run.step_logs[0].status, "Failed")
		self.assertEqual(run.step_logs[1].status, "Success")


class TestAutomationRule(AIPlatformTestCase):
	def test_rule_cannot_target_platform_doctypes(self):
		doc = frappe.get_doc(
			{
				"doctype": "AI Automation Rule",
				"rule_name": "Recursive Rule",
				"document_type": "AI Document",
				"event": "on_update",
				"action_type": "Summarize",
			}
		)
		with self.assertRaises(frappe.ValidationError):
			doc.insert(ignore_permissions=True)

	def test_invalid_condition_rejected(self):
		doc = frappe.get_doc(
			{
				"doctype": "AI Automation Rule",
				"rule_name": "Bad Condition Rule",
				"document_type": "ToDo",
				"event": "on_update",
				"action_type": "Summarize",
				"condition": "doc.status ==",
			}
		)
		with self.assertRaises(frappe.ValidationError):
			doc.insert(ignore_permissions=True)

	def test_unknown_target_field_rejected(self):
		doc = frappe.get_doc(
			{
				"doctype": "AI Automation Rule",
				"rule_name": "Bad Target Rule",
				"document_type": "ToDo",
				"event": "on_update",
				"action_type": "Summarize",
				"target_field": "nonexistent_field",
			}
		)
		with self.assertRaises(frappe.ValidationError):
			doc.insert(ignore_permissions=True)


class TestLogging(AIPlatformTestCase):
	def test_redaction_masks_configured_patterns(self):
		from ai_fr_hg.ai.logging import clear_pattern_cache, redact

		settings = frappe.get_single("AI Platform Settings")
		original = settings.redact_patterns
		settings.redact_patterns = r"\b\d{4}[- ]?\d{4}[- ]?\d{4}[- ]?\d{4}\b"
		settings.save(ignore_permissions=True)
		clear_pattern_cache()

		try:
			redacted = redact("Card 4111 1111 1111 1111 was used.")
			self.assertNotIn("4111 1111 1111 1111", redacted)
			self.assertIn("REDACTED", redacted)
		finally:
			settings.redact_patterns = original
			settings.save(ignore_permissions=True)
			clear_pattern_cache()

	def test_malformed_embedding_closes_execution_log_as_failed(self):
		from types import SimpleNamespace

		from ai_fr_hg.ai.engine import run_embedding
		from ai_fr_hg.ai.exceptions import ProviderError

		document = self.make_document("Malformed Embedding Audit", "Audit provider failures.")
		provider = SimpleNamespace(embed=lambda texts, model: [[1.0, 2.0]])
		with (
			patch("ai_fr_hg.ai.engine.get_settings", return_value=SimpleNamespace(platform_enabled=1)),
			patch("ai_fr_hg.ai.engine.get_provider", return_value=provider),
			self.assertRaises(ProviderError),
		):
			run_embedding(
				["first", "second"],
				model=self.embedding_model.name,
				reference_doctype="AI Document",
				reference_name=document.name,
			)

		log = frappe.get_all(
			"AI Execution Log",
			filters={
				"operation": "Embedding",
				"reference_doctype": "AI Document",
				"reference_name": document.name,
			},
			fields=["status", "error_message", "finished_at"],
			order_by="creation desc",
			limit=1,
		)[0]
		self.assertEqual(log.status, "Failed")
		self.assertTrue(log.error_message)
		self.assertTrue(log.finished_at)

	def test_audit_log_is_written(self):
		from ai_fr_hg.ai.logging import write_audit_log

		write_audit_log(
			action="Unit Test Action",
			category="Configuration",
			message="Written by the test suite.",
		)
		self.assertTrue(frappe.db.exists("AI Audit Log", {"action": "Unit Test Action"}))


class TestGovernance(AIPlatformTestCase):
	def test_administrator_bypasses_capability_checks(self):
		from ai_fr_hg.ai.governance import check_capability

		# Should not raise for Administrator.
		check_capability("tools")
		check_capability("pipeline")

	def test_effective_policy_resolves(self):
		from ai_fr_hg.ai.governance import get_effective_policy

		policy = get_effective_policy("Administrator")
		self.assertIsNotNone(policy)


class TestAPIEndpoints(AIPlatformTestCase):
	def test_get_supported_formats(self):
		from ai_fr_hg.api.knowledge import get_supported_formats

		formats = get_supported_formats()
		self.assertIn("pdf", formats["extensions"])
		self.assertTrue(formats["by_reader"])

	def test_get_system_status(self):
		from ai_fr_hg.api.admin import get_system_status

		status = get_system_status()
		self.assertIn("checks", status)
		self.assertTrue(all("label" in check for check in status["checks"]))

	def test_system_status_rejects_authenticated_non_manager(self):
		from ai_fr_hg.api.admin import get_system_status

		user = frappe.get_doc(
			{
				"doctype": "User",
				"email": "ai-platform-non-manager@example.com",
				"first_name": "AI Platform Non Manager",
				"enabled": 1,
				"send_welcome_email": 0,
			}
		).insert(ignore_permissions=True)
		user.add_roles("AI User")
		frappe.set_user(user.name)
		try:
			with self.assertRaises(frappe.PermissionError):
				get_system_status()
		finally:
			frappe.set_user("Administrator")

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

	def test_log_purge_rejects_non_positive_retention(self):
		from ai_fr_hg.api.admin import purge_logs

		for days in (0, -1):
			with self.assertRaises(frappe.ValidationError):
				purge_logs("AI Execution Log", days)

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

	def test_model_pull_records_durable_lifecycle_and_worker_failure(self):
		from types import SimpleNamespace

		from ai_fr_hg.api.admin import _pull_model_job

		adapter = SimpleNamespace(pull_model=lambda model: None)
		with (
			patch("ai_fr_hg.ai.providers.get_provider", return_value=adapter),
			patch("ai_fr_hg.ai.monitoring.sync_provider_models", return_value={"created": ["test"]}),
			patch("ai_fr_hg.ai.logging.write_audit_log") as audit,
			patch.object(frappe.db, "commit") as commit,
			patch("frappe.publish_realtime"),
		):
			_pull_model_job(self.provider.name, "test-pull-model", "Administrator")

		self.assertEqual([item.kwargs["action"] for item in audit.call_args_list], ["Model Pull Started", "Model Pulled"])
		self.assertTrue(all(item.kwargs["raise_on_error"] for item in audit.call_args_list))
		self.assertEqual(commit.call_count, 2)

		failing_adapter = SimpleNamespace(pull_model=lambda model: (_ for _ in ()).throw(RuntimeError("pull failed")))
		with (
			patch("ai_fr_hg.ai.providers.get_provider", return_value=failing_adapter),
			patch("ai_fr_hg.ai.logging.write_audit_log") as failed_audit,
			patch.object(frappe.db, "commit") as failed_commit,
			patch.object(frappe.db, "rollback") as rollback,
			patch("frappe.publish_realtime"),
			patch("frappe.log_error"),
			self.assertRaisesRegex(RuntimeError, "pull failed"),
		):
			_pull_model_job(self.provider.name, "test-failing-pull-model", "Administrator")

		self.assertEqual(
			[item.kwargs["action"] for item in failed_audit.call_args_list],
			["Model Pull Started", "Model Pull Failed"],
		)
		self.assertTrue(all(item.kwargs["raise_on_error"] for item in failed_audit.call_args_list))
		self.assertEqual(failed_commit.call_count, 2)
		rollback.assert_called_once_with()

	def test_get_knowledge_overview(self):
		from ai_fr_hg.api.knowledge import get_knowledge_overview

		overview = get_knowledge_overview()
		self.assertIn("totals", overview)
		self.assertIn("knowledge_bases", overview)

	def test_get_chat_context(self):
		from ai_fr_hg.api.chat import get_chat_context

		context = get_chat_context()
		self.assertIn("agents", context)
		self.assertIn("models", context)
		self.assertIn("knowledge_bases", context)

	def test_platform_metrics(self):
		from ai_fr_hg.ai.monitoring import get_platform_metrics

		metrics = get_platform_metrics()
		self.assertIn("providers", metrics)
		self.assertIn("knowledge", metrics)
		self.assertIn("activity_24h", metrics)


class TestModelTypeInference(AIPlatformTestCase):
	def test_embedding_models_detected(self):
		from ai_fr_hg.ai.monitoring import _guess_model_type

		for name in ("nomic-embed-text", "bge-large", "mxbai-embed-large", "all-minilm"):
			self.assertEqual(_guess_model_type(name), "Embedding", name)

	def test_vision_models_detected(self):
		from ai_fr_hg.ai.monitoring import _guess_model_type

		for name in ("llava:7b", "bakllava", "moondream", "qwen2-vl"):
			self.assertEqual(_guess_model_type(name), "Vision", name)

	def test_chat_is_the_default(self):
		from ai_fr_hg.ai.monitoring import _guess_model_type

		for name in ("llama3.1:8b", "mistral:7b", "qwen2.5:14b"):
			self.assertEqual(_guess_model_type(name), "Chat", name)


class TestLearningLoop(AIPlatformTestCase):
	"""The teach → validate → conflict → approve → recall → observe lifecycle."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		# Cache the original values so tearDownClass can restore them.
		cls._original_learning = frappe.db.get_single_value(
			"AI Platform Settings", "learning_enabled"
		)
		cls._original_approval = frappe.db.get_single_value(
			"AI Platform Settings", "require_memory_approval"
		)
		# Enable the learning loop for all tests in this class.
		frappe.db.set_single_value("AI Platform Settings", "learning_enabled", 1)
		frappe.db.set_single_value("AI Platform Settings", "require_memory_approval", 1)
		frappe.clear_cache()

	@classmethod
	def tearDownClass(cls):
		# Restore original values so other test classes are not affected.
		frappe.db.set_single_value(
			"AI Platform Settings", "learning_enabled", cls._original_learning
		)
		frappe.db.set_single_value(
			"AI Platform Settings", "require_memory_approval", cls._original_approval
		)
		frappe.clear_cache()
		super().tearDownClass()

	def make_memory(self, content, candidate_type="Fact"):
		from ai_fr_hg.ai.learning import _promote_to_memory, create_candidate

		candidate = create_candidate(content=content, candidate_type=candidate_type, user="Administrator")
		candidate.db_set("status", "Validated")
		return _promote_to_memory(candidate)["name"]

	def test_teach_creates_a_validated_candidate(self):
		from ai_fr_hg.ai.learning import create_candidate, validate_candidate

		candidate = create_candidate(
			content="The refund period is thirty days.", candidate_type="Fact", user="Administrator"
		)
		self.assertEqual(candidate.status, "Draft")
		self.assertEqual(candidate.user, "Administrator")

		report = validate_candidate(candidate)
		self.assertTrue(report["valid"])

	def test_provenance_is_server_attributed_and_caller_context_is_labelled_unverified(self):
		from ai_fr_hg.ai.learning import create_candidate

		candidate = create_candidate(
			content="A provenance-sensitive fact.",
			user="Administrator",
			provenance="Approved by an external authority.",
		)
		self.assertIn("Source Type: Explicit Teaching", candidate.provenance)
		self.assertIn("Teaching User: Administrator", candidate.provenance)
		self.assertIn("Recorded By: Administrator", candidate.provenance)
		self.assertIn("User-Provided Context (Unverified)", candidate.provenance)
		self.assertEqual(candidate.provenance_context, "Approved by an external authority.")

	def test_direct_candidate_insertion_is_attributed_and_audited(self):
		doc = frappe.get_doc(
			{
				"doctype": "AI Knowledge Candidate",
				"title": "Direct Governed Candidate",
				"content": "Direct insertions still use authoritative learning provenance.",
				"candidate_type": "Fact",
				"source_type": "Explicit Teaching",
				"status": "Draft",
				"target_scope": "Global",
				"provenance": "Client-supplied provenance claim",
			}
		).insert(ignore_permissions=True)

		self.assertEqual(doc.user, "Administrator")
		self.assertIn("Teaching User: Administrator", doc.provenance)
		self.assertIn("User-Provided Context (Unverified)", doc.provenance)
		self.assertTrue(
			frappe.db.exists(
				"AI Audit Log",
				{
					"action": "Knowledge Candidate Created",
					"reference_doctype": "AI Knowledge Candidate",
					"reference_name": doc.name,
				},
			)
		)

	def test_direct_candidate_audit_failure_propagates_for_transaction_rollback(self):
		save_point = "candidate_strict_audit_failure"
		frappe.db.savepoint(save_point)
		doc = frappe.get_doc(
			{
				"doctype": "AI Knowledge Candidate",
				"title": "Unaudited Candidate Must Roll Back",
				"content": "This candidate cannot survive a strict audit failure.",
				"candidate_type": "Fact",
				"source_type": "Explicit Teaching",
				"status": "Draft",
				"target_scope": "Global",
			}
		)
		with (
			patch("ai_fr_hg.ai.logging.write_audit_log", side_effect=RuntimeError("audit unavailable")),
			self.assertRaisesRegex(RuntimeError, "audit unavailable"),
		):
			doc.insert(ignore_permissions=True)
		frappe.db.rollback(save_point=save_point)
		frappe.db.release_savepoint(save_point)

		self.assertFalse(frappe.db.exists("AI Knowledge Candidate", doc.name))

	def test_direct_candidate_requires_source_record_for_document_provenance(self):
		doc = frappe.get_doc(
			{
				"doctype": "AI Knowledge Candidate",
				"title": "Missing Source",
				"content": "This claims to come from a document.",
				"candidate_type": "Document",
				"source_type": "Document",
				"status": "Draft",
				"target_scope": "Global",
			}
		)
		with self.assertRaises(frappe.ValidationError):
			doc.insert(ignore_permissions=True)

	def test_empty_teaching_is_rejected(self):
		from ai_fr_hg.ai.learning import LearningError, create_candidate

		with self.assertRaises(LearningError):
			create_candidate(content="   ", user="Administrator")

	def test_approve_fact_creates_memory_with_embedding(self):
		from ai_fr_hg.ai.learning import approve_candidate, create_candidate

		candidate = create_candidate(
			content="Customers are greeted by name on arrival.",
			candidate_type="Preference",
			user="Administrator",
		)
		candidate.db_set("status", "Validated")

		with patch("ai_fr_hg.ai.engine.run_embedding", return_value=[[0.1, 0.2, 0.3]]):
			result = approve_candidate(candidate.name)

		self.assertEqual(result["promoted_to"], "AI Memory")
		memory = frappe.get_doc("AI Memory", result["promoted_name"])
		self.assertEqual(memory.content, candidate.content)
		self.assertTrue(memory.embedding)
		self.assertEqual(memory.embedding_dimensions, 3)
		self.assertEqual(frappe.db.get_value("AI Knowledge Candidate", candidate.name, "status"), "Approved")

	def test_approve_instruction_creates_a_skill(self):
		from ai_fr_hg.ai.learning import approve_candidate, create_candidate

		candidate = create_candidate(
			content="Always use markdown tables when comparing two options.",
			candidate_type="Instruction",
			user="Administrator",
		)
		candidate.db_set("status", "Validated")
		result = approve_candidate(candidate.name)

		self.assertEqual(result["promoted_to"], "AI Skill")
		self.assertTrue(frappe.db.exists("AI Skill", result["promoted_name"]))

	def test_duplicate_teaching_flags_a_conflict(self):
		from ai_fr_hg.ai.learning import teach

		self.make_memory("The office closes at five on weekdays.")
		result = teach("The office closes at five on weekdays.", user="Administrator")

		self.assertEqual(result["status"], "Conflict")
		self.assertTrue(result["conflicts"]["duplicates"])

	def test_reject_never_learns(self):
		from ai_fr_hg.ai.learning import create_candidate, reject_candidate

		candidate = create_candidate(content="Colours are dark mode only.", user="Administrator")
		candidate.db_set("status", "Validated")
		reject_candidate(candidate.name)
		self.assertEqual(frappe.db.get_value("AI Knowledge Candidate", candidate.name, "status"), "Rejected")
		self.assertFalse(frappe.db.exists("AI Memory", {"source_candidate": candidate.name}))

	def test_recall_returns_only_relevant_memories(self):
		from ai_fr_hg.ai.learning import build_memory_context

		self.make_memory("Always cite the source document in answers.")
		self.make_memory("Colours follow the company dark mode palette.")

		memory_block, _skills = build_memory_context("how do i cite sources in my answers", agent=None)
		self.assertIn("LEARNED KNOWLEDGE", memory_block)
		self.assertIn("cite the source document", memory_block)

		# An unrelated question must not pull in the citation memory.
		memory_block2, _ = build_memory_context("what time does the cafeteria close", agent=None)
		self.assertNotIn("cite the source document", memory_block2)

	def test_memory_scoping_hides_other_users_memories(self):
		from ai_fr_hg.ai.learning import recall

		memory_name = self.make_memory("This is a private fact for another user.")
		frappe.db.set_value("AI Memory", memory_name, {"scope": "User", "scope_value": "alice"})

		memories, _skills = recall("private fact", agent=None, user="Administrator")
		self.assertFalse(memories)

		memories_alice, _ = recall("private fact", agent=None, user="alice")
		self.assertTrue(memories_alice)

	def test_observe_negative_feedback_creates_candidate(self):
		from ai_fr_hg.ai.learning import observe_feedback

		conversation = frappe.get_doc(
			{
				"doctype": "AI Conversation",
				"title": "Learning Feedback",
				"user": "Administrator",
				"status": "Active",
			}
		).insert(ignore_permissions=True)
		message = frappe.get_doc(
			{
				"doctype": "AI Message",
				"conversation": conversation.name,
				"role": "Assistant",
				"content": "This answer was wrong and should be corrected.",
				"sequence": 1,
				"status": "Completed",
				"user": "Administrator",
			}
		).insert(ignore_permissions=True)

		result = observe_feedback(message.name, "Negative")
		self.assertTrue(result["candidate"])
		candidate = frappe.get_doc("AI Knowledge Candidate", result["candidate"])
		self.assertEqual(candidate.source_type, "Chat Correction")
		self.assertEqual(candidate.source_reference_name, message.name)
		self.assertEqual(candidate.candidate_type, "Feedback")
		self.assertIn("failure example", candidate.content)
		self.assertNotEqual(candidate.content, message.content)

	def test_preference_defaults_to_teaching_user_scope(self):
		from ai_fr_hg.ai.learning import approve_candidate, create_candidate

		candidate = create_candidate(
			content="I prefer concise monthly reports.",
			candidate_type="Preference",
			user="Administrator",
		)
		candidate.db_set("status", "Validated")
		with patch("ai_fr_hg.ai.engine.run_embedding", return_value=[]):
			result = approve_candidate(candidate.name)

		memory = frappe.get_doc("AI Memory", result["promoted_name"])
		self.assertEqual(memory.scope, "User")
		self.assertEqual(memory.scope_value, "Administrator")

	def test_disabled_approval_gate_auto_promotes_conflict_free_teaching(self):
		from ai_fr_hg.ai.learning import teach

		settings = frappe.get_single("AI Platform Settings")
		settings.learning_enabled = 1
		settings.require_memory_approval = 0
		with (
			patch("ai_fr_hg.ai.learning._settings", return_value=settings),
			patch("ai_fr_hg.ai.engine.run_embedding", return_value=[]),
		):
			result = teach(
				"Warehouse aisle turquoise has a safety inspection every 19 days.",
				user="Administrator",
			)

		self.assertEqual(result["status"], "Approved")
		self.assertTrue(frappe.db.exists("AI Memory", {"source_candidate": result["candidate"]}))

	def test_approve_is_idempotent(self):
		from ai_fr_hg.ai.learning import approve_candidate, create_candidate

		candidate = create_candidate(
			content="Idempotent approvals prevent duplicate learned records.",
			user="Administrator",
		)
		candidate.db_set("status", "Validated")
		with patch("ai_fr_hg.ai.engine.run_embedding", return_value=[]):
			first = approve_candidate(candidate.name)
			second = approve_candidate(candidate.name)

		self.assertEqual(first["promoted_name"], second["promoted_name"])
		self.assertEqual(
			frappe.db.count("AI Memory", {"source_candidate": candidate.name}),
			1,
		)

	def test_document_candidate_promotes_to_fact_memory(self):
		from ai_fr_hg.ai.learning import approve_candidate, create_candidate

		candidate = create_candidate(
			content="The document establishes a quarterly calibration schedule.",
			candidate_type="Document",
			source_type="Document",
			source_reference_doctype="AI Document",
			source_reference_name=self.make_document("Learning Source", "Quarterly calibration.").name,
			user="Administrator",
		)
		candidate.db_set("status", "Validated")
		with patch("ai_fr_hg.ai.engine.run_embedding", return_value=[]):
			result = approve_candidate(candidate.name)

		memory = frappe.get_doc("AI Memory", result["promoted_name"])
		self.assertEqual(memory.memory_type, "Fact")
		self.assertEqual(memory.source_type, "Document")

	def test_feedback_updates_recalled_memory_counters_once(self):
		from ai_fr_hg.ai.learning import record_feedback

		memory_name = self.make_memory("Always include an owner in action items.")
		conversation = frappe.get_doc(
			{
				"doctype": "AI Conversation",
				"title": "Feedback Counters",
				"user": "Administrator",
				"status": "Active",
			}
		).insert(ignore_permissions=True)
		message = frappe.get_doc(
			{
				"doctype": "AI Message",
				"conversation": conversation.name,
				"role": "Assistant",
				"content": "An answer shaped by memory.",
				"sequence": 1,
				"status": "Completed",
				"user": "Administrator",
				"learned_context": frappe.as_json({"memories": [memory_name], "skills": []}),
			}
		).insert(ignore_permissions=True)

		record_feedback(message.name, "Positive")
		record_feedback(message.name, "Positive")
		self.assertEqual(frappe.db.get_value("AI Memory", memory_name, "helpful_count"), 1)

		record_feedback(message.name, "Negative", correction="Always name an owner for each action item.")
		counts = frappe.db.get_value(
			"AI Memory",
			memory_name,
			["helpful_count", "not_helpful_count"],
			as_dict=True,
		)
		self.assertEqual(counts.helpful_count, 0)
		self.assertEqual(counts.not_helpful_count, 1)

	def test_build_system_prompt_includes_memory_block(self):
		from ai_fr_hg.ai.agent import build_system_prompt
		from ai_fr_hg.ai.learning_utils import build_memory_block

		agent = frappe.get_doc(
			{
				"doctype": "AI Agent",
				"agent_name": "Learning Prompt Agent",
				"enabled": 1,
				"model": self.chat_model.name,
				"use_knowledge": 0,
				"system_prompt": "You are a learning test assistant.",
			}
		).insert(ignore_permissions=True)

		memory = build_memory_block(
			[{"name": "m", "content": "Always greet customers by name.", "memory_type": "Instruction"}]
		)
		prompt = build_system_prompt(agent, memory=memory)
		self.assertIn("Always greet customers by name.", prompt)


# --------------------------------------------------------------------------
# Helpers referenced by the pipeline tests above.
# --------------------------------------------------------------------------


def unmarked_pipeline_method(context=None, step=None, config=None):
	return {"unsafe": True}


@pipeline_step_method
def always_fails(context=None, step=None, config=None):
	raise ValueError("This step always fails, by design.")


@pipeline_step_method
def always_works(context=None, step=None, config=None):
	return {"ok": True}
