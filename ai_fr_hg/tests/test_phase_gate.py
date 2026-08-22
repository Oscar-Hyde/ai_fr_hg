# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Part 3 §32/§33/§36/§37 enforced as tests, not as a document.

Part 2 closed with the standing instruction: never rely on documents. A phase
roadmap written as prose is a document — it cannot fail, so it cannot govern.
`scripts/phase_gate.py` derives the phase state from the register and refuses
a dependency inversion or a prohibited completion claim; this module runs it
in CI and pins the properties that make it a control rather than a formality.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
from unittest import TestCase

ROOT = Path(__file__).resolve().parents[2]
GATE = ROOT / "scripts" / "phase_gate.py"


def _run(*args: str) -> subprocess.CompletedProcess:
	return subprocess.run(
		[sys.executable, str(GATE), *args],
		cwd=ROOT,
		capture_output=True,
		text=True,
		check=False,
	)


class TestPhaseGate(TestCase):
	def test_the_gate_passes_on_the_current_register(self):
		"""§33: no phase may be worked while an earlier one is incomplete."""
		result = _run()
		self.assertEqual(result.returncode, 0, f"phase gate failed:\n{result.stdout}\n{result.stderr}")

	def test_the_report_covers_every_phase_with_findings(self):
		result = _run("--report")
		self.assertEqual(result.returncode, 0)
		self.assertIn("Isolation, permissions, and API safety", result.stdout)
		self.assertIn("Production qualification and release", result.stdout)
		self.assertNotIn("unplaced rows", result.stdout, "a finding has no phase")

	def test_the_gate_rejects_a_prohibited_completion_claim(self):
		"""§37: 'tests pass' is not acceptable completion evidence.

		Proven by mutating a copy of the register, so the check is shown to
		fail rather than merely asserted to exist.
		"""
		register = ROOT / "docs" / "GAP_REGISTER.md"
		original = register.read_text()
		try:
			lines = original.splitlines()
			for index, line in enumerate(lines):
				if line.startswith("| RET-01 |"):
					cells = line.split("|")
					cells[-2] = " Tests pass. "
					lines[index] = "|".join(cells)
					break
			else:  # pragma: no cover - register must contain RET-01
				self.fail("RET-01 not found in the register")
			register.write_text("\n".join(lines) + "\n")
			result = _run()
		finally:
			register.write_text(original)

		self.assertEqual(result.returncode, 1)
		self.assertIn("not coverage", result.stdout)

	def test_the_gate_rejects_a_dependency_inversion(self):
		"""§33: reopening earlier work must block later phases immediately."""
		register = ROOT / "docs" / "GAP_REGISTER.md"
		original = register.read_text()
		try:
			lines = original.splitlines()
			for index, line in enumerate(lines):
				if line.startswith("| RET-01 |"):
					lines[index] = line.replace("CLOSED — IMPLEMENTED", "OPEN", 1)
					break
			else:  # pragma: no cover - register must contain RET-01
				self.fail("RET-01 not found in the register")
			register.write_text("\n".join(lines) + "\n")
			result = _run()
		finally:
			register.write_text(original)

		self.assertEqual(result.returncode, 1)
		self.assertIn("while earlier work is open", result.stdout)

	def test_the_runtime_exemption_requires_an_explicit_marker(self):
		"""A row may only skip §33 by naming the tier it waits on.

		The exemption exists because no reordering makes an unavailable tier
		available. It must not be claimable by prose: an earlier version
		matched loose wording and silently missed TRN-04.
		"""
		source = GATE.read_text()
		self.assertIn(r"\[RUNTIME-TIER\]", source)
		register = (ROOT / "docs" / "GAP_REGISTER.md").read_text()
		# Every exempt row must also state what it is waiting for.
		for line in register.splitlines():
			if "[RUNTIME-TIER]" in line:
				self.assertRegex(
					line,
					r"(?i)(runtime|browser|chaos|Phase 7)",
					"a RUNTIME-TIER row must say which runtime evidence is missing",
				)


