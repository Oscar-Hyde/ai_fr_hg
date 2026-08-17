# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Integration tests that exercise the DocTypes and service layer.

Anything that would call a live model runtime is stubbed, so the suite runs on
a plain CI site with no Ollama installed.
"""

from contextlib import contextmanager
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

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
			patch("ai_fr_hg.ai.ingestion.time.monotonic", side_effect=[0, 10, 10, 10]),
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

		document = self.make_document("Unextracted Document", "")
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


def always_fails(context=None, step=None, config=None):
	raise ValueError("This step always fails, by design.")


def always_works(context=None, step=None, config=None):
	return {"ok": True}
