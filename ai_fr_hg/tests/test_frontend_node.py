# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Run Node frontend contracts on the Server image when `node` is present."""

from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SUITE = ROOT / "ai_fr_hg" / "tests" / "js" / "test_frontend_ui.mjs"


class TestFrontendNodeContracts(unittest.TestCase):
	def test_node_desk_workflow_contracts(self):
		node = shutil.which("node")
		if not node:
			self.skipTest("node is not available on this bench")
		completed = subprocess.run(
			[node, "--test", str(SUITE)],
			check=False,
			capture_output=True,
			text=True,
			cwd=ROOT,
		)
		self.assertEqual(
			completed.returncode,
			0,
			completed.stdout + "\n" + completed.stderr,
		)
