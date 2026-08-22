# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Shared fixtures for colocated Frappe DocType integration tests."""

import re
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

	# Indexing binds run_embedding on knowledge; retrieval lazy-imports engine.
	with (
		patch("ai_fr_hg.ai.knowledge.run_embedding", side_effect=fake_embed) as mock,
		patch("ai_fr_hg.ai.engine.run_embedding", side_effect=fake_embed),
	):
		yield mock


#: Letters used to synthesise deterministic pseudo-translations in tests. The
#: output is nonsense, but it is nonsense in the right script and of the right
#: length, which is exactly what the quality checks are meant to measure.
_SCRIPT_ALPHABETS = {
	"ar": "ابتثجحخدذرزسشصضطظعغفقكلمنهوي",
	"he": "אבגדהוזחטיכלמנסעפצקרשת",
	"en": "abcdefghijklmnopqrstuvwxyz",
}

_LETTER_RUN = re.compile(r"[^\W\d_]{2,}", re.UNICODE)
_SENTINEL_SPLIT = re.compile(r"(\[\[T\d+\]\])")
_BATCH_SEGMENT = re.compile(r"<<<SEG (\d+)>>>\n(.*?)(?=\n<<<SEG |\Z)", re.DOTALL)
_SINGLE_TEXT = re.compile(r"<TEXT>\n(.*)\n</TEXT>", re.DOTALL)


def pseudo_translate(text: str, target: str) -> str:
	"""Rewrite every word into the target script, keeping length and placeholders."""
	alphabet = _SCRIPT_ALPHABETS.get(target, _SCRIPT_ALPHABETS["en"])

	def _swap(match):
		word = match.group(0)
		return "".join(alphabet[(index + len(word)) % len(alphabet)] for index in range(len(word)))

	pieces = _SENTINEL_SPLIT.split(text)
	# Odd indices are protected sentinels and must survive untouched.
	return "".join(
		piece if position % 2 else _LETTER_RUN.sub(_swap, piece) for position, piece in enumerate(pieces)
	)


def _target_from_system_prompt(system: str) -> str:
	if "into Arabic" in system:
		return "ar"
	if "into Hebrew" in system:
		return "he"
	return "en"


def _default_translation_reply(system: str, user: str, target: str) -> str:
	if segments := _BATCH_SEGMENT.findall(user):
		return "\n".join(
			f"<<<SEG {index}>>>\n{pseudo_translate(body.strip(), target)}" for index, body in segments
		)
	match = _SINGLE_TEXT.search(user)
	return pseudo_translate((match.group(1) if match else user).strip(), target)


@contextmanager
def stub_translation_model(behaviour=None):
	"""Replace the chat engine with a deterministic offline pseudo-translator.

	`behaviour(system, user, target)` can override the reply to simulate a
	misbehaving model - dropped placeholders, refusals, untranslated echoes -
	so the quality gate can be tested without a real runtime.
	"""
	from ai_fr_hg.ai.providers.base import CompletionResult

	calls: list[dict] = []

	def fake_run_chat(messages, **kwargs):
		system = next((m.get("content", "") for m in messages if m.get("role") == "system"), "")
		user = next((m.get("content", "") for m in messages if m.get("role") == "user"), "")
		target = _target_from_system_prompt(system)
		calls.append({"system": system, "user": user, "target": target, "options": kwargs})
		reply = (behaviour or _default_translation_reply)(system, user, target)
		return CompletionResult(
			content=reply,
			total_tokens=max(1, len(reply) // 4),
			duration_ms=5,
			model="stub-translator",
		)

	with patch("ai_fr_hg.ai.translation.run_chat", side_effect=fake_run_chat) as mock:
		mock.calls = calls
		yield mock


#: DocTypes these suites create under fixed, human-readable names.
#:
#: Each uses ``autoname: field:<x>``, so the name IS the primary key and two
#: runs cannot coexist. ``IntegrationTestCase`` normally hides that by rolling
#: back, but a rollback only undoes rows *this* transaction created. Anything
#: committed by an earlier run — a crashed process, an interrupted migrate, or
#: an application bug that commits mid-test — survives and makes every later
#: run fail with ``DuplicateEntryError``, permanently, with no escape but
#: manual SQL. That stranded the AI Pipeline suite on a real bench for three
#: consecutive runs.
#:
#: Records seeded by ``setUpClass`` below are deliberately absent: they are
#: reused across the session via ``db.exists`` and must survive.
FIXED_NAME_FIXTURE_DOCTYPES = (
	"AI Pipeline",
	"AI Tool",
	"AI Automation Rule",
	"AI Extraction Schema",
	"AI Translation Glossary",
	"AI Agent",
)


class AIPlatformTestCase(IntegrationTestCase):
	"""Shared fixtures for the platform's integration tests."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.clear_fixed_name_fixtures()
		cls.provider = cls.ensure_provider()
		cls.chat_model = cls.ensure_model("Test Chat Model", "Chat")
		cls.embedding_model = cls.ensure_model("Test Embedding Model", "Embedding")
		cls.knowledge_base = cls.ensure_knowledge_base()

	@classmethod
	def clear_fixed_name_fixtures(cls):
		"""Delete fixed-name leftovers a rollback cannot reach.

		Runs once per class, before any fixture is seeded, so an interrupted
		earlier run cannot strand the suite.

		**Scoped to names this app's own tests declare**, harvested from the
		test sources themselves. Deleting every row of these DocTypes would
		destroy an operator's real pipelines and agents — `bench run-tests`
		is routinely pointed at a site that holds production data, so a
		blanket delete is not acceptable. Reading the names from source also
		means the set cannot drift as tests are added or renamed.

		Tolerant by design: a row that will not delete must not stop the run,
		because the insert that follows reports the real conflict far more
		clearly than a teardown traceback would.
		"""
		for doctype, names in cls._declared_fixture_names().items():
			if not names or not frappe.db.table_exists(doctype):
				continue
			for name in names:
				if not frappe.db.exists(doctype, name):
					continue
				try:
					frappe.delete_doc(
						doctype, name, force=True, ignore_permissions=True, delete_permanently=True
					)
				except Exception:
					continue
		frappe.db.commit()

	@classmethod
	def _declared_fixture_names(cls) -> dict[str, set[str]]:
		"""Map each fixed-name DocType to the names this app's tests insert."""
		import json
		from pathlib import Path

		app_root = Path(__file__).resolve().parents[1]
		name_fields: dict[str, str] = {}
		for schema in app_root.rglob("*/doctype/*/*.json"):
			try:
				meta = json.loads(schema.read_text())
			except ValueError:
				continue
			autoname = str(meta.get("autoname") or "")
			if meta.get("name") in FIXED_NAME_FIXTURE_DOCTYPES and autoname.startswith("field:"):
				name_fields[meta["name"]] = autoname.split(":", 1)[1]

		declared: dict[str, set[str]] = {doctype: set() for doctype in name_fields}
		for test_file in app_root.rglob("test_*.py"):
			if "__pycache__" in test_file.parts:
				continue
			source = test_file.read_text()
			for doctype, field in name_fields.items():
				if f'"{doctype}"' not in source:
					continue
				declared[doctype].update(re.findall(rf'"{re.escape(field)}":\s*"([^"]+)"', source))
		return declared

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
