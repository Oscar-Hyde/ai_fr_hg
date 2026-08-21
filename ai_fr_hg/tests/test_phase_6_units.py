# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Dependency-free Phase 6 policy tests.

These cover the pure decision layer that the Redis- and database-backed
enforcement in :mod:`ai_fr_hg.ai.limits`, :mod:`ai_fr_hg.ai.engine` and the
learning reports depends on. They run without a bench, so a wrong rule is
caught before the integration suite ever starts a runtime.

Findings covered here: GOV-04 (model type), PROV-01 (failover equivalence),
PROV-02 (effective capability), LEARN-01 (validated report filters).
"""

from unittest import TestCase

from ai_fr_hg.ai import capability
from ai_fr_hg.ai.learning_utils import (
	REPORT_ROW_LIMIT,
	ReportFilterError,
	normalise_report_filters,
)


class _Adapter:
	"""Stand-in for a provider adapter class and its transport flags."""

	def __init__(self, **flags):
		defaults = {
			"supports_streaming": True,
			"supports_tools": True,
			"supports_embeddings": True,
			"supports_json_mode": True,
			"supports_vision": True,
		}
		defaults.update(flags)
		for name, value in defaults.items():
			setattr(self, name, value)


def _model(**overrides) -> dict:
	base = {
		"name": "Chat A",
		"provider": "Primary",
		"model_name": "llama3",
		"model_type": "Chat",
		"family": "llama",
		"parameter_size": "8B",
		"context_window": 8192,
		"embedding_dimensions": 0,
		"enabled": 1,
		"is_default": 0,
		"supports_tools": 1,
		"supports_vision": 1,
		"supports_streaming": 1,
		"supports_json_mode": 1,
		"provider_priority": 0,
	}
	base.update(overrides)
	return base


# ---------------------------------------------------------------------------
# GOV-04
# ---------------------------------------------------------------------------


class TestModelTypeCompatibility(TestCase):
	def test_embedding_model_cannot_serve_a_chat_request(self):
		error = capability.model_type_error("Chat", _model(model_type="Embedding"))
		self.assertEqual(error, "incompatible_model_type")

	def test_chat_model_cannot_serve_an_embedding_request(self):
		self.assertEqual(
			capability.model_type_error("Embedding", _model(model_type="Chat")),
			"incompatible_model_type",
		)

	def test_vision_model_may_serve_chat(self):
		self.assertIsNone(capability.model_type_error("Chat", _model(model_type="Vision")))

	def test_chat_model_serves_vision_only_when_it_declares_vision(self):
		self.assertIsNone(capability.model_type_error("Vision", _model(model_type="Chat", supports_vision=1)))
		self.assertEqual(
			capability.model_type_error("Vision", _model(model_type="Chat", supports_vision=0)),
			"vision_not_supported",
		)

	def test_missing_and_unknown_types_are_named_distinctly(self):
		self.assertEqual(capability.model_type_error("Chat", _model(model_type="")), "unknown_model_type")
		self.assertEqual(capability.model_type_error("Rerank", _model()), "unknown_operation_type")

	def test_every_declared_model_type_option_is_covered(self):
		# Keeps the policy table honest against the DocType Select options.
		for option in ("Chat", "Completion", "Embedding", "Vision"):
			self.assertIn(option, capability.COMPATIBLE_MODEL_TYPES)


# ---------------------------------------------------------------------------
# PROV-02
# ---------------------------------------------------------------------------


class TestEffectiveCapability(TestCase):
	def test_model_flag_off_defeats_a_capable_provider(self):
		caps = capability.effective_capabilities(_Adapter(), _model(supports_tools=0))
		self.assertFalse(caps["tools"])
		self.assertTrue(caps["streaming"])

	def test_provider_flag_off_defeats_a_capable_model(self):
		caps = capability.effective_capabilities(_Adapter(supports_json_mode=False), _model())
		self.assertFalse(caps["json_mode"])

	def test_failed_probe_defeats_both_declarations(self):
		caps = capability.effective_capabilities(_Adapter(), _model(), failed_probes={"tools"})
		self.assertFalse(caps["tools"])
		self.assertTrue(caps["vision"])

	def test_embeddings_follow_model_type_not_a_check_field(self):
		self.assertFalse(capability.effective_capabilities(_Adapter(), _model())["embeddings"])
		self.assertTrue(
			capability.effective_capabilities(_Adapter(), _model(model_type="Embedding"))["embeddings"]
		)

	def test_capability_error_names_the_first_missing_capability(self):
		caps = capability.effective_capabilities(_Adapter(), _model(supports_tools=0))
		self.assertEqual(capability.capability_error(caps, tools=True), "tools")
		self.assertIsNone(capability.capability_error(caps, json_schema=True))

	def test_streaming_is_never_a_hard_failure(self):
		caps = capability.effective_capabilities(_Adapter(supports_streaming=False), _model())
		self.assertFalse(caps["streaming"])
		# No argument of capability_error can be satisfied only by streaming.
		self.assertIsNone(capability.capability_error(caps))

	def test_runtime_refusal_is_classified_onto_a_capability(self):
		self.assertEqual(
			capability.classify_capability_failure("HTTP 400: model does not support tools"),
			"tools",
		)
		self.assertIsNone(capability.classify_capability_failure("connection refused"))
		self.assertIsNone(capability.classify_capability_failure(None))


# ---------------------------------------------------------------------------
# PROV-01
# ---------------------------------------------------------------------------


class TestFailoverEquivalence(TestCase):
	def test_same_provider_is_never_a_failover_target(self):
		source = _model()
		self.assertIsNone(capability.score_failover_candidate(source, _model(name="Chat B")))

	def test_incompatible_type_is_excluded(self):
		source = _model()
		candidate = _model(name="E1", provider="Backup", model_type="Embedding")
		self.assertIsNone(capability.score_failover_candidate(source, candidate))

	def test_disabled_candidate_is_excluded(self):
		source = _model()
		candidate = _model(name="B1", provider="Backup", enabled=0)
		self.assertIsNone(capability.score_failover_candidate(source, candidate))

	def test_embedding_failover_requires_identical_dimensions(self):
		source = _model(model_type="Embedding", embedding_dimensions=768)
		same = _model(name="E-same", provider="Backup", model_type="Embedding", embedding_dimensions=768)
		different = _model(
			name="E-diff", provider="Backup", model_type="Embedding", embedding_dimensions=1024
		)
		unknown = _model(name="E-none", provider="Backup", model_type="Embedding", embedding_dimensions=0)
		self.assertIsNotNone(capability.score_failover_candidate(source, same))
		self.assertIsNone(capability.score_failover_candidate(source, different))
		self.assertIsNone(capability.score_failover_candidate(source, unknown))

	def test_required_capability_excludes_a_model_that_lacks_it(self):
		source = _model()
		candidate = _model(name="B1", provider="Backup", supports_tools=0)
		self.assertIsNone(capability.score_failover_candidate(source, candidate, required={"tools"}))
		self.assertIsNotNone(capability.score_failover_candidate(source, candidate))

	def test_same_runtime_name_outranks_a_merely_compatible_model(self):
		source = _model()
		ranked = capability.rank_failover_candidates(
			source,
			[
				_model(name="Other", provider="Backup", model_name="mistral", family="mistral"),
				_model(name="Twin", provider="Backup", model_name="llama3"),
			],
		)
		self.assertEqual([row["model"] for row in ranked], ["Twin", "Other"])

	def test_provider_priority_breaks_a_tie_deterministically(self):
		source = _model()
		candidates = [
			_model(name="Slow", provider="B2", model_name="llama3", provider_priority=90),
			_model(name="Fast", provider="B1", model_name="llama3", provider_priority=0),
		]
		self.assertEqual(
			[row["model"] for row in capability.rank_failover_candidates(source, candidates)],
			["Fast", "Slow"],
		)
		# Reversing the input must not change the outcome.
		self.assertEqual(
			[row["model"] for row in capability.rank_failover_candidates(source, list(reversed(candidates)))],
			["Fast", "Slow"],
		)

	def test_ranking_reports_the_actual_target_identity(self):
		source = _model()
		ranked = capability.rank_failover_candidates(
			source, [_model(name="Twin", provider="Backup", model_name="llama3-q4")]
		)
		self.assertEqual(ranked[0]["provider"], "Backup")
		self.assertEqual(ranked[0]["model"], "Twin")
		self.assertEqual(ranked[0]["model_name"], "llama3-q4")


# ---------------------------------------------------------------------------
# LEARN-01
# ---------------------------------------------------------------------------


class TestReportFilterContract(TestCase):
	def test_known_filters_are_coerced_and_unknown_keys_dropped(self):
		cleaned = normalise_report_filters(
			"Memory Usage", {"status": "Active", "min_usage": "12", "injected": "1"}
		)
		self.assertEqual(cleaned, {"status": "Active", "min_usage": 12})

	def test_cleared_select_controls_mean_no_filter(self):
		self.assertEqual(normalise_report_filters("Skill Summary", {"scope": "", "enabled": None}), {})

	def test_value_outside_the_declared_choice_set_is_rejected(self):
		with self.assertRaises(ReportFilterError):
			normalise_report_filters("Memory Usage", {"status": "Active' OR 1=1 --"})

	def test_non_numeric_int_filter_is_rejected(self):
		with self.assertRaises(ReportFilterError):
			normalise_report_filters("Memory Usage", {"min_usage": "lots"})

	def test_int_filter_is_clamped_to_its_declared_range(self):
		self.assertEqual(normalise_report_filters("Skill Summary", {"enabled": "7"})["enabled"], 1)
		self.assertEqual(normalise_report_filters("Skill Summary", {"enabled": "-3"})["enabled"], 0)

	def test_dates_must_be_iso_and_ordered(self):
		self.assertEqual(
			normalise_report_filters("Learning Activity", {"from_date": "2026-01-01"}),
			{"from_date": "2026-01-01"},
		)
		with self.assertRaises(ReportFilterError):
			normalise_report_filters("Learning Activity", {"from_date": "01/02/2026"})
		with self.assertRaises(ReportFilterError):
			normalise_report_filters(
				"Learning Activity", {"from_date": "2026-05-01", "to_date": "2026-01-01"}
			)

	def test_unknown_report_is_refused(self):
		with self.assertRaises(ReportFilterError):
			normalise_report_filters("Not A Report", {})

	def test_row_limit_is_bounded(self):
		self.assertGreater(REPORT_ROW_LIMIT, 0)
		self.assertLessEqual(REPORT_ROW_LIMIT, 5000)


# ---------------------------------------------------------------------------
# PROV-02 — non-regressive discovery defaults
# ---------------------------------------------------------------------------


class TestDiscoveryCapabilityDefaults(TestCase):
	def test_chat_model_inherits_the_adapter_transport(self):
		defaults = capability.discovery_capability_defaults(_Adapter(), "Chat", "llama3")
		self.assertEqual(defaults["supports_streaming"], 1)
		self.assertEqual(defaults["supports_tools"], 1)
		self.assertEqual(defaults["supports_json_mode"], 1)

	def test_adapter_without_tools_does_not_claim_tools(self):
		defaults = capability.discovery_capability_defaults(_Adapter(supports_tools=False), "Chat", "llama3")
		self.assertEqual(defaults["supports_tools"], 0)

	def test_vision_is_only_seeded_for_a_plausible_vision_model(self):
		self.assertEqual(
			capability.discovery_capability_defaults(_Adapter(), "Chat", "llama3")["supports_vision"], 0
		)
		self.assertEqual(
			capability.discovery_capability_defaults(_Adapter(), "Chat", "llava:13b")["supports_vision"],
			1,
		)
		self.assertEqual(
			capability.discovery_capability_defaults(_Adapter(), "Vision", "custom-eyes")["supports_vision"],
			1,
		)

	def test_embedding_models_claim_no_chat_capability(self):
		defaults = capability.discovery_capability_defaults(_Adapter(), "Embedding", "nomic-embed")
		self.assertEqual(set(defaults.values()), {0})


# ---------------------------------------------------------------------------
# Phase 6 source and registration contracts
# ---------------------------------------------------------------------------


class TestPhase6SourceContracts(TestCase):
	def setUp(self):
		from pathlib import Path

		self.app = Path(__file__).resolve().parents[1]

	def test_health_scheduling_no_longer_uses_minute_modulo(self):
		source = (self.app / "tasks.py").read_text()
		self.assertNotIn("minute % interval", source)
		self.assertIn("interval_minutes=interval", source)

	def test_health_claim_uses_the_canonical_for_update_pattern(self):
		source = (self.app / "ai/monitoring.py").read_text()
		self.assertIn("claim_due_providers", source)
		self.assertIn("for_update=True", source)

	def test_engine_reserves_rather_than_only_checking_quota(self):
		source = (self.app / "ai/engine.py").read_text()
		self.assertIn("reserve_request_quota", source)
		self.assertIn("acquire_leases", source)
		self.assertIn("check_rate_limit", source)
		self.assertIn("resolve_failover_attempts", source)
		# PROV-01: the audit trail must name the target that answered.
		self.assertIn("model=attempt_model.name", source)

	def test_admission_control_uses_native_frappe_cache(self):
		source = (self.app / "ai/limits.py").read_text()
		self.assertIn("frappe.cache()", source)
		# No parallel pagination, and no second job/lock subsystem.
		self.assertNotIn("limit_page_length", source)
		self.assertNotIn("limit_start", source)

	def test_phase_6_patch_is_registered_exactly_once(self):
		patches = (self.app / "patches.txt").read_text()
		entry = "ai_fr_hg.patches.v0_0_21_phase_6_governance"
		self.assertIn(entry, patches)
		self.assertEqual(patches.count(entry), 1)

	def test_learning_reports_declare_script_report(self):
		import json

		for folder, name in (
			("learning_activity", "Learning Activity"),
			("memory_usage", "Memory Usage"),
			("skill_summary", "Skill Summary"),
		):
			definition = json.loads((self.app / f"ai_learning/report/{folder}/{folder}.json").read_text())
			self.assertEqual(definition["report_type"], "Script Report", name)
			self.assertEqual(definition["is_standard"], "Yes", name)
			self.assertFalse((definition.get("query") or "").strip(), name)

	def test_capability_fields_are_documented_as_enforced(self):
		import json

		definition = json.loads((self.app / "ai_core/doctype/ai_model/ai_model.json").read_text())
		flags = {
			field["fieldname"]: field
			for field in definition["fields"]
			if field["fieldname"].startswith("supports_")
		}
		for fieldname, field in flags.items():
			self.assertIn("PROV-02", field.get("description", ""), fieldname)
		# Defaults must preserve pre-enforcement behaviour.
		self.assertEqual(flags["supports_tools"].get("default"), "1")
		self.assertEqual(flags["supports_json_mode"].get("default"), "1")
		self.assertEqual(flags["supports_streaming"].get("default"), "1")


# ---------------------------------------------------------------------------
# CHAT-02 regression — source contracts for the reopened finding
# ---------------------------------------------------------------------------


class TestChat02SequenceAllocatorContract(TestCase):
	def setUp(self):
		from pathlib import Path

		self.app = Path(__file__).resolve().parents[1]

	def test_allocator_never_reads_a_maximum_through_a_snapshot(self):
		source = (self.app / "ai/conversation.py").read_text()
		function = source.split("def allocate_sequence")[1].split("\ndef ")[0]
		# Assert against the executable body only; the docstring deliberately
		# names both rejected designs and would match either pattern.
		body = function.split('"""', 2)[2]

		# A plain `select max(sequence)` is served from the REPEATABLE READ
		# snapshot and reissues an already-committed sequence.
		self.assertNotIn("max(sequence)", body.replace("(select max(sequence)", ""))
		# `max(...) for update` triggers MariaDB 1020 via the MAX optimizer.
		self.assertNotIn("for update", body)
		# The allocation must be a DML current read on the parent row.
		self.assertIn("update `tabAI Conversation`", body)
		self.assertIn("message_sequence_counter", body)

	def test_sequence_constraints_are_schema_owned_not_patch_owned(self):
		# Frappe skips historical patches on a fresh install, so a constraint
		# defined only in a patch never exists on a new site. The DocType hook
		# is what Frappe runs for both fresh installs and migrations.
		controller = (self.app / "ai_conversation/doctype/ai_message/ai_message.py").read_text()
		self.assertIn("def on_doctype_update", controller)
		self.assertIn("ensure_sequence_constraints", controller)

		owner = (self.app / "ai/conversation_indexes.py").read_text()
		self.assertIn("ADD UNIQUE INDEX", owner)
		self.assertIn("def ensure_sequence_constraints", owner)
		# The failure must never be downgraded to a log line again.
		self.assertIn("raise RuntimeError", owner)

		patch = (self.app / "patches/v0_0_17_conversation_turn_identity.py").read_text()
		self.assertIn("ensure_sequence_constraints", patch)
		# One implementation; the patch and the hook both delegate to it.
		self.assertNotIn("ADD UNIQUE INDEX", patch)
		self.assertNotIn("ADD UNIQUE INDEX", controller)

	def test_missing_unique_index_is_a_failure_not_a_skip(self):
		test_source = (
			self.app / "ai_conversation/doctype/ai_conversation/test_ai_conversation.py"
		).read_text()
		self.assertNotIn("unique_conversation_sequence index is not present on this site", test_source)
		self.assertIn("test_duplicate_sequence_is_rejected_by_the_database", test_source)

	def test_counter_field_exists_on_the_conversation(self):
		import json

		meta = json.loads(
			(self.app / "ai_conversation/doctype/ai_conversation/ai_conversation.json").read_text()
		)
		field = next((f for f in meta["fields"] if f["fieldname"] == "message_sequence_counter"), None)
		self.assertIsNotNone(field)
		self.assertEqual(field["fieldtype"], "Int")
		self.assertTrue(field.get("read_only"))
