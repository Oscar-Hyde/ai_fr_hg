# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Behavioral regressions for migration patches, run against a fake database.

These tests exist because two migration defects reached a real bench in a
state where CI had never executed the patch bodies:

- ``v0_0_17_conversation_turn_identity`` called ``_index_exists(name)`` with
  two positional arguments, so ``execute()`` raised ``TypeError`` and aborted
  ``bench migrate`` on the first site that ran it (2026-08-20).
- ``v0_0_18_document_processing_progress`` passed ``"tabAI Document"`` to
  ``frappe.db.table_exists()``, which itself prepends ``"tab"`` — the probe
  always returned False and the entire backfill would have silently no-oped.

Each test executes the real patch module against an in-memory reimplementation
of the Frappe database semantics it relies on, so it fails on the pre-fix code
and passes on the fixed code without needing a bench. Identifier strings inside
patch SQL are literal only; the fake database stores state per-table.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest import TestCase

ROOT = Path(__file__).resolve().parents[2]
PATCHES_DIR = ROOT / "ai_fr_hg" / "patches"
#: CHAT-02's DDL moved out of the patch and into a single dependency-light
#: owner that `AI Message.on_doctype_update` also calls, so a *fresh* install
#: gets the unique index too. The harness follows it there.
AI_DIR = ROOT / "ai_fr_hg" / "ai"

MESSAGE_TABLE = "tabAI Message"
CONVERSATION_TABLE = "tabAI Conversation"
DOCUMENT_TABLE = "tabAI Document"

SHOW_INDEX_RE = re.compile(r"show index from `(?P<table>[^`]+)`", re.IGNORECASE)
ADD_INDEX_RE = re.compile(r"add (?:unique )?(?:fulltext )?index `(?P<name>[^`]+)`", re.IGNORECASE)
ADD_COLUMN_RE = re.compile(r"add column `(?P<name>[^`]+)`", re.IGNORECASE)


class _FakeDB:
	"""Minimal in-memory stand-in implementing the Frappe db semantics used by patches.

	Critically, ``table_exists`` and ``has_column`` take the bare DocType name and
	prepend ``tab`` exactly like the real framework methods.
	"""

	def __init__(self, state):
		self.db_type = state.db_type
		self._state = state

	def sql(self, query, values=(), *args, **kwargs):
		state = self._state
		normalized = " ".join(str(query).split()).lower()
		state.sql_log.append(str(query))

		if normalized.startswith("show index"):
			table = SHOW_INDEX_RE.search(str(query)).group("table")
			return [(values[0],)] if values[0] in state.indexes.get(table, set()) else []

		if normalized.startswith("select name from `" + CONVERSATION_TABLE.lower() + "`"):
			return [(name,) for name in state.conversations]

		if normalized.startswith("select name, sequence"):
			conversation = values[0]
			rows = [m for m in state.messages if m["conversation"] == conversation]
			rows.sort(key=lambda m: (m["sequence"] or 0, m["creation"], m["name"]))
			return [(m["name"], m["sequence"]) for m in rows]

		if normalized.startswith("alter table"):
			table = re.search(r"alter table `([^`]+)`", str(query), re.IGNORECASE).group(1)
			index_match = ADD_INDEX_RE.search(str(query))
			if index_match:
				state.indexes.setdefault(table, set()).add(index_match.group("name"))
				state.alters.append(query)
				return []
			column_match = ADD_COLUMN_RE.search(str(query))
			if column_match:
				state.columns.setdefault(table, set()).add(column_match.group("name"))
				state.alters.append(query)
				return []
			raise AssertionError(f"unexpected ALTER statement: {query}")

		if normalized.startswith("update `" + DOCUMENT_TABLE.lower() + "`"):
			for document in state.documents:
				if document["status"] == "Indexed" and not document.get("processing_progress"):
					document["processing_progress"] = 100
					document["processing_message"] = "Indexed"
			state.updates.append(query)
			return []

		raise AssertionError(f"unexpected SQL statement: {query}")

	def set_value(self, doctype, name, fieldname, value, update_modified=True):
		for message in self._state.messages:
			if message["name"] == name:
				message[fieldname] = value
		self._state.set_values.append((doctype, name, fieldname, value))

	def table_exists(self, doctype, *, cached=True):
		return f"tab{doctype}" in self._state.tables

	def has_column(self, doctype, columnname, *, cached=True):
		return columnname in self._state.columns.get(f"tab{doctype}", set())

	def commit(self):
		return None


def _make_state(**overrides):
	state = SimpleNamespace(
		db_type="mariadb",
		tables={MESSAGE_TABLE, CONVERSATION_TABLE, DOCUMENT_TABLE},
		conversations=[],
		messages=[],
		documents=[],
		indexes={},
		columns={},
		sql_log=[],
		alters=[],
		updates=[],
		set_values=[],
		logged_errors=[],
	)
	for key, value in overrides.items():
		setattr(state, key, value)
	return state