class TestOperationalRunbooks(TestCase):
	"""A recovery procedure that names stale commands is worse than none.

	OPS-07 records how to restore the workspace after it is recreated. The
	risk is that it rots: the ignore-list, the dependency set or the branch
	convention change, and the runbook silently starts describing a recovery
	that no longer works — at exactly the moment someone is relying on it.
	"""

	RUNBOOK = ROOT / "docs" / "phase-reports" / "WORKSPACE_RECOVERY.md"

	@classmethod
	def setUpClass(cls):
		cls.text = cls.RUNBOOK.read_text()

	def test_the_runbook_exists_and_names_the_verification_step(self):
		self.assertIn("git ls-remote --heads origin", self.text)
		self.assertIn("stash", self.text)
		# The step that stops a partial restore being committed.
		self.assertIn("scripts/mutation_check.py", self.text)

	def test_the_runbook_lists_every_dependency_the_suites_need(self):
		"""A partial rebuild yields a green run that proves less than it looks."""
		for dependency in ("fakeredis[lua]", "ruff==0.14.10", "pytest"):
			self.assertIn(dependency, self.text, f"{dependency} missing from the runbook")
		self.assertIn("frappe.git", self.text, "the Frappe checkout is not in the runbook")

	def test_the_runbook_baseline_matches_the_real_offline_batch(self):
		"""The documented pytest invocation must skip exactly the bench-only suites."""
		mutation_source = (ROOT / "scripts" / "mutation_check.py").read_text()
		ignored = set(re.findall(r"--ignore=(\S+?\.py)", mutation_source))
		documented = set(re.findall(r"--ignore=(\S+?\.py)", self.text))
		self.assertEqual(
			ignored,
			documented,
			"the runbook's baseline command no longer matches scripts/mutation_check.py",
		)


class TestEvidenceTierHonesty(TestCase):
	"""A CLOSED row may not count a test that never runs in its claimed tier.

	This rule exists because the same defect appeared four times — SEC-04,
	SEC-07, GOV-01/02/03 and the §24 audit writer were each closed citing
	tests that either did not exist or lived only in bench-only suites. Those
	suites error without a live site, so offline and under the mutation gate
	they contribute no evidence at all.

	193 application functions are currently referenced *only* by bench-only
	suites. That is not a defect in itself — those suites are real evidence at
	the runtime tier — but it means a reference count says nothing about
	whether a behaviour is verified offline. The check below is therefore
	narrow and mechanical: rows claiming `tier: fakebench behaviour` must cite
	symbols something in the offline batch actually exercises.
	"""

	@classmethod
	def setUpClass(cls):
		mutation_source = (ROOT / "scripts" / "mutation_check.py").read_text()
		cls.bench_only_names = {
			Path(path).name for path in re.findall(r"--ignore=(\S+?\.py)", mutation_source)
		}
		cls.offline_text = "\n".join(
			path.read_text()
			for path in (ROOT / "ai_fr_hg" / "tests").rglob("test_*.py")
			if path.name not in cls.bench_only_names and "__pycache__" not in path.parts
		)
		cls.register = (ROOT / "docs" / "GAP_REGISTER.md").read_text()

	def test_discovery_found_the_offline_batch(self):
		self.assertGreater(len(self.offline_text), 200_000, "offline suite discovery collapsed")
		self.assertGreaterEqual(len(self.bench_only_names), 7)

	def test_fakebench_tier_rows_cite_symbols_the_offline_batch_exercises(self):
		"""Claiming the fakebench tier requires evidence that runs in it."""
		unproven: list[str] = []
		for line in self.register.splitlines():
			if not re.match(r"^\|\s*[A-Z]+-\d+", line):
				continue
			if "tier: fakebench behaviour" not in line:
				continue
			columns = [column.strip() for column in line.split("|")]
			evidence = columns[-2]
			cited = [
				symbol
				for symbol in re.findall(r"`([A-Za-z_][A-Za-z0-9_]*)`", evidence)
				if "_" in symbol or symbol[0].isupper()
			]
			# At least one cited symbol must appear in a suite that actually runs.
			if cited and not any(
				re.search(rf"\b{re.escape(symbol)}\b", self.offline_text) for symbol in cited
			):
				unproven.append(f"{columns[1]}: cites {cited} — none exercised by the offline batch")
		self.assertEqual(unproven, [])


