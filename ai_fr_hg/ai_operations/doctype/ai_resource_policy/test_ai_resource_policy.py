# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Frappe integration coverage for this DocType and its canonical domain services."""

from ai_fr_hg.tests.integration_test_case import AIPlatformTestCase


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
