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
