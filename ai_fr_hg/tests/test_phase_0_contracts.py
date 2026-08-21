# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Dependency-free regressions for the Phase 0 product and release contract."""

import json
import re
from pathlib import Path
from unittest import TestCase

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "ai_fr_hg"
FRAPPE_V17_SHA = "d7000da3d5862087d3df08e009fe76518ea649c4"
AUDIT_ID_PATTERN = re.compile(
	r"^### ((?:SEC|RET|CHAT|ING|INT|TRN|PAT|AUTO|PIPE|TASK|GOV|PROV|FILE|OPS|LEARN)-\d{2})\b",
	re.MULTILINE,
)
REGISTER_ID_PATTERN = re.compile(
	r"^\| ((?:SEC|RET|CHAT|ING|INT|TRN|PAT|AUTO|PIPE|TASK|GOV|PROV|FILE|OPS|LEARN)-\d{2}) \|",
	re.MULTILINE,
)


def load_doctype(relative_path: str) -> dict:
	return json.loads((APP / relative_path).read_text())


def field(meta: dict, fieldname: str) -> dict:
	return next(item for item in meta["fields"] if item.get("fieldname") == fieldname)


class TestUnsupportedCapabilityExposure(TestCase):
	def test_document_encryption_is_hidden_read_only_and_off(self):
		meta = load_doctype("ai_core/doctype/ai_platform_settings/ai_platform_settings.json")
		control = field(meta, "encrypt_documents")

		self.assertEqual(control.get("hidden"), 1)
		self.assertEqual(control.get("read_only"), 1)
		self.assertEqual(control.get("default"), "0")
		self.assertIn("Unsupported", control["label"])

	def test_folder_ingestion_is_not_a_selectable_source(self):
		meta = load_doctype("ai_knowledge/doctype/ai_document/ai_document.json")
		options = field(meta, "source_type")["options"].splitlines()

		self.assertEqual(options, ["File", "Text", "URL", "DocType Record"])

	def test_reranker_is_not_selectable_and_versions_are_hidden(self):
		meta = load_doctype("ai_core/doctype/ai_model/ai_model.json")
		options = field(meta, "model_type")["options"].splitlines()

		self.assertEqual(options, ["Chat", "Completion", "Embedding", "Vision"])
		self.assertEqual(field(meta, "versions_sec").get("hidden"), 1)
		self.assertEqual(field(meta, "versions").get("hidden"), 1)
		self.assertEqual(field(meta, "versions").get("read_only"), 1)

	def test_target_doctype_mapping_is_hidden_and_read_only(self):
		meta = load_doctype("ai_knowledge/doctype/ai_extraction_schema/ai_extraction_schema.json")
		control = field(meta, "target_doctype")

		self.assertEqual(control.get("hidden"), 1)
		self.assertEqual(control.get("read_only"), 1)
		self.assertIn("JSON", control["description"])

	def test_translation_label_describes_extracted_text_only(self):
		meta = load_doctype("ai_knowledge/doctype/ai_translation/ai_translation.json")
		control = field(meta, "preserve_formatting")

		self.assertEqual(control["label"], "Preserve Extracted-Text Structure")
		self.assertIn("does not reconstruct", control["description"])

	def test_msg_is_not_registered_and_scanned_pdf_warning_is_truthful(self):
		registry = (APP / "ai/readers/__init__.py").read_text()
		pdf_reader = (APP / "ai/readers/office.py").read_text()

		self.assertNotIn('"msg": EmailReader', registry)
		self.assertIn('"eml": EmailReader', registry)
		self.assertIn('"odp": OdpReader', registry)
		self.assertIn("Scanned-PDF OCR is not supported", pdf_reader)

	def test_extraction_evidence_is_a_durable_ai_document_field(self):
		meta = load_doctype("ai_knowledge/doctype/ai_document/ai_document.json")
		control = field(meta, "extraction_evidence")

		self.assertEqual(control.get("fieldtype"), "Code")
		self.assertEqual(control.get("options"), "JSON")
		self.assertEqual(control.get("read_only"), 1)
		self.assertIn("extraction_evidence", meta["field_order"])


