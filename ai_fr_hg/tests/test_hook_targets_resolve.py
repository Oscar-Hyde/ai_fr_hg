# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Every active hook target must actually exist.

Frappe resolves `hooks.py` dotted paths lazily, at the moment the hook fires.
A typo in a `doc_events` handler, a scheduler entry, or a
`permission_query_conditions` target therefore does not fail at install — it
fails silently in production, or raises inside a background worker where the
traceback is easy to miss.

`fakebench` cannot catch this: it never reads `hooks.py`. Nor can a source-grep
test, which would only prove the string is present — which is precisely the
thing that is already true when the target is misspelled.

This module parses `hooks.py` as Python (so commented-out boilerplate is
excluded by construction) and resolves each dotted path against the app's real
modules using the AST, without importing Frappe.
"""

from __future__ import annotations

import ast
from pathlib import Path
from unittest import TestCase

APP = Path(__file__).resolve().parents[1]
ROOT = APP.parent

#: Hook keys whose values are dotted Python paths this app owns.
CALLABLE_HOOK_KEYS = frozenset(
	{
		"before_install",
		"after_install",
		"after_migrate",
		"before_uninstall",
		"after_uninstall",
		"doc_events",
		"scheduler_events",
		"permission_query_conditions",
		"has_permission",
		"override_whitelisted_methods",
		"override_doctype_class",
		"jinja",
		"on_session_creation",
		"on_logout",
		"boot_session",
		"extend_bootinfo",
		"website_context",
		"standard_queries",
		"ai_document_readers",
		"ai_providers",
		"ai_tools",
	}
)


def _hook_namespace() -> dict:
	"""Evaluate hooks.py literals without importing Frappe.

	Executing the module directly is safe: `hooks.py` is declarative and has no
	imports or side effects. Commented-out examples never reach the namespace.
	"""
	namespace: dict = {}
	source = (APP / "hooks.py").read_text()
	exec(compile(source, "hooks.py", "exec"), namespace)
	return namespace


def _dotted_paths(value, found: set[str]) -> None:
	"""Collect every `ai_fr_hg.…` dotted string from a nested hook value."""
	if isinstance(value, str):
		if value.startswith("ai_fr_hg.") and "/" not in value:
			found.add(value)
	elif isinstance(value, dict):
		for item in value.values():
			_dotted_paths(item, found)
	elif isinstance(value, list | tuple | set):
		for item in value:
			_dotted_paths(item, found)


def _module_symbols(path: Path) -> set[str]:
	tree = ast.parse(path.read_text())
	symbols: set[str] = set()
	for node in tree.body:
		if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
			symbols.add(node.name)
		elif isinstance(node, ast.Assign):
			symbols.update(t.id for t in node.targets if isinstance(t, ast.Name))
		elif isinstance(node, ast.ImportFrom):
			symbols.update(a.asname or a.name for a in node.names)
		elif isinstance(node, ast.Import):
			symbols.update((a.asname or a.name).split(".")[0] for a in node.names)
	return symbols


def _resolves(dotted: str) -> bool:
	parts = dotted.split(".")
	# `ai_fr_hg.x.y.z` -> try the longest module prefix that exists on disk.
	for cut in range(len(parts), 1, -1):
		module = ROOT.joinpath(*parts[:cut]).with_suffix(".py")
		package = ROOT.joinpath(*parts[:cut], "__init__.py")
		target = module if module.exists() else (package if package.exists() else None)
		if target is None:
			continue
		attribute = parts[cut:]
		if not attribute:
			return True
		return attribute[0] in _module_symbols(target)
	return False


class TestActiveHookTargets(TestCase):
	@classmethod
	def setUpClass(cls):
		cls.hooks = _hook_namespace()

	def test_hooks_module_is_declarative_and_parses(self):
		self.assertIn("app_name", self.hooks)
		self.assertEqual(self.hooks["app_name"], "ai_fr_hg")

	def test_every_active_callable_hook_resolves(self):
		unresolved: list[tuple[str, str]] = []
		checked = 0
		for key in sorted(CALLABLE_HOOK_KEYS):
			if key not in self.hooks:
				continue
			found: set[str] = set()
			_dotted_paths(self.hooks[key], found)
			for dotted in sorted(found):
				checked += 1
				if not _resolves(dotted):
					unresolved.append((key, dotted))
		self.assertGreater(checked, 20, "hook discovery found suspiciously little to check")
		self.assertEqual(unresolved, [], f"unresolved hook targets: {unresolved}")

	def test_doc_event_handlers_resolve(self):
		"""A misspelled doc_event fails silently at runtime, not at install."""
		unresolved = []
		for doctype, events in (self.hooks.get("doc_events") or {}).items():
			for event, handlers in events.items():
				for handler in handlers if isinstance(handlers, list) else [handlers]:
					if handler.startswith("ai_fr_hg.") and not _resolves(handler):
						unresolved.append(f"{doctype}.{event} -> {handler}")
		self.assertEqual(unresolved, [])

	def test_scheduler_targets_resolve(self):
		"""A broken scheduled task only surfaces in a worker log."""
		unresolved = []
		for bucket, entries in (self.hooks.get("scheduler_events") or {}).items():
			items = entries.values() if isinstance(entries, dict) else [entries]
			for group in items:
				for target in group if isinstance(group, list) else [group]:
					if target.startswith("ai_fr_hg.") and not _resolves(target):
						unresolved.append(f"{bucket} -> {target}")
		self.assertEqual(unresolved, [])

	def test_permission_hooks_resolve_for_every_guarded_doctype(self):
		unresolved = []
		for doctype, target in (self.hooks.get("permission_query_conditions") or {}).items():
			if not _resolves(target):
				unresolved.append(f"{doctype} -> {target}")
		self.assertEqual(unresolved, [])

	def test_has_permission_covers_every_permission_query_doctype(self):
		"""A row filter without a document check leaves direct reads unguarded."""
		queries = set(self.hooks.get("permission_query_conditions") or {})
		checks = set(self.hooks.get("has_permission") or {})
		self.assertEqual(
			queries - checks,
			set(),
			"DocTypes with a permission query but no has_permission hook",
		)

	def test_scheduler_only_references_existing_task_functions(self):
		tasks = _module_symbols(APP / "tasks.py")
		referenced = set()
		for entries in (self.hooks.get("scheduler_events") or {}).values():
			groups = entries.values() if isinstance(entries, dict) else [entries]
			for group in groups:
				for target in group if isinstance(group, list) else [group]:
					if target.startswith("ai_fr_hg.tasks."):
						referenced.add(target.rsplit(".", 1)[1])
		self.assertTrue(referenced, "no scheduled tasks discovered")
		self.assertEqual(referenced - tasks, set(), "scheduler references missing task functions")
