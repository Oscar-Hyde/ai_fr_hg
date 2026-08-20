# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Frappe integration coverage for Arabic / English / Hebrew translation."""

from unittest.mock import MagicMock, call, patch

import frappe

from ai_fr_hg.tests.integration_test_case import (
	AIPlatformTestCase,
	pseudo_translate,
	stub_translation_model,
)

STRUCTURED_SOURCE = (
	"# Maintenance Agreement\n\n"
	"[Page 1]\n\n"
	"The contractor shall deliver the works for 1,250,000 USD before 2026-03-15.\n\n"
	"- Scope of work is fixed for the full term of the agreement.\n"
	"- Payment follows each accepted milestone without further notice.\n\n"
	"[Page 2]\n\n"
	"Any dispute is referred to arbitration under the rules stated in annex B.\n"
)


class TranslationTestCase(AIPlatformTestCase):
	def make_translation(self, document, target="ar", **kwargs):
		from ai_fr_hg.ai.translation import create_translation

		name = create_translation(document.name, target, **kwargs)
		return frappe.get_doc("AI Translation", name)


class TestDocumentTranslation(TranslationTestCase):
	def test_worker_authority_is_restored_after_failure(self):
		from ai_fr_hg.ai.translation import _translation_user

		worker_frappe = MagicMock()
		worker_frappe.session.user = "background-worker@example.com"
		worker_frappe.db.get_value.return_value = 1
		worker_frappe.set_user.side_effect = lambda user: setattr(worker_frappe.session, "user", user)

		with (
			patch("ai_fr_hg.ai.translation.frappe", worker_frappe),
			self.assertRaisesRegex(RuntimeError, "provider failed"),
		):
			with _translation_user("requester@example.com"):
				self.assertEqual(worker_frappe.session.user, "requester@example.com")
				raise RuntimeError("provider failed")

		self.assertEqual(worker_frappe.session.user, "background-worker@example.com")
		self.assertEqual(
			worker_frappe.set_user.call_args_list,
			[call("requester@example.com"), call("background-worker@example.com")],
		)

	def test_translation_preserves_document_structure(self):
		from ai_fr_hg.ai.translation import run_translation

		document = self.make_document("Maintenance Agreement", STRUCTURED_SOURCE)
		translation = self.make_translation(document, "ar")

		with stub_translation_model():
			run_translation(translation.name)

		translation.reload()
		self.assertEqual(translation.status, "Completed")
		self.assertEqual(translation.source_language, "en")
		self.assertEqual(translation.target_language, "ar")
		self.assertEqual(translation.direction, "rtl")
		self.assertGreaterEqual(translation.quality_score, 90)
		self.assertEqual(translation.flagged_segments, 0)

		output = translation.translated_text
		# Structure is a contract: markers, headings and list bullets survive.
		self.assertIn("[Page 1]", output)
		self.assertIn("[Page 2]", output)
		self.assertTrue(output.lstrip().startswith("#"))
		self.assertEqual(output.count("\n- "), 2)

		kinds = {row.kind for row in translation.segments}
		self.assertTrue({"heading", "marker", "paragraph", "list"} <= kinds)
		marker = next(row for row in translation.segments if row.kind == "marker")
		self.assertEqual(marker.status, "Copied")
		self.assertEqual(marker.translated_text.strip(), "[Page 1]")

	def test_numbers_and_dates_are_never_rewritten_by_the_model(self):
		from ai_fr_hg.ai.translation import run_translation

		document = self.make_document("Figures", STRUCTURED_SOURCE)
		translation = self.make_translation(document, "he")

		with stub_translation_model():
			run_translation(translation.name)

		translation.reload()
		self.assertIn("1,250,000", translation.translated_text)
		self.assertIn("2026-03-15", translation.translated_text)

	def test_every_segment_is_stored_for_review(self):
		from ai_fr_hg.ai.translation import run_translation

		document = self.make_document("Reviewable", STRUCTURED_SOURCE)
		translation = self.make_translation(document, "ar")

		with stub_translation_model():
			run_translation(translation.name)

		translation.reload()
		self.assertEqual(translation.segment_count, len(translation.segments))
		indexes = [row.segment_index for row in translation.segments]
		self.assertEqual(indexes, sorted(indexes))
		for row in translation.segments:
			self.assertTrue(row.source_text)
			self.assertTrue(row.translated_text)
			self.assertTrue(row.fingerprint)