class TestControlledBaseline(TestCase):
	def test_every_audit_id_is_registered_exactly_once(self):
		audit = (ROOT / "docs/DEVELOPMENT_PLAN.md").read_text()
		register = (ROOT / "docs/GAP_REGISTER.md").read_text()
		audit_ids = AUDIT_ID_PATTERN.findall(audit)
		registered_ids = REGISTER_ID_PATTERN.findall(register)

		# 79 original findings, plus CHAT-09 (amended in on 2026-08-21 when the
		# CHAT-02 reopening exposed it), plus CHAT-10 and FILE-08 (amended in
		# on 2026-08-21 by the CLOSED-claim re-audit: a Desk button that faked
		# its server call, and eleven endpoints left published without a
		# caller when FILE-05 removed the custom picker).
		self.assertEqual(len(audit_ids), 82)
		self.assertEqual(len(audit_ids), len(set(audit_ids)))
		self.assertEqual(len(registered_ids), len(set(registered_ids)))
		self.assertEqual(set(audit_ids), set(registered_ids))

	def test_architecture_decisions_cover_phase_zero_choices(self):
		decisions = (ROOT / "docs/ARCHITECTURE_DECISIONS.md").read_text()

		for decision in range(1, 9):
			self.assertIn(f"ADR-{decision:03d}", decisions)
		self.assertIn("MariaDB 11.8 only", decisions)
		self.assertIn(FRAPPE_V17_SHA, decisions)

	def test_verification_model_is_documented_with_its_limits(self):
		"""ADR-015..019 record the verification model and what it cannot prove.

		The point of these ADRs is the non-claims. If the limitations were
		dropped, the documents would read as a guarantee of production
		readiness that no tier in this repository supports.
		"""
		decisions = (ROOT / "docs/ARCHITECTURE_DECISIONS.md").read_text()

		for decision in range(15, 20):
			self.assertIn(f"ADR-{decision:03d}", decisions)

		# The runtime tier is unavailable, and nothing may imply otherwise.
		for limitation in (
			"InnoDB isolation",
			"bench migrate",
			"browser",
			"CHAT-09",
		):
			self.assertIn(limitation, decisions)

	def test_ci_targets_an_immutable_frappe_v17_development_revision(self):
		workflow = (ROOT / ".github/workflows/ci.yml").read_text()

		self.assertIn(f"FRAPPE_SHA: {FRAPPE_V17_SHA}", workflow)
		self.assertIn('--frappe-branch "$FRAPPE_BRANCH"', workflow)
		self.assertIn('fetch --depth 1 upstream "$FRAPPE_SHA"', workflow)
		self.assertIn('frappe.__version__ == "17.0.0-dev"', workflow)
		self.assertIn("bench --site test_site migrate", workflow)
		self.assertIn("bench --site test_site run-tests --app ai_fr_hg", workflow)

	def test_quality_workflow_has_required_independent_statuses(self):
		workflow = (ROOT / ".github/workflows/linter.yml").read_text()

		for status in ("Linter", "Frontend static", "Dependency audit"):
			self.assertIn(f"name: {status}", workflow)
		self.assertIn("node --check", workflow)
		self.assertIn("pre-commit==4.6.2", workflow)
		self.assertIn("FRAPPE_SEMGREP_SHA: b101a16e69df049b3fed1478bcc16223e957cca2", workflow)
		self.assertIn("semgrep==1.173.0", workflow)
		self.assertRegex(
			workflow,
			r"semgrep scan \\\n\s+--error \\\n\s+--metrics=off",
		)
		self.assertNotRegex(workflow, r"semgrep scan[\s\\-]*--no-suppress-errors")
		self.assertIn("pip-audit==2.10.1", workflow)
		self.assertIn("docs/phase-reports/export_optional_requirements.py", workflow)
		self.assertIn('--requirement "$AUDIT_REQUIREMENTS"', workflow)
		self.assertIn("--progress-spinner off", workflow)
		self.assertNotIn("--skip-editable", workflow)
		self.assertNotIn("r/python.lang.correctness", workflow)
		self.assertIn("semgrep scan", workflow)
		self.assertIn("--metrics=off", workflow)

	def test_normalization_patch_is_registered(self):
		patches = (APP / "patches.txt").read_text().splitlines()

		self.assertEqual(
			patches.count("ai_fr_hg.patches.v0_0_14_disable_unsupported_controls"),
			1,
		)

	def test_primary_product_guides_state_unsupported_boundaries(self):
		readme = (ROOT / "README.md").read_text()
		configuration = (ROOT / "docs/CONFIGURATION.md").read_text()
		translation = (ROOT / "docs/TRANSLATION.md").read_text()
		project = (ROOT / "pyproject.toml").read_text()

		# The advertised extension count must match the real registry, so the
		# README cannot drift from what a parser actually backs.
		from ai_fr_hg.ai.readers import BUILTIN_READERS

		self.assertIn(f"{len(BUILTIN_READERS)} registered extensions", readme)
		self.assertIn("PostgreSQL is not currently supported", readme)
		self.assertIn("scanned-pdf ocr", readme.lower())
		self.assertIn("not supported", readme.lower())
		self.assertIn("does not OCR scanned PDFs", configuration)
		self.assertIn("not a complete backup/restore mechanism", readme)
		self.assertRegex(translation, r"does not\s+reconstruct")
		self.assertNotIn("A complete, fully local", project)
		self.assertNotIn("36 registered", readme)
		self.assertNotIn("37 registered", readme)
		# ADR-012: legacy OLE formats stay declared-unsupported.
		self.assertIn("`.doc`/`.xls`/`.ppt`", readme)
		self.assertNotIn("37 ingestible", translation)
		self.assertNotIn("Translation-memory isolation hardening remains open", readme)
		self.assertNotIn("Connection-level network hardening is still tracked", readme)
		self.assertNotIn("SEC-04 connection hardening remains open", translation)


class TestRepositoryMetadata(TestCase):
	def test_all_tracked_json_documents_parse(self):
		for path in sorted(ROOT.glob("**/*.json")):
			if ".git" in path.parts:
				continue
			with self.subTest(path=path.relative_to(ROOT)):
				json.loads(path.read_text())
