# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Frappe integration coverage for this DocType and its canonical domain services."""

from unittest.mock import patch

import frappe

from ai_fr_hg.tests.integration_test_case import AIPlatformTestCase


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

	def test_reranker_model_type_is_rejected_server_side(self):
		doc = frappe.get_doc(
			{
				"doctype": "AI Model",
				"model_label": "Unsupported Reranker",
				"provider": self.provider.name,
				"model_name": "rerank-test",
				"model_type": "Reranker",
			}
		)

		with self.assertRaises(frappe.ValidationError):
			doc.validate_supported_type()

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
		self.assertTrue(frappe.db.exists("AI Model", {"provider": provider.name, "model_name": first_name}))

	def test_discovery_does_not_create_reranker_without_execution_path(self):
		from types import SimpleNamespace

		from ai_fr_hg.ai.monitoring import sync_provider_models

		model_name = "phase-zero-reranker"
		adapter = SimpleNamespace(
			list_models=lambda: [
				SimpleNamespace(
					name=model_name,
					digest="reranker-digest",
					size=100,
					family="reranker",
					parameter_size="1B",
					quantization="Q4",
					context_window=4096,
				)
			]
		)

		with patch("ai_fr_hg.ai.providers.get_provider", return_value=adapter):
			result = sync_provider_models(self.provider.name)

		self.assertIn(model_name, result["unsupported"])
		self.assertNotIn(model_name, result["created"])
		self.assertFalse(
			frappe.db.exists("AI Model", {"provider": self.provider.name, "model_name": model_name})
		)

	def test_probe_returns_friendly_oom_instead_of_raising(self):
		"""Desk Test must not 417 when Ollama rejects the model for RAM."""
		from ai_fr_hg.ai.agent import PROVIDER_OOM_ANSWER
		from ai_fr_hg.ai.exceptions import ProviderError
		from ai_fr_hg.api.admin import test_model

		exc = ProviderError(
			'Provider Local Ollama returned HTTP 500: {"error":"model requires more system memory (10.8 GiB) than is available (9.3 GiB)"}'
		)
		with patch("ai_fr_hg.ai.engine.run_chat", side_effect=exc):
			result = test_model(self.chat_model.name)

		self.assertEqual(result["status"], "Failed")
		self.assertEqual(result["reason"], "oom")
		self.assertEqual(result["response"], PROVIDER_OOM_ANSWER)
		self.assertEqual(frappe.db.get_value("AI Model", self.chat_model.name, "status"), "Error")

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


class TestModelTypeInference(AIPlatformTestCase):
	def test_reranker_models_are_reported_as_unsupported(self):
		from ai_fr_hg.ai.monitoring import _guess_model_type

		for name in ("bge-reranker-v2", "enterprise-rerank"):
			self.assertIsNone(_guess_model_type(name), name)

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