class TestQualityGate(TranslationTestCase):
	def test_an_untranslated_echo_is_flagged(self):
		from ai_fr_hg.ai.translation import run_translation

		def echo(system, user, target):
			import re

			segments = re.findall(r"<<<SEG (\d+)>>>\n(.*?)(?=\n<<<SEG |\Z)", user, re.DOTALL)
			if segments:
				return "\n".join(f"<<<SEG {i}>>>\n{body.strip()}" for i, body in segments)
			match = re.search(r"<TEXT>\n(.*)\n</TEXT>", user, re.DOTALL)
			return (match.group(1) if match else user).strip()

		document = self.make_document("Echoed", STRUCTURED_SOURCE)
		translation = self.make_translation(document, "ar")

		with stub_translation_model(behaviour=echo):
			run_translation(translation.name)

		translation.reload()
		self.assertEqual(translation.status, "Needs Review")
		self.assertGreater(translation.flagged_segments, 0)
		flagged = next(row for row in translation.segments if row.status == "Flagged")
		self.assertTrue(flagged.issues)

	def test_a_refusal_is_flagged_rather_than_stored_as_a_translation(self):
		from ai_fr_hg.ai.translation import run_translation

		document = self.make_document("Refused", "The contractor shall deliver the works on time.")
		translation = self.make_translation(document, "he")

		with stub_translation_model(
			behaviour=lambda system, user, target: "I'm sorry, as an AI I cannot translate this."
		):
			run_translation(translation.name)

		translation.reload()
		self.assertEqual(translation.status, "Needs Review")
		self.assertGreater(translation.flagged_segments, 0)

	def test_a_flagged_segment_is_repaired_on_the_second_attempt(self):
		from ai_fr_hg.ai.translation import run_translation

		state = {"attempts": 0}

		def broken_then_fixed(system, user, target):
			from ai_fr_hg.tests.integration_test_case import _default_translation_reply

			state["attempts"] += 1
			if state["attempts"] == 1:
				return "   "  # an empty first answer must not be accepted
			return _default_translation_reply(system, user, target)

		document = self.make_document("Repairable", "The contractor shall deliver the works on time.")
		translation = self.make_translation(document, "ar")

		with stub_translation_model(behaviour=broken_then_fixed) as mock:
			run_translation(translation.name)

		translation.reload()
		self.assertGreaterEqual(mock.call_count, 2)
		self.assertEqual(translation.status, "Completed")
		self.assertEqual(translation.flagged_segments, 0)

	def test_a_repair_that_scores_worse_is_discarded(self):
		"""A retry may only replace the first attempt when it is actually better."""
		from ai_fr_hg.ai.translation import run_translation

		# The source must be unique across the whole suite: an identical clause
		# translated earlier on the same knowledge base is served straight from
		# translation memory and the model is never called at all.
		source = "The supplier shall invoice the client after each delivery."
		state = {"attempts": 0}

		def flagged_then_empty(system, user, target):
			state["attempts"] += 1
			# First: an untranslated echo (flagged, but real text).
			# Second: nothing at all, which must not be kept.
			return source if state["attempts"] == 1 else "   "

		document = self.make_document("Not Worse", source)
		translation = self.make_translation(document, "ar")

		with stub_translation_model(behaviour=flagged_then_empty) as mock:
			run_translation(translation.name)

		translation.reload()
		self.assertEqual(mock.call_count, 2)
		self.assertIn("supplier", translation.translated_text)
		self.assertEqual(translation.status, "Needs Review")