def _clears_fixtures_in_setup(source: str) -> bool:
	"""True when a setUp/setUpClass deletes or skips pre-existing records.

	Fixed-name fixtures must be cleared per test, not merely somewhere in the
	module, so this inspects the setUp bodies rather than the whole file.
	Generating unique names per run is an equally valid strategy.
	"""
	import ast

	try:
		tree = ast.parse(source)
	except SyntaxError:
		return False
	for node in ast.walk(tree):
		if not isinstance(node, ast.FunctionDef) or node.name not in ("setUp", "setUpClass"):
			continue
		body = ast.dump(node)
		if "delete_doc" in body or "exists" in body or "uuid" in body:
			return True
	return False


class TestFixedNameFixtureHygiene(TestCase):
	"""A suite that inserts fixed-name records must be able to run twice.

	Frappe's IntegrationTestCase isolates by rolling back, which only undoes
	rows the current transaction created. When a DocType uses
	`autoname: field:<x>` the name is the primary key, so any row committed by
	an interrupted run -- a crashed process, a failed migrate, or an
	application bug that commits mid-test -- survives and makes every later
	run fail with DuplicateEntryError. The operator's only escape is manual
	SQL, which is not an acceptable state for a test suite to leave behind.

	This is not hypothetical: it stranded the AI Pipeline suite on a real
	bench for three consecutive runs.
	"""

	@classmethod
	def setUpClass(cls):
		import json

		cls.autonamed = {}
		for path in (ROOT / "ai_fr_hg").rglob("*/doctype/*/*.json"):
			try:
				meta = json.loads(path.read_text())
			except ValueError:
				continue
			autoname = str(meta.get("autoname") or "")
			if meta.get("name") and autoname.startswith("field:"):
				cls.autonamed[meta["name"]] = autoname.split(":", 1)[1]

	def test_discovery_found_the_autonamed_doctypes(self):
		self.assertGreaterEqual(len(self.autonamed), 5, "autoname discovery collapsed")

	def test_the_shared_base_actually_clears_fixtures_in_setupclass(self):
		"""Inheritance only helps if the base really does the cleanup.

		The suite check below accepts `AIPlatformTestCase` as sufficient
		protection, so that acceptance has to be earned. Deleting the call
		from setUpClass previously left every suite reported as guarded while
		none of them were.
		"""
		import ast

		source = (ROOT / "ai_fr_hg" / "tests" / "integration_test_case.py").read_text()
		tree = ast.parse(source)
		base = next(
			node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "AIPlatformTestCase"
		)
		setup = next(
			node for node in base.body if isinstance(node, ast.FunctionDef) and node.name == "setUpClass"
		)
		called = {
			node.func.attr
			for node in ast.walk(setup)
			if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
		}
		self.assertIn("clear_fixed_name_fixtures", called)

		# ...and the cleanup must be scoped, not a blanket table wipe.
		cleanup = next(
			node
			for node in base.body
			if isinstance(node, ast.FunctionDef) and node.name == "clear_fixed_name_fixtures"
		)
		body = ast.dump(cleanup)
		self.assertIn("_declared_fixture_names", body)
		self.assertIn("exists", body)

	def test_suites_inserting_fixed_names_clean_up_first(self):
		import re

		offenders: list[str] = []
		for path in (ROOT / "ai_fr_hg").rglob("test_*.py"):
			if "__pycache__" in path.parts:
				continue
			source = path.read_text()
			inserts = {
				doctype
				for doctype, field in self.autonamed.items()
				if f'"{doctype}"' in source and re.search(rf'"{re.escape(field)}":\s*"', source)
			}
			if not inserts:
				continue
			# The guard must run *before every test*, so require it inside
			# setUp/setUpClass. Checking the whole file was too weak: an
			# unrelated `db.exists` elsewhere satisfied it, so deleting the
			# real cleanup still passed. Parse the setUp body instead of
			# grepping, so this tracks structure rather than substrings.
			# The cleanup lives in the shared AIPlatformTestCase base, so a
			# suite is guarded either by inheriting it or by handling fixed
			# names itself (unique per-run names, or its own setUp cleanup).
			guarded = "AIPlatformTestCase" in source or _clears_fixtures_in_setup(source)
			if not guarded:
				offenders.append(f"{path.name}: inserts {sorted(inserts)} with no cleanup")
		self.assertEqual(offenders, [])