def _load_patch(filename, state):
	"""Load a patch module with a fake ``frappe`` bound, leaving global state restored."""
	return _load_module(PATCHES_DIR / filename, state)


def _load_module(path, state):
	"""Load any frappe-only module with a fake ``frappe`` bound."""
	fake = ModuleType("frappe")
	fake.db = _FakeDB(state)
	fake.log_error = lambda **kwargs: state.logged_errors.append(kwargs)
	fake.get_traceback = lambda: ""

	path = Path(path)
	spec = importlib.util.spec_from_file_location(f"ai_fr_hg_patch_reg_{path.stem}", path)
	module = importlib.util.module_from_spec(spec)

	sentinel = object()
	previous = sys.modules.get("frappe", sentinel)
	sys.modules["frappe"] = fake
	try:
		spec.loader.exec_module(module)
	finally:
		if previous is sentinel:
			sys.modules.pop("frappe", None)
		else:
			sys.modules["frappe"] = previous
	return module


def _message(name, conversation, sequence, creation):
	return {"name": name, "conversation": conversation, "sequence": sequence, "creation": creation}


class TestConversationTurnIdentityPatch(TestCase):
	"""CHAT-02 migration behaviour, exercised on its canonical owner.

	The patch is now a two-line delegate to
	``ai_fr_hg/ai/conversation_indexes.py``; that module is what has to behave
	correctly, so it is what these tests execute.
	"""

	MODULE = "conversation_indexes.py"

	def _execute(self, state):
		module = _load_module(AI_DIR / self.MODULE, state)
		module.ensure_sequence_constraints()
		return module

	def test_after_migrate_reasserts_the_constraint_on_an_existing_site(self):
		"""Reported from a real bench: `bench migrate` left the index absent.

		Neither previous owner fires on an already-installed site.
		`AI Message.on_doctype_update` runs from `DocType.on_update`, and
		`frappe.modules.import_file` skips the import (and therefore the
		save) when the JSON's migration_hash is unchanged -- so a migrate
		that changes no DocType JSON never calls it. The v0_0_17 patch is
		marked already-applied on any site installed after it was written.
		Both paths miss, and the site runs with no uniqueness backstop.

		`after_migrate` has no such condition, so this asserts the hook
		really reaches the constraint owner and creates the index, rather
		than asserting that the source text mentions it.
		"""
		state = _make_state(indexes={})
		module = _load_module(AI_DIR / self.MODULE, state)

		# Precondition: the index genuinely does not exist yet.
		self.assertNotIn("unique_conversation_sequence", state.indexes.get(MESSAGE_TABLE, set()))

		module.ensure_sequence_constraints()

		self.assertIn(
			"unique_conversation_sequence",
			state.indexes.get(MESSAGE_TABLE, set()),
			"after_migrate did not create the uniqueness backstop",
		)
		self.assertTrue(
			any("ADD UNIQUE INDEX" in alter for alter in state.alters),
			f"no unique index DDL was issued; alters were {state.alters}",
		)

	def test_after_migrate_calls_the_constraint_owner(self):
		"""The hook must be wired, not merely available.

		A source-text assertion would pass if the call sat in a function
		nothing invokes, so this parses install.py and checks the call is
		inside `after_migrate` itself.
		"""
		import ast

		source = (Path(__file__).resolve().parents[1] / "install.py").read_text()
		tree = ast.parse(source)
		hook = next(
			node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "after_migrate"
		)
		called = {
			node.func.id
			for node in ast.walk(hook)
			if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
		}
		self.assertIn("ensure_sequence_constraints", called)

	def test_patch_compiles_and_runs_without_type_error(self):
		"""Reproduces the bench crash: execute() must complete end to end."""
		state = _make_state(
			conversations=["CONV-1"],
			messages=[
				_message("MSG-1", "CONV-1", 0, "2026-01-01 00:00:01"),
				_message("MSG-2", "CONV-1", 0, "2026-01-01 00:00:02"),
				_message("MSG-3", "CONV-1", 0, "2026-01-01 00:00:03"),
			],
		)
		self._execute(state)  # failed here with TypeError before the fix

		sequences = {message["name"]: message["sequence"] for message in state.messages}
		self.assertEqual(sequences, {"MSG-1": 1, "MSG-2": 2, "MSG-3": 3})
		self.assertEqual(state.indexes[MESSAGE_TABLE], {"unique_conversation_sequence", "turn_id_index"})
		self.assertEqual(len(state.alters), 2)
		self.assertEqual(state.logged_errors, [])

	def test_resumes_after_failed_migration_partial_ddls_committed(self):
		"""The unique index survives the failed run (MariaDB implicit DDL commit).

		Re-running the fixed patch on the site state left behind by the crash must
		not renumber again (sequences are already unique) and must only add the
		missing turn_id index.
		"""
		state = _make_state(
			conversations=["CONV-1"],
			messages=[
				_message("MSG-1", "CONV-1", 1, "2026-01-01 00:00:01"),
				_message("MSG-2", "CONV-1", 2, "2026-01-01 00:00:02"),
				_message("MSG-3", "CONV-1", 3, "2026-01-01 00:00:03"),
			],
			indexes={MESSAGE_TABLE: {"unique_conversation_sequence"}},
		)
		self._execute(state)

		self.assertEqual(state.set_values, [])
		self.assertEqual(len(state.alters), 1)
		self.assertIn("turn_id_index", state.alters[0])
		self.assertEqual(state.indexes[MESSAGE_TABLE], {"unique_conversation_sequence", "turn_id_index"})

	def test_idempotent_second_run_changes_nothing(self):
		state = _make_state(
			conversations=["CONV-1"],
			messages=[
				_message("MSG-1", "CONV-1", 1, "2026-01-01 00:00:01"),
				_message("MSG-2", "CONV-1", 2, "2026-01-01 00:00:02"),
			],
			indexes={MESSAGE_TABLE: {"unique_conversation_sequence", "turn_id_index"}},
		)
		self._execute(state)

		self.assertEqual(state.alters, [])
		self.assertEqual(state.set_values, [])

	def test_postgres_is_skipped_entirely(self):
		state = _make_state(db_type="postgres", conversations=["CONV-1"])
		self._execute(state)
		self.assertEqual(state.sql_log, [])