class TestTranslationMemory(TranslationTestCase):
	def test_identical_segments_are_reused_across_documents(self):
		from ai_fr_hg.ai.translation import run_translation

		clause = (
			"The contractor shall deliver the works before the agreed completion date.\n\n"
			"Payment follows each accepted milestone without further notice or delay.\n"
		)
		first = self.make_translation(self.make_document("Contract A", clause), "ar")
		with stub_translation_model():
			run_translation(first.name)
		first.reload()
		self.assertEqual(first.memory_hits, 0)

		second = self.make_translation(self.make_document("Contract B", clause), "ar")
		with stub_translation_model() as mock:
			run_translation(second.name)

		second.reload()
		self.assertGreater(second.memory_hits, 0)
		self.assertEqual(mock.call_count, 0)
		self.assertEqual(second.translated_text, first.translated_text)
		self.assertTrue(any(row.reused for row in second.segments))

	def test_memory_is_scoped_to_the_language_pair(self):
		from ai_fr_hg.ai.translation import run_translation

		clause = "The contractor shall deliver the works before the agreed completion date.\n"
		arabic = self.make_translation(self.make_document("Pairing A", clause), "ar")
		with stub_translation_model():
			run_translation(arabic.name)

		hebrew = self.make_translation(self.make_document("Pairing B", clause), "he")
		with stub_translation_model() as mock:
			run_translation(hebrew.name)

		hebrew.reload()
		self.assertEqual(hebrew.memory_hits, 0)
		self.assertGreater(mock.call_count, 0)

	def test_memory_is_not_used_without_an_authorized_knowledge_base(self):
		from ai_fr_hg.ai.translation import translate_text

		clause = "Unscoped memory must never read every translation corpus.\n"
		stored = self.make_translation(self.make_document("Scoped Store", clause), "ar")
		with stub_translation_model():
			from ai_fr_hg.ai.translation import run_translation

			run_translation(stored.name)

		with stub_translation_model() as mock:
			outcome = translate_text(clause, "ar")

		self.assertEqual(outcome.memory_hits, 0)
		self.assertGreater(mock.call_count, 0)

	def test_memory_is_isolated_across_knowledge_bases(self):
		from ai_fr_hg.ai.translation import run_translation

		other = self.ensure_other_knowledge_base()
		clause = "Cross knowledge base memory reuse is a disclosure.\n"
		first = self.make_translation(self.make_document("KB One", clause), "ar")
		with stub_translation_model():
			run_translation(first.name)

		foreign = self.make_document("KB Two", clause)
		foreign.db_set("knowledge_base", other.name)
		second = self.make_translation(foreign, "ar")
		with stub_translation_model() as mock:
			run_translation(second.name)

		second.reload()
		self.assertEqual(second.memory_hits, 0)
		self.assertGreater(mock.call_count, 0)

	def test_memory_is_not_reused_under_a_different_policy(self):
		from ai_fr_hg.ai.translation import run_translation

		clause = "Policy identity must be part of translation memory.\n"
		first = self.make_translation(self.make_document("Policy A", clause), "ar", tone="Legal")
		with stub_translation_model():
			run_translation(first.name)

		second = self.make_translation(self.make_document("Policy B", clause), "ar", tone="Neutral")
		with stub_translation_model() as mock:
			run_translation(second.name)

		second.reload()
		self.assertEqual(second.memory_hits, 0)
		self.assertGreater(mock.call_count, 0)

	def test_document_tool_uses_only_the_source_document_knowledge_base(self):
		from ai_fr_hg.ai.tools.builtin import translate_content

		other = self.ensure_other_knowledge_base()
		clause = "The tool must not query another knowledge base for memory.\n"
		seed = self.make_document("Tool Seed", clause)
		seed.db_set("knowledge_base", other.name)
		stored = self.make_translation(seed, "ar")
		with stub_translation_model():
			from ai_fr_hg.ai.translation import run_translation

			run_translation(stored.name)

		document = self.make_document("Tool Target", clause)
		with stub_translation_model() as mock:
			result = translate_content(target_language="ar", document=document.name)

		self.assertTrue(result["translated"])
		self.assertGreater(mock.call_count, 0)


