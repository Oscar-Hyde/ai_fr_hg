# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Phase 6 enforcement tests against a real Frappe v17 bench.

Everything here needs the real runtime the audit demanded: Redis for the
atomic admission scripts, MariaDB for committed usage and provider rows, and
the real DocTypes for permission and report behaviour. Nothing that matters is
mocked — only the model *runtime* is replaced, because these tests are about
admission, capability and failover decisions, not about token generation.

Findings covered: GOV-01, GOV-02, GOV-03, GOV-04, PROV-01, PROV-02, OPS-02,
LEARN-01.
"""

import time
import uuid
from unittest.mock import patch

import frappe

from ai_fr_hg.ai import capability, limits
from ai_fr_hg.ai.exceptions import (
	ConcurrencyLimitError,
	ModelNotAvailableError,
	ProviderOfflineError,
	QuotaExceededError,
	RateLimitExceededError,
)
from ai_fr_hg.ai.providers.base import CompletionResult
from ai_fr_hg.tests.integration_test_case import AIPlatformTestCase


def _scope(prefix: str) -> str:
	"""A unique scope so parallel or repeated runs never share a semaphore."""
	return f"{prefix}:test-{uuid.uuid4().hex[:12]}"


# ---------------------------------------------------------------------------
# GOV-01 — concurrency leases
# ---------------------------------------------------------------------------


class TestConcurrencyLeases(AIPlatformTestCase):
	def test_limit_saturates_and_release_frees_the_slot(self):
		scope = _scope("model")
		first = limits.acquire_leases([(scope, 2)])
		second = limits.acquire_leases([(scope, 2)])
		self.assertEqual(limits.current_usage(scope), 2)

		with self.assertRaises(ConcurrencyLimitError):
			limits.acquire_leases([(scope, 2)])

		second.release()
		third = limits.acquire_leases([(scope, 2)])
		self.assertEqual(limits.current_usage(scope), 2)

		first.release()
		third.release()
		self.assertEqual(limits.current_usage(scope), 0)

	def test_zero_limit_means_unlimited_and_stores_nothing(self):
		scope = _scope("provider")
		held = limits.acquire_leases([(scope, 0)])
		self.assertEqual(held.leases, [])
		self.assertEqual(limits.current_usage(scope), 0)
		held.release()

	def test_abandoned_lease_expires_so_a_dead_worker_cannot_hold_a_slot(self):
		scope = _scope("model")
		# A worker that is killed never runs its `finally`. The TTL, not the
		# release, is what must free the slot.
		with patch.object(limits, "MIN_LEASE_TTL", 1):
			orphan = limits.acquire_leases([(scope, 1)], ttl=1)
			self.assertEqual(len(orphan.leases), 1)
			with self.assertRaises(ConcurrencyLimitError):
				limits.acquire_leases([(scope, 1)], ttl=1)

			time.sleep(1.3)
			recovered = limits.acquire_leases([(scope, 1)], ttl=1)
			self.assertEqual(len(recovered.leases), 1)
			recovered.release()
		limits.reset_scope(scope)

	def test_partial_acquisition_is_rolled_back(self):
		# Scopes are acquired in sorted order, so "model:..." is taken before
		# "user:...". If the second is full the first must not stay held.
		free_scope = f"model:aaa-{uuid.uuid4().hex[:8]}"
		full_scope = f"user:zzz-{uuid.uuid4().hex[:8]}"
		blocker = limits.acquire_leases([(full_scope, 1)])

		with self.assertRaises(ConcurrencyLimitError):
			limits.acquire_leases([(free_scope, 5), (full_scope, 1)])

		self.assertEqual(limits.current_usage(free_scope), 0)
		blocker.release()

	def test_error_names_the_exhausted_scope(self):
		scope = _scope("provider")
		held = limits.acquire_leases([(scope, 1)])
		with self.assertRaises(ConcurrencyLimitError) as caught:
			limits.acquire_leases([(scope, 1)])
		message = str(caught.exception)
		self.assertIn("concurrent request limit", message)
		# The scope must be named, not rendered as a raw internal key.
		self.assertIn(scope.split(":", 1)[1], message)
		held.release()

	def test_every_scope_prefix_renders_a_message(self):
		# Regression: unpacking str.partition() into `_` shadowed Frappe's
		# translation function, so every rejection raised TypeError instead of
		# the policy error. Exercise all three prefixes.
		for prefix in ("user", "provider", "model"):
			scope = _scope(prefix)
			held = limits.acquire_leases([(scope, 1)])
			try:
				with self.assertRaises(ConcurrencyLimitError):
					limits.acquire_leases([(scope, 1)])
			finally:
				held.release()

	def test_policy_resolves_user_provider_and_model_scopes(self):
		from ai_fr_hg.ai.governance import runtime_concurrency_limits

		frappe.db.set_value("AI Provider", self.provider.name, "max_concurrent_requests", 4)
		frappe.db.set_value("AI Model", self.chat_model.name, "max_concurrent_requests", 2)
		frappe.clear_document_cache("AI Model", self.chat_model.name)
		try:
			model_doc = frappe.get_cached_doc("AI Model", self.chat_model.name)
			specs = dict(runtime_concurrency_limits(model_doc, self.provider.name))
			self.assertEqual(specs[f"model:{self.chat_model.name}"], 2)
			self.assertEqual(specs[f"provider:{self.provider.name}"], 4)
		finally:
			frappe.db.set_value("AI Provider", self.provider.name, "max_concurrent_requests", 0)
			frappe.db.set_value("AI Model", self.chat_model.name, "max_concurrent_requests", 0)
			frappe.clear_document_cache("AI Model", self.chat_model.name)


# ---------------------------------------------------------------------------
# GOV-02 — provider rate limiting
# ---------------------------------------------------------------------------


class TestProviderRateLimit(AIPlatformTestCase):
	def test_burst_beyond_the_window_is_refused_with_a_real_retry_delay(self):
		scope = _scope("provider")
		for _ in range(3):
			self.assertTrue(limits.check_rate_limit(scope, 3)["enforced"])

		with self.assertRaises(RateLimitExceededError) as caught:
			limits.check_rate_limit(scope, 3)

		self.assertGreater(caught.exception.retry_after_ms, 0)
		self.assertLessEqual(caught.exception.retry_after_ms, limits.RATE_WINDOW_MS)
		limits.reset_scope(scope, kind="rate")

	def test_zero_disables_the_window(self):
		scope = _scope("provider")
		for _ in range(50):
			self.assertFalse(limits.check_rate_limit(scope, 0)["enforced"])
		limits.reset_scope(scope, kind="rate")

	def test_windows_are_independent_per_scope(self):
		first, second = _scope("provider"), _scope("provider")
		limits.check_rate_limit(first, 1)
		with self.assertRaises(RateLimitExceededError):
			limits.check_rate_limit(first, 1)
		# A saturated provider must not throttle a different provider.
		self.assertTrue(limits.check_rate_limit(second, 1)["enforced"])
		limits.reset_scope(first, kind="rate")
		limits.reset_scope(second, kind="rate")

	def test_configured_provider_limit_is_read_from_the_record(self):
		from ai_fr_hg.ai.governance import provider_rate_limit

		frappe.db.set_value("AI Provider", self.provider.name, "rate_limit_per_minute", 42)
		try:
			self.assertEqual(provider_rate_limit(self.provider.name), 42)
		finally:
			frappe.db.set_value("AI Provider", self.provider.name, "rate_limit_per_minute", 0)


# ---------------------------------------------------------------------------
# GOV-03 — quota reservation
# ---------------------------------------------------------------------------


class TestQuotaReservation(AIPlatformTestCase):
	def setUp(self):
		super().setUp()
		self.user = f"quota-{uuid.uuid4().hex[:8]}@example.com"

	def tearDown(self):
		limits.reset_scope(self.user, kind="quota")
		super().tearDown()

	def _reserve(self, **overrides):
		payload = {
			"user": self.user,
			"request_limit": 0,
			"token_limit": 0,
			"committed_requests": 0,
			"committed_tokens": 0,
			"estimated_tokens": 0,
		}
		payload.update(overrides)
		return limits.reserve_quota(**payload)

	def test_in_flight_requests_count_against_the_limit(self):
		# The pre-GOV-03 check counted only *finished* AI Execution Log rows,
		# so three simultaneous requests all observed zero usage and all
		# passed. The ledger makes the second and third see the first.
		first = self._reserve(request_limit=2)
		second = self._reserve(request_limit=2)
		self.assertTrue(first.enforced)
		self.assertTrue(second.enforced)

		with self.assertRaises(QuotaExceededError):
			self._reserve(request_limit=2)

		second.release()
		third = self._reserve(request_limit=2)
		self.assertTrue(third.enforced)
		first.release()
		third.release()

	def test_committed_database_usage_is_added_to_in_flight_usage(self):
		held = self._reserve(request_limit=3, committed_requests=2)
		self.assertTrue(held.enforced)
		with self.assertRaises(QuotaExceededError):
			self._reserve(request_limit=3, committed_requests=2)
		held.release()

	def test_token_budget_reserves_the_worst_case_not_the_average(self):
		held = self._reserve(token_limit=1000, estimated_tokens=600)
		self.assertTrue(held.enforced)
		with self.assertRaises(QuotaExceededError) as caught:
			self._reserve(token_limit=1000, estimated_tokens=600)
		self.assertIn("token limit", str(caught.exception).lower())
		held.release()
		self.assertTrue(self._reserve(token_limit=1000, estimated_tokens=600).enforced)

	def test_abandoned_reservation_expires(self):
		with patch.object(limits, "MIN_LEASE_TTL", 1), patch.object(limits, "RESERVATION_GRACE_SECONDS", 0):
			self._reserve(request_limit=1, ttl=1)
			with self.assertRaises(QuotaExceededError):
				self._reserve(request_limit=1, ttl=1)
			time.sleep(1.3)
			recovered = self._reserve(request_limit=1, ttl=1)
			self.assertTrue(recovered.enforced)
			recovered.release()

	def test_no_configured_limit_reserves_nothing(self):
		held = self._reserve()
		self.assertFalse(held.enforced)
		self.assertEqual(limits.current_usage(self.user), 0)

	def test_release_is_idempotent(self):
		held = self._reserve(request_limit=1)
		held.release()
		held.release()
		self.assertTrue(self._reserve(request_limit=1).enforced)

	def test_policy_layer_reserves_for_a_real_user(self):
		from ai_fr_hg.ai.governance import reserve_request_quota

		policy = frappe.get_doc(
			{
				"doctype": "AI Resource Policy",
				"policy_name": f"Quota Policy {uuid.uuid4().hex[:6]}",
				"enabled": 1,
				"priority": 1,
				"user": "Administrator",
				"max_requests_per_hour": 1,
			}
		)
		policy.insert(ignore_permissions=True)
		try:
			# Administrator is deliberately exempt from policy enforcement.
			held = reserve_request_quota(self.chat_model)
			self.assertFalse(held.enforced)
		finally:
			policy.delete(ignore_permissions=True)


# ---------------------------------------------------------------------------
# GOV-04 / PROV-02 — model type and capability enforcement in the engine
# ---------------------------------------------------------------------------


class TestModelTypeAndCapabilityEnforcement(AIPlatformTestCase):
	def test_explicit_embedding_model_is_refused_for_chat(self):
		from ai_fr_hg.ai.engine import resolve_model

		with self.assertRaises(ModelNotAvailableError) as caught:
			resolve_model(self.embedding_model.name, "Chat")
		self.assertIn("cannot serve", str(caught.exception))

	def test_explicit_chat_model_is_refused_for_embedding(self):
		from ai_fr_hg.ai.engine import resolve_model

		with self.assertRaises(ModelNotAvailableError):
			resolve_model(self.chat_model.name, "Embedding")

	def test_compatible_explicit_model_still_resolves(self):
		from ai_fr_hg.ai.engine import resolve_model

		self.assertEqual(resolve_model(self.chat_model.name, "Chat").name, self.chat_model.name)
		self.assertEqual(
			resolve_model(self.embedding_model.name, "Embedding").name, self.embedding_model.name
		)

	def test_run_chat_rejects_a_wrong_type_model_before_calling_the_runtime(self):
		from ai_fr_hg.ai import engine

		with patch.object(engine, "get_provider") as provider:
			with self.assertRaises(ModelNotAvailableError):
				engine.run_chat([{"role": "user", "content": "hi"}], model=self.embedding_model.name)
			provider.assert_not_called()

	def test_run_chat_rejects_tools_a_model_cannot_execute(self):
		from ai_fr_hg.ai import engine

		original = frappe.db.get_value("AI Model", self.chat_model.name, "supports_tools")
		frappe.db.set_value("AI Model", self.chat_model.name, "supports_tools", 0)
		frappe.clear_document_cache("AI Model", self.chat_model.name)
		try:
			with patch.object(engine, "get_provider") as provider:
				with self.assertRaises(ModelNotAvailableError) as caught:
					engine.run_chat(
						[{"role": "user", "content": "hi"}],
						model=self.chat_model.name,
						tools=[{"type": "function", "function": {"name": "noop"}}],
					)
				self.assertIn("tool calls", str(caught.exception))
				provider.assert_not_called()
		finally:
			frappe.db.set_value("AI Model", self.chat_model.name, "supports_tools", original)
			frappe.clear_document_cache("AI Model", self.chat_model.name)

	def test_capability_fields_default_to_the_adapter_transport(self):
		# PROV-02 must not silently disable a working install: a model created
		# without explicit capability flags keeps the behaviour it had when
		# only the adapter was consulted.
		model = frappe.get_cached_doc("AI Model", self.chat_model.name)
		self.assertTrue(model.supports_streaming)
		self.assertTrue(model.supports_tools)
		self.assertTrue(model.supports_json_mode)

	def test_effective_capability_intersects_provider_and_model(self):
		from ai_fr_hg.ai.engine import effective_capabilities

		original = frappe.db.get_value("AI Model", self.chat_model.name, "supports_tools")
		frappe.db.set_value("AI Model", self.chat_model.name, "supports_tools", 1)
		frappe.clear_document_cache("AI Model", self.chat_model.name)
		try:
			model_doc = frappe.get_cached_doc("AI Model", self.chat_model.name)
			caps = effective_capabilities(model_doc)
			# The fixture provider is Ollama, whose adapter transports tools.
			self.assertTrue(caps["tools"])

			capability.record_probe_failure(self.chat_model.name, "tools")
			try:
				self.assertFalse(effective_capabilities(model_doc)["tools"])
			finally:
				capability.clear_probe(self.chat_model.name)
			self.assertTrue(effective_capabilities(model_doc)["tools"])
		finally:
			frappe.db.set_value("AI Model", self.chat_model.name, "supports_tools", original)
			frappe.clear_document_cache("AI Model", self.chat_model.name)


# ---------------------------------------------------------------------------
# PROV-01 — equivalent-model failover and truthful audit identity
# ---------------------------------------------------------------------------


class TestEquivalentModelFailover(AIPlatformTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.backup_provider = cls._ensure_backup_provider()
		cls.backup_model = cls._ensure_backup_model()

	@classmethod
	def _ensure_backup_provider(cls):
		name = "Phase 6 Backup Provider"
		if frappe.db.exists("AI Provider", name):
			return frappe.get_doc("AI Provider", name)
		doc = frappe.get_doc(
			{
				"doctype": "AI Provider",
				"provider_name": name,
				"provider_type": "Ollama",
				"base_url": "http://127.0.0.1:11435",
				"enabled": 1,
				"priority": 5,
				"request_timeout": 30,
			}
		)
		doc.insert(ignore_permissions=True)
		return doc

	@classmethod
	def _ensure_backup_model(cls):
		name = "Phase 6 Backup Chat Model"
		if frappe.db.exists("AI Model", name):
			return frappe.get_doc("AI Model", name)
		doc = frappe.get_doc(
			{
				"doctype": "AI Model",
				"model_label": name,
				"provider": cls.backup_provider.name,
				# Deliberately a *different* runtime name: the old failover
				# reused the primary's model_name, which does not exist here.
				"model_name": "backup-chat-q4",
				"model_type": "Chat",
				"enabled": 1,
				"context_window": 8192,
			}
		)
		doc.insert(ignore_permissions=True)
		return doc

	def test_failover_targets_are_real_models_on_other_providers(self):
		from ai_fr_hg.ai.engine import resolve_failover_attempts

		model_doc = frappe.get_cached_doc("AI Model", self.chat_model.name)
		attempts = resolve_failover_attempts(model_doc)
		targets = {(row["provider"], row["model"].name) for row in attempts}

		self.assertIn((self.backup_provider.name, self.backup_model.name), targets)
		for row in attempts:
			self.assertNotEqual(row["provider"], model_doc.provider)
			self.assertEqual(row["model"].model_type, "Chat")

	def test_embedding_failover_never_crosses_dimensions(self):
		from ai_fr_hg.ai.engine import resolve_failover_attempts

		frappe.db.set_value("AI Model", self.embedding_model.name, "embedding_dimensions", 768)
		frappe.clear_document_cache("AI Model", self.embedding_model.name)
		mismatched = frappe.get_doc(
			{
				"doctype": "AI Model",
				"model_label": f"Backup Embed {uuid.uuid4().hex[:6]}",
				"provider": self.backup_provider.name,
				"model_name": "backup-embed",
				"model_type": "Embedding",
				"enabled": 1,
				"embedding_dimensions": 1024,
			}
		)
		mismatched.insert(ignore_permissions=True)
		try:
			model_doc = frappe.get_cached_doc("AI Model", self.embedding_model.name)
			attempts = resolve_failover_attempts(model_doc)
			self.assertNotIn(mismatched.name, {row["model"].name for row in attempts})
		finally:
			mismatched.delete(ignore_permissions=True)
			frappe.db.set_value("AI Model", self.embedding_model.name, "embedding_dimensions", 0)
			frappe.clear_document_cache("AI Model", self.embedding_model.name)

	def test_execution_log_records_the_model_that_actually_answered(self):
		from ai_fr_hg.ai import engine

		calls = []

		class _Adapter:
			supports_streaming = False

			def __init__(self, provider_name):
				self.provider_name = provider_name

			def chat(self, messages, model, options=None, tools=None, json_schema=None):
				calls.append((self.provider_name, model))
				if self.provider_name != TestEquivalentModelFailover.backup_provider.name:
					raise ProviderOfflineError("primary runtime is down")
				return CompletionResult(content="ok", model=model, total_tokens=7, duration_ms=5)

		def fake_get_provider(name=None):
			return _Adapter(name)

		with patch.object(engine, "get_provider", side_effect=fake_get_provider):
			result = engine.run_chat([{"role": "user", "content": "hi"}], model=self.chat_model.name)

		self.assertEqual(result.content, "ok")
		# The second attempt must dial the backup's own runtime model name.
		self.assertIn((self.backup_provider.name, "backup-chat-q4"), calls)

		log = frappe.get_all(
			"AI Execution Log",
			filters={"status": "Success", "conversation": ["is", "not set"]},
			fields=["name", "provider", "model"],
			order_by="creation desc",
			limit=1,
		)[0]
		self.assertEqual(log.provider, self.backup_provider.name)
		self.assertEqual(log.model, self.backup_model.name)
		# The originally requested model must not be credited with the answer.
		self.assertNotEqual(log.model, self.chat_model.name)


# ---------------------------------------------------------------------------
# OPS-02 — health-check scheduling
# ---------------------------------------------------------------------------


class TestHealthCheckScheduling(AIPlatformTestCase):
	def _set_last_check(self, minutes_ago: int | None):
		from frappe.utils import add_to_date, now_datetime

		value = None if minutes_ago is None else add_to_date(now_datetime(), minutes=-minutes_ago)
		frappe.db.set_value("AI Provider", self.provider.name, "last_health_check", value)

	def test_interval_not_divisible_by_five_is_honoured(self):
		from ai_fr_hg.ai.monitoring import claim_due_providers

		# The old minute-modulo test could never schedule a 7-minute interval
		# correctly against a 5-minute cron.
		self._set_last_check(8)
		self.assertIn(self.provider.name, claim_due_providers(7))

		self._set_last_check(3)
		self.assertNotIn(self.provider.name, claim_due_providers(7))

	def test_a_due_provider_is_claimed_exactly_once(self):
		from ai_fr_hg.ai.monitoring import claim_due_providers

		self._set_last_check(120)
		first = claim_due_providers(15)
		second = claim_due_providers(15)
		self.assertIn(self.provider.name, first)
		self.assertNotIn(self.provider.name, second)

	def test_never_checked_provider_is_due(self):
		from ai_fr_hg.ai.monitoring import claim_due_providers

		self._set_last_check(None)
		self.assertIn(self.provider.name, claim_due_providers(15))

	def test_disabled_provider_is_never_claimed(self):
		from ai_fr_hg.ai.monitoring import claim_due_providers

		self._set_last_check(120)
		frappe.db.set_value("AI Provider", self.provider.name, "enabled", 0)
		try:
			self.assertNotIn(self.provider.name, claim_due_providers(15))
		finally:
			frappe.db.set_value("AI Provider", self.provider.name, "enabled", 1)

	def test_scheduler_task_no_longer_uses_minute_modulo(self):
		from pathlib import Path

		source = Path(frappe.get_app_path("ai_fr_hg", "tasks.py")).read_text()
		self.assertNotIn("minute % interval", source)
		self.assertIn("interval_minutes=interval", source)


# ---------------------------------------------------------------------------
# LEARN-01 — script reports with working filters
# ---------------------------------------------------------------------------


class TestLearningReports(AIPlatformTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		# Candidate creation is refused when the Learning Loop is disabled.
		frappe.db.set_single_value("AI Platform Settings", "learning_enabled", 1)
		frappe.clear_cache(doctype="AI Platform Settings")
		cls.active = cls._memory("Phase 6 active memory", "Active", 9)
		cls.archived = cls._memory("Phase 6 archived memory", "Archived", 0)

	@classmethod
	def _memory(cls, content, status, usage):
		"""Create a memory the way the platform does: by promoting a candidate.

		`AI Memory.before_insert` refuses a direct insert - memories exist only
		as the product of an approved `AI Knowledge Candidate`. The fixture
		honours that invariant instead of bypassing it.
		"""
		candidate = frappe.get_doc(
			{
				"doctype": "AI Knowledge Candidate",
				"title": content[:100],
				"content": content,
				"candidate_type": "Fact",
				"source_type": "Explicit Teaching",
				"target_scope": "Global",
			}
		)
		candidate.flags.ignore_permissions = True
		candidate.insert(ignore_permissions=True)

		doc = frappe.get_doc(
			{
				"doctype": "AI Memory",
				"content": content,
				"memory_type": "Fact",
				"status": status,
				"scope": "Global",
				"usage_count": usage,
				"source_candidate": candidate.name,
			}
		)
		doc.flags.ignore_permissions = True
		doc.flags.from_learning = True
		doc.insert(ignore_permissions=True)
		return doc

	def test_reports_are_registered_as_script_reports(self):
		for name in ("Learning Activity", "Memory Usage", "Skill Summary"):
			doc = frappe.get_doc("Report", name)
			self.assertEqual(doc.report_type, "Script Report", name)
			self.assertEqual(doc.is_standard, "Yes", name)
			# Leftover static SQL would still win over `execute()`.
			self.assertFalse((doc.query or "").strip(), name)

	def test_status_filter_actually_filters(self):
		from ai_fr_hg.ai_learning.report.memory_usage.memory_usage import execute

		_columns, rows = execute({"status": "Active"})
		contents = {row["Content"] for row in rows}
		self.assertIn(self.active.content, contents)
		self.assertNotIn(self.archived.content, contents)

	def test_unfiltered_run_returns_both_records(self):
		from ai_fr_hg.ai_learning.report.memory_usage.memory_usage import execute

		_columns, rows = execute({})
		contents = {row["Content"] for row in rows}
		self.assertIn(self.active.content, contents)
		self.assertIn(self.archived.content, contents)

	def test_numeric_filter_actually_filters(self):
		from ai_fr_hg.ai_learning.report.memory_usage.memory_usage import execute

		_columns, rows = execute({"min_usage": 5})
		self.assertTrue(all(row["Used"] >= 5 for row in rows))
		self.assertIn(self.active.content, {row["Content"] for row in rows})

	def test_invalid_filter_value_is_refused(self):
		from ai_fr_hg.ai.learning_utils import ReportFilterError
		from ai_fr_hg.ai_learning.report.memory_usage.memory_usage import execute

		with self.assertRaises(ReportFilterError):
			execute({"status": "Active'; drop table `tabAI Memory`; --"})

	def test_report_runs_through_the_frappe_report_framework(self):
		# Proves the Script Report wiring end to end through Frappe's own
		# query_report entry point, not just by importing the module.
		from frappe.desk.query_report import run

		response = run(
			"Memory Usage",
			filters={"status": "Active"},
			ignore_prepared_report=True,
			are_default_filters=False,
		)
		rows = response.get("result") or []
		self.assertTrue(response.get("columns"))
		contents = {row.get("Content") for row in rows if isinstance(row, dict)}
		self.assertIn(self.active.content, contents)
		self.assertNotIn(self.archived.content, contents)

	def test_unauthorised_user_cannot_execute_the_report(self):
		user = frappe.get_doc(
			{
				"doctype": "User",
				"email": f"report-denied-{uuid.uuid4().hex[:8]}@example.com",
				"first_name": "Report Denied",
				"send_welcome_email": 0,
				"roles": [],
			}
		).insert(ignore_permissions=True)
		try:
			from ai_fr_hg.ai_learning.report.memory_usage.memory_usage import execute

			frappe.set_user(user.name)
			with self.assertRaises(frappe.PermissionError):
				execute({})
		finally:
			frappe.set_user("Administrator")
			user.delete(ignore_permissions=True)

	def test_learning_activity_date_filters_are_enforced(self):
		from ai_fr_hg.ai.learning_utils import ReportFilterError
		from ai_fr_hg.ai_learning.report.learning_activity.learning_activity import execute

		columns, _rows = execute({"from_date": "2020-01-01"})
		self.assertTrue(columns)
		with self.assertRaises(ReportFilterError):
			execute({"from_date": "yesterday"})
