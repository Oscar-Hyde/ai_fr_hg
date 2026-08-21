# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Part 2 static wiring checks.

**Scope, stated honestly.** These are *static* assertions over source and
configuration. They cannot prove behaviour, and they are kept only for the two
things that have no executable surface offline:

* §25 — Desk page timer lifecycle, which lives in browser JavaScript.
* §23 — hook registration in `hooks.py`, which Frappe reads at boot.

Everything else that was once asserted here as a source-text grep has been
replaced by executing tests in `test_part2_behaviour.py`, because a test of the
form ``assertIn("field_name", source)`` passes when the field is written to the
wrong row and fails when a local variable is renamed. It measures text, not
behaviour.

`scripts/mutation_check.py` guards against that regression: it injects real
defects and fails if the suite does not notice.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest import TestCase

APP = Path(__file__).resolve().parents[1]


def _doctype(relative: str) -> dict:
	return json.loads((APP / relative).read_text())


def _field(meta: dict, fieldname: str) -> dict:
	return next(f for f in meta["fields"] if f["fieldname"] == fieldname)


# ---------------------------------------------------------------------------
# §13.3 — retrieval traceability
# ---------------------------------------------------------------------------


class TestOperationsPollingLifecycle(TestCase):
	"""Frappe keeps Desk pages in the DOM; an uncleared timer polls forever."""

	SOURCE = "ai_operations/page/ai_operations/ai_operations.js"

	def test_polling_is_stopped_when_the_page_is_hidden(self):
		source = (APP / self.SOURCE).read_text()
		self.assertIn("on_page_hide", source)
		self.assertIn("clearInterval", source)

	def test_polling_restarts_when_a_cached_page_is_shown(self):
		source = (APP / self.SOURCE).read_text()
		show = source[source.index("on_page_show") :][:400]
		self.assertIn("start_polling", show)

	def test_start_polling_is_idempotent(self):
		"""Revisiting the page must not stack a second interval."""
		source = (APP / self.SOURCE).read_text()
		start = source[source.index("start_polling() {") :][:260]
		self.assertIn("if (this.timer) return;", start)

	def test_every_interval_in_the_app_is_cleared(self):
		"""Any setInterval without a matching clearInterval is a leak."""
		for path in APP.rglob("*.js"):
			if "node_modules" in str(path):
				continue
			source = path.read_text()
			if "setInterval" in source:
				self.assertIn("clearInterval", source, f"{path.name} starts a timer it never clears")


# ---------------------------------------------------------------------------
# §21.3 — API endpoints stay thin
# ---------------------------------------------------------------------------


class TestAnalysisRowIsolation(TestCase):
	def test_every_knowledge_scoped_doctype_has_a_permission_query(self):
		hooks = (APP / "hooks.py").read_text()
		for doctype in ("AI Pattern Entity", "AI Entity Relationship", "AI Document Chunk"):
			self.assertIn(f'"{doctype}":', hooks, f"{doctype} needs a permission query")

	def test_analysis_rows_are_read_only_for_non_managers(self):
		source = (APP / "utils/permissions.py").read_text()
		self.assertIn('{"AI Pattern Entity", "AI Entity Relationship"}', source)
		self.assertIn("_is_read(permission_type)", source)


# ---------------------------------------------------------------------------
# §24 — audit coverage for AI interactions
# ---------------------------------------------------------------------------