class TestGlossary(TranslationTestCase):
	def ensure_glossary(self):
		if frappe.db.exists("AI Translation Glossary", "Test Glossary"):
			return frappe.get_doc("AI Translation Glossary", "Test Glossary")
		doc = frappe.get_doc(
			{
				"doctype": "AI Translation Glossary",
				"glossary_name": "Test Glossary",
				"enabled": 1,
				"terms": [
					{"term_en": "Acme Corp", "do_not_translate": 1},
					{"term_en": "Contractor", "term_ar": "المقاول", "term_he": "הקבלן"},
				],
			}
		)
		doc.insert(ignore_permissions=True)
		return doc

	def test_protected_terms_survive_translation(self):
		from ai_fr_hg.ai.translation import run_translation

		glossary = self.ensure_glossary()
		document = self.make_document(
			"Glossary Document", "Acme Corp and the Contractor agreed the maintenance schedule.\n"
		)
		translation = self.make_translation(document, "ar", glossary=glossary.name)

		with stub_translation_model() as mock:
			run_translation(translation.name)

		translation.reload()
		self.assertIn("Acme Corp", translation.translated_text)
		# The required rendering of a mapped term is put in front of the model.
		self.assertIn("المقاول", mock.calls[0]["system"])

	def test_a_glossary_needs_two_languages_or_a_keep_as_is_flag(self):
		doc = frappe.get_doc(
			{
				"doctype": "AI Translation Glossary",
				"glossary_name": "Incomplete Glossary",
				"terms": [{"term_en": "Contractor"}],
			}
		)
		self.assertRaises(frappe.ValidationError, doc.insert)


class TestTranslationRecord(TranslationTestCase):
	def test_source_and_target_must_differ(self):
		document = self.make_document("Same Language", "This document is already in English.\n")
		self.assertRaises(
			frappe.ValidationError,
			frappe.get_doc(
				{
					"doctype": "AI Translation",
					"title": "Invalid",
					"source_document": document.name,
					"source_language": "en",
					"target_language": "en",
				}
			).insert,
		)

	def test_an_unsupported_language_is_rejected(self):
		from ai_fr_hg.ai.translation import create_translation

		document = self.make_document("Unsupported", "Content that will not be translated.\n")
		self.assertRaises(frappe.ValidationError, create_translation, document.name, "fr")

	def test_a_document_without_text_cannot_be_translated(self):
		from ai_fr_hg.ai.translation import create_translation

		# A text document cannot be *created* without content, so the state this
		# guard protects is the post-extraction one (an empty file read, or text
		# cleared later). Simulate that state directly.
		document = self.make_document("Empty", "Original text.\n")
		document.db_set("content", "")
		self.assertRaises(frappe.ValidationError, create_translation, document.name, "ar")

	def test_a_single_segment_can_be_retranslated_with_an_instruction(self):
		from ai_fr_hg.ai.translation import retranslate_segment, run_translation

		document = self.make_document("Retranslate", STRUCTURED_SOURCE)
		translation = self.make_translation(document, "ar")
		with stub_translation_model():
			run_translation(translation.name)

		translation.reload()
		target = next(row for row in translation.segments if row.kind == "paragraph")
		with stub_translation_model() as mock:
			result = retranslate_segment(
				translation.name, target.segment_index, instructions="Keep the clause numbering."
			)

		self.assertEqual(mock.call_count, 1)
		self.assertIn("Keep the clause numbering.", mock.calls[0]["system"])
		self.assertTrue(result["translated_text"])

		translation.reload()
		self.assertIn("[Page 1]", translation.translated_text)

	def test_marking_reviewed_clears_the_flags(self):
		from ai_fr_hg.ai.translation import run_translation

		document = self.make_document("Reviewed", "The contractor shall deliver the works on time.")
		translation = self.make_translation(document, "ar")
		with stub_translation_model(behaviour=lambda system, user, target: "I'm sorry, as an AI I cannot."):
			run_translation(translation.name)

		translation.reload()
		self.assertEqual(translation.status, "Needs Review")
		translation.mark_reviewed()
		translation.reload()
		self.assertEqual(translation.status, "Completed")
		self.assertEqual(translation.flagged_segments, 0)

	def test_the_translation_can_be_indexed_as_its_own_document(self):
		from ai_fr_hg.ai.translation import index_translation, run_translation

		document = self.make_document("Indexable", STRUCTURED_SOURCE)
		translation = self.make_translation(document, "he")
		with stub_translation_model():
			run_translation(translation.name)

		created = self.make_document("Placeholder Translated Copy", "content")
		with patch("ai_fr_hg.ai.ingestion.ingest_text", return_value=created.name) as ingest:
			indexed = index_translation(translation.name)

		self.assertEqual(indexed, created.name)
		self.assertEqual(ingest.call_args.kwargs["language"], "he")
		translation.reload()
		self.assertEqual(translation.translated_document, created.name)


