# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Shared fixtures for colocated Frappe DocType integration tests."""

from contextlib import contextmanager
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase


@contextmanager
def stub_embeddings(dimensions: int = 8):
	"""Replace the embedding engine with deterministic pseudo-vectors."""

	def fake_embed(texts, model=None, operation="Embedding", **kwargs):
		vectors = []
		for value in texts:
			seed = sum(ord(char) for char in (value or "x"))
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