class TestDocumentProcessingProgressPatch(TestCase):
	FILENAME = "v0_0_18_document_processing_progress.py"

	def test_backfill_runs_when_table_exists(self):
		"""table_exists semantics: bare DocType name, framework prepends 'tab'.

		The pre-fix call probed for 'tabtabAI Document', never found it, and the
		whole patch silently returned — the exact phantom-success this repo's
		discipline forbids.
		"""
		state = _make_state(
			columns={DOCUMENT_TABLE: {"name", "status"}},
			documents=[
				{"name": "DOC-1", "status": "Indexed", "processing_progress": 0},
				{"name": "DOC-2", "status": "Indexed", "processing_progress": None},
				{"name": "DOC-3", "status": "Failed", "processing_progress": 0},
				{"name": "DOC-4", "status": "Indexed", "processing_progress": 42},
			],
		)
		module = _load_patch(self.FILENAME, state)
		module.execute()

		added = state.columns[DOCUMENT_TABLE]
		for field in (
			"processing_progress",
			"processing_message",
			"processing_heartbeat",
			"cancel_requested",
		):
			self.assertIn(field, added)
		self.assertEqual(len(state.alters), 4)

		progress = {doc["name"]: doc.get("processing_progress") for doc in state.documents}
		self.assertEqual(progress, {"DOC-1": 100, "DOC-2": 100, "DOC-3": 0, "DOC-4": 42})

	def test_doctype_synced_site_only_backfills(self):
		"""On the real upgrade path the JSON schema sync already created the columns."""
		state = _make_state(
			columns={
				DOCUMENT_TABLE: {
					"name",
					"status",
					"processing_progress",
					"processing_message",
					"processing_heartbeat",
					"cancel_requested",
				}
			},
			documents=[{"name": "DOC-1", "status": "Indexed", "processing_progress": 0}],
		)
		module = _load_patch(self.FILENAME, state)
		module.execute()

		self.assertEqual(state.alters, [])
		self.assertEqual(len(state.updates), 1)
		self.assertEqual(state.documents[0]["processing_progress"], 100)

	def test_missing_table_is_a_noop(self):
		state = _make_state(tables={MESSAGE_TABLE}, columns={}, documents=[])
		module = _load_patch(self.FILENAME, state)
		module.execute()
		self.assertEqual(state.alters, [])
		self.assertEqual(state.updates, [])


class TestPatchCorpus(TestCase):
	def test_every_registered_patch_compiles(self):
		import py_compile

		patches_txt = (ROOT / "ai_fr_hg" / "patches.txt").read_text()
		registered = sorted(
			line.rsplit(".", 1)[-1]
			for line in patches_txt.splitlines()
			if line.startswith("ai_fr_hg.patches.")
		)
		self.assertGreaterEqual(len(registered), 1)
		for stem in registered:
			path = PATCHES_DIR / f"{stem}.py"
			self.assertTrue(path.is_file(), f"registered patch missing on disk: {stem}")
			py_compile.compile(str(path), doraise=True)