class TestTranslationIntegrations(TranslationTestCase):
	def test_the_pipeline_step_translates_its_input(self):
		from ai_fr_hg.ai.pipeline import execute_step

		step = frappe._dict(
			step_type="Translate",
			step_name="Translate to Arabic",
			model=None,
			knowledge_base=None,
			input_field="content",
			output_field="translated",
			config=frappe.as_json({"target_language": "ar", "return": "text"}),
		)
		run_doc = frappe._dict(name="RUN-TEST", reference_doctype=None, reference_name=None)

		with stub_translation_model():
			output = execute_step(
				step,
				{"content": "The contractor shall deliver the works on time."},
				run_doc,
			)

		self.assertIsInstance(output, str)
		self.assertTrue(output.strip())

	def test_the_pipeline_step_requires_a_target_language(self):
		from ai_fr_hg.ai.exceptions import PipelineError
		from ai_fr_hg.ai.pipeline import execute_step

		step = frappe._dict(
			step_type="Translate",
			step_name="Broken",
			model=None,
			knowledge_base=None,
			input_field="content",
			output_field="translated",
			config="{}",
		)
		run_doc = frappe._dict(name="RUN-TEST", reference_doctype=None, reference_name=None)
		self.assertRaises(PipelineError, execute_step, step, {"content": "text"}, run_doc)

	def test_the_agent_tool_translates_a_passage(self):
		from ai_fr_hg.ai.tools.builtin import translate_content

		with stub_translation_model():
			result = translate_content(
				target_language="he", text="The contractor shall deliver the works on time."
			)

		self.assertTrue(result["translated"])
		self.assertEqual(result["target_language"], "he")
		self.assertEqual(result["source_language"], "en")
		self.assertTrue(result["text"].strip())

	def test_the_agent_tool_rejects_an_unsupported_language(self):
		from ai_fr_hg.ai.tools.builtin import translate_content

		result = translate_content(target_language="fr", text="Some text to translate.")
		self.assertFalse(result["translated"])
		self.assertIn("error", result)

	def test_the_api_reports_the_supported_pairs(self):
		from ai_fr_hg.api.translation import get_languages

		payload = get_languages()
		self.assertEqual({item["code"] for item in payload["languages"]}, {"ar", "en", "he"})
		self.assertEqual(len(payload["pairs"]), 6)

	def test_the_document_action_queues_a_translation(self):
		document = self.make_document("Actioned", STRUCTURED_SOURCE)

		with patch("ai_fr_hg.ai.translation.frappe.enqueue") as enqueue:
			result = document.translate("ar", background=True)

		self.assertTrue(enqueue.called)
		self.assertEqual(result["status"], "Queued")
		translation = frappe.get_doc("AI Translation", result["translation"])
		self.assertEqual(translation.target_language, "ar")
		self.assertEqual(translation.source_document, document.name)


class TestPseudoTranslator(TranslationTestCase):
	"""The stub itself must be trustworthy, or the tests above prove nothing."""

	def test_pseudo_translation_changes_script_but_keeps_placeholders(self):
		output = pseudo_translate("Pay [[T0]] before [[T1]] please", "ar")
		self.assertIn("[[T0]]", output)
		self.assertIn("[[T1]]", output)
		self.assertNotIn("Pay", output)
		self.assertNotIn("please", output)
