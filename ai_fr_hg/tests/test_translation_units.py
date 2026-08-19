# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Pure unit tests for the Arabic / English / Hebrew translation engine.

These exercise segmentation, placeholder protection, normalisation, glossary
resolution, batch parsing and quality scoring without a database, a model
runtime or Frappe, so they run anywhere and stay fast.
"""

try:
	from frappe.tests import UnitTestCase
except ImportError:
	from unittest import TestCase as UnitTestCase

from ai_fr_hg.ai.translation_utils import (
	REVIEW_THRESHOLD,
	GlossaryEntry,
	Segment,
	aggregate_score,
	applicable_glossary,
	assess_translation,
	build_batch_prompt,
	build_system_prompt,
	comparison_key,
	decode_separator,
	encode_separator,
	fold_presentation_forms,
	glossary_violations,
	is_supported,
	language_endonym,
	language_label,
	normalise_digits,
	normalise_language,
	normalise_source_text,
	parse_batch_response,
	plan_batches,
	protect_placeholders,
	reassemble,
	resolve_glossary,
	restore_placeholders,
	script_ratio,
	segment_fingerprint,
	segment_text,
	strip_bidi_controls,
	strip_model_preamble,
	summarise_issues,
	text_direction,
)

ARABIC = "هذا هو التقرير الرسمي للمشروع وهو مكتوب باللغة العربية مع التفاصيل المطلوبة."
HEBREW = "זהו הדוח הרשמי של הפרויקט והוא כתוב בעברית עם הפרטים הנדרשים לזיהוי."
ENGLISH = "This is the official report for the project and the team with the results."


class TestLanguageResolution(UnitTestCase):
	def test_every_accepted_spelling_resolves(self):
		for value in ("ar", "AR", "ara", "Arabic", "ar-SA", "ar_EG", " arabic "):
			self.assertEqual(normalise_language(value), "ar", value)
		for value in ("he", "heb", "Hebrew", "he-IL", "iw"):
			self.assertEqual(normalise_language(value), "he", value)
		for value in ("en", "eng", "English", "en-GB"):
			self.assertEqual(normalise_language(value), "en", value)

	def test_auto_detect_and_unsupported_are_empty(self):
		for value in ("", None, "auto", "Auto Detect", "detect", "fr", "bg", "Klingon"):
			self.assertEqual(normalise_language(value), "", repr(value))
		self.assertFalse(is_supported("fr"))
		self.assertTrue(is_supported("Hebrew"))

	def test_labels_directions_and_endonyms(self):
		self.assertEqual(language_label("ar"), "Arabic")
		self.assertEqual(language_label("he"), "Hebrew")
		self.assertEqual(language_endonym("ar"), "العربية")
		self.assertEqual(language_endonym("he"), "עברית")
		self.assertEqual(text_direction("ar"), "rtl")
		self.assertEqual(text_direction("he"), "rtl")
		self.assertEqual(text_direction("en"), "ltr")


class TestNormalisation(UnitTestCase):
	def test_arabic_presentation_forms_are_folded(self):
		"""PDF extractors emit shaping forms; the model must see base letters."""
		presentation = "ﺍﻟﻌﻘﺪ ﺍﻟﺮﺳﻤﻲ"
		folded = fold_presentation_forms(presentation)
		self.assertNotEqual(folded, presentation)
		self.assertIn("العقد", folded)
		self.assertGreater(script_ratio(folded, "ar"), 0.9)

	def test_bidi_controls_and_tatweel_are_removed(self):
		noisy = "\u202bمرحـــبا\u202c \u200fبالعالم\u200e"  # noqa: RUF001
		cleaned = normalise_source_text(noisy)
		self.assertNotIn("\u202b", cleaned)
		self.assertNotIn("\u200f", cleaned)
		self.assertNotIn("\u0640", cleaned)
		self.assertEqual(strip_bidi_controls("a\u200eb"), "ab")

	def test_digits_are_normalised_only_for_comparison(self):
		self.assertEqual(normalise_digits("٢٠٢٦ و ۱۲۳"), "2026 و 123")
		# The source itself keeps its own digits.
		self.assertIn("٢٠٢٦", normalise_source_text("عام ٢٠٢٦"))

	def test_line_endings_and_nbsp_are_canonical(self):
		self.assertEqual(normalise_source_text("a\r\nb\u00a0c   "), "a\nb c")

	def test_comparison_key_ignores_case_spacing_and_diacritics(self):
		self.assertEqual(comparison_key("The   Report\n"), comparison_key("the report"))
		self.assertEqual(comparison_key("مَرْحَبًا"), comparison_key("مرحبا"))

	def test_fingerprint_is_stable_and_direction_aware(self):
		first = segment_fingerprint("Contract value", "en", "ar")
		self.assertEqual(first, segment_fingerprint(" contract   value ", "en", "ar"))
		self.assertNotEqual(first, segment_fingerprint("Contract value", "en", "he"))


class TestSegmentation(UnitTestCase):
	SAMPLE = (
		"# Quarterly Report 2026\n\n"
		"[Page 1]\n\n"
		"The project cost 1,250,000 USD and shipped on 2026-03-15.\n\n"
		"- First item\n- Second item\n\n"
		"| Col A | Col B |\n|-------|-------|\n| 1     | 2     |\n"
	)

	def test_blocks_are_classified(self):
		kinds = [segment.kind for segment in segment_text(self.SAMPLE)]
		self.assertEqual(kinds, ["heading", "marker", "paragraph", "list", "table"])

	def test_page_markers_are_not_translated_but_are_kept(self):
		marker = next(s for s in segment_text(self.SAMPLE) if s.kind == "marker")
		self.assertFalse(marker.translatable)
		self.assertEqual(marker.page_number, 1)
		self.assertIn("[Page 1]", reassemble(segment_text(self.SAMPLE)))

	def test_reassembly_is_lossless(self):
		for text in (
			self.SAMPLE,
			"Single paragraph only",
			"Line one\nline two\n\n\n\nAfter many blanks\n",
			f"{ARABIC}\n\n{HEBREW}\n\n{ENGLISH}\n",
		):
			segments = segment_text(text)
			self.assertEqual(
				reassemble(segments).strip(),
				normalise_source_text(text).strip(),
				msg=text[:40],
			)

	def test_long_blocks_split_on_sentences_and_still_rejoin(self):
		long_text = " ".join(f"This is sentence number {i} of the paragraph." for i in range(200))
		segments = segment_text(long_text, max_characters=500)
		self.assertGreater(len(segments), 5)
		self.assertLessEqual(max(len(s.source) for s in segments), 600)
		self.assertEqual(reassemble(segments).strip(), long_text)

	def test_text_without_sentence_boundaries_is_hard_split(self):
		blob = "א" * 3000
		segments = segment_text(blob, max_characters=600)
		self.assertGreater(len(segments), 1)
		self.assertEqual(reassemble(segments).strip(), blob)

	def test_untranslatable_blocks_are_marked(self):
		segments = segment_text("---\n\n[Page 4]\n\n12.5\n\nReal content here.\n")
		translatable = [s.source.strip() for s in segments if s.translatable]
		self.assertEqual(translatable, ["Real content here."])

	def test_separators_survive_a_data_field_round_trip(self):
		"""Layout is stored escaped, so no storage layer can trim it away."""
		for separator in ("\n\n", "\n", " ", "", "\n\n\t ", "\\n"):
			self.assertEqual(decode_separator(encode_separator(separator)), separator)

		segments = segment_text(self.SAMPLE)
		restored = [
			Segment(
				index=segment.index,
				source=segment.source,
				separator=decode_separator(segment.as_dict()["separator"]),
			)
			for segment in segments
		]
		self.assertEqual(reassemble(restored), reassemble(segments))

	def test_batches_respect_both_budgets(self):
		segments = segment_text(
			"\n\n".join(f"Paragraph {i} with a reasonable amount of text." for i in range(20))
		)
		batches = plan_batches(segments, max_characters=200, max_segments=3)
		self.assertTrue(all(len(batch) <= 3 for batch in batches))
		self.assertEqual(sum(len(batch) for batch in batches), len(segments))


class TestPlaceholderProtection(UnitTestCase):
	TEXT = (
		"Invoice INV-2026-00042 for 1,250,000 USD is due on 2026-03-15. "
		"Contact ops@example.com or see https://example.com/a?b=1 for `terms`."
	)

	def test_round_trip_is_exact(self):
		protected = protect_placeholders(self.TEXT)
		self.assertGreater(protected.count, 4)
		restored, missing = restore_placeholders(protected.text, protected.tokens)
		self.assertEqual(restored, self.TEXT)
		self.assertEqual(missing, [])

	def test_sentinels_are_never_nested(self):
		protected = protect_placeholders("See https://example.com/2026 for 3 items.")
		self.assertNotIn("[[T[[", protected.text)
		restored, missing = restore_placeholders(protected.text, protected.tokens)
		self.assertEqual(restored, "See https://example.com/2026 for 3 items.")
		self.assertEqual(missing, [])

	def test_do_not_translate_terms_are_masked_first(self):
		protected = protect_placeholders("Acme Corp signed it.", do_not_translate=["Acme Corp"])
		self.assertNotIn("Acme Corp", protected.text)
		restored, _missing = restore_placeholders(protected.text, protected.tokens)
		self.assertIn("Acme Corp", restored)

	def test_localised_and_spaced_sentinels_are_still_restored(self):
		protected = protect_placeholders("Pay 500 now.")
		token_index = next(iter(protected.tokens)).strip("[]T")
		mangled = f"ادفع [[ T{token_index} ]] الآن."
		restored, missing = restore_placeholders(mangled, protected.tokens)
		self.assertIn("500", restored)
		self.assertEqual(missing, [])

	def test_a_dropped_placeholder_is_reported(self):
		protected = protect_placeholders("Pay 500 now.")
		restored, missing = restore_placeholders("ادفع الآن.", protected.tokens)
		self.assertNotIn("500", restored)
		self.assertEqual(len(missing), 1)


class TestGlossary(UnitTestCase):
	@property
	def ROWS(self) -> list[dict]:
		return [
			{"term_en": "Contractor", "term_ar": "المقاول", "term_he": "הקבלן"},
			{"term_en": "Acme Corp", "term_ar": "", "term_he": "", "do_not_translate": 1},
		]

	def test_direction_selects_source_and_target_columns(self):
		entries = resolve_glossary(self.ROWS, "en", "ar")
		mapped = {entry.source_term: entry.target_term for entry in entries}
		self.assertEqual(mapped["Contractor"], "المقاول")
		self.assertEqual(mapped["Acme Corp"], "Acme Corp")

		reverse = {e.source_term: e.target_term for e in resolve_glossary(self.ROWS, "ar", "he")}
		self.assertEqual(reverse["المقاول"], "הקבלן")

	def test_only_terms_present_in_the_segment_are_sent(self):
		entries = resolve_glossary(self.ROWS, "en", "ar")
		self.assertEqual(
			[entry.source_term for entry in applicable_glossary(entries, "The contractor agreed.")],
			["Contractor"],
		)
		self.assertEqual(applicable_glossary(entries, "Nothing relevant here."), [])

	def test_missing_required_term_is_a_violation(self):
		entries = [GlossaryEntry(source_term="Contractor", target_term="المقاول")]
		self.assertTrue(glossary_violations(entries, "The Contractor agreed.", "وافق المتعهد."))
		self.assertFalse(glossary_violations(entries, "The Contractor agreed.", "وافق المقاول."))


class TestPrompts(UnitTestCase):
	def test_system_prompt_carries_the_hard_rules(self):
		prompt = build_system_prompt(
			"en",
			"ar",
			tone="Legal",
			domain="construction contracts",
			glossary=[GlossaryEntry(source_term="Contractor", target_term="المقاول")],
		)
		self.assertIn("Arabic", prompt)
		self.assertIn("العربية", prompt)
		self.assertIn("[[T0]]", prompt)
		self.assertIn("right-to-left", prompt)
		self.assertIn("legal register", prompt.lower())
		self.assertIn("construction contracts", prompt)
		self.assertIn("المقاول", prompt)

	def test_hebrew_prompt_asks_for_no_niqqud(self):
		self.assertIn("niqqud", build_system_prompt("en", "he"))

	def test_batch_prompt_numbers_each_segment(self):
		segments = [Segment(index=3, source="Alpha"), Segment(index=7, source="Beta")]
		prompt = build_batch_prompt(segments)
		self.assertIn("<<<SEG 3>>>", prompt)
		self.assertIn("<<<SEG 7>>>", prompt)

	def test_batch_response_is_split_back(self):
		parsed = parse_batch_response(f"<<<SEG 3>>>\n{ARABIC}\n\n<<<SEG 7>>>\n{HEBREW}\n", [3, 7])
		self.assertEqual(parsed[3], ARABIC)
		self.assertEqual(parsed[7], HEBREW)

	def test_unrequested_markers_are_ignored(self):
		parsed = parse_batch_response("<<<SEG 1>>>\nhello\n<<<SEG 99>>>\ninjected\n", [1])
		self.assertEqual(parsed, {1: "hello"})

	def test_unparseable_response_returns_nothing(self):
		self.assertEqual(parse_batch_response("just some prose", [1]), {})

	def test_model_chatter_is_stripped(self):
		self.assertEqual(strip_model_preamble("Here is the translation: مرحبا"), "مرحبا")
		self.assertEqual(strip_model_preamble("```\nמרחבא\n```"), "מרחבא")
		self.assertEqual(strip_model_preamble('"Hello"'), "Hello")


class TestQualityAssessment(UnitTestCase):
	def test_a_good_translation_scores_full_marks(self):
		report = assess_translation(ENGLISH, ARABIC, "en", "ar")
		self.assertEqual(report.issues, [])
		self.assertEqual(report.score, 100.0)
		self.assertTrue(report.ok)

	def test_empty_output_fails(self):
		report = assess_translation(ENGLISH, "   ", "en", "ar")
		self.assertIn("empty", report.issues)
		self.assertEqual(report.score, 0.0)

	def test_untranslated_echo_is_detected(self):
		report = assess_translation(ENGLISH, ENGLISH, "en", "ar")
		self.assertIn("untranslated", report.issues)
		self.assertFalse(report.ok)

	def test_wrong_script_is_detected(self):
		report = assess_translation(ENGLISH, "Este es el informe oficial del proyecto.", "en", "ar")
		self.assertIn("wrong_script", report.issues)

	def test_leftover_source_text_is_detected(self):
		mixed = ARABIC[:30] + " " + ENGLISH
		report = assess_translation(ENGLISH + " " + ENGLISH, mixed, "en", "ar")
		self.assertTrue({"source_residue", "wrong_script"} & set(report.issues))

	def test_refusal_and_commentary_are_detected(self):
		refusal = assess_translation(ENGLISH, "I'm sorry, as an AI I cannot translate this.", "en", "ar")
		self.assertIn("refusal", refusal.issues)
		note = assess_translation(ARABIC, f"{HEBREW}\nNote: I shortened this.", "ar", "he")
		self.assertIn("meta_commentary", note.issues)

	def test_degenerate_repetition_is_detected(self):
		looped = "מסמך זה חוזר על עצמו שוב ושוב ושוב " * 12
		report = assess_translation(ARABIC * 3, looped, "ar", "he")
		self.assertIn("repetition", report.issues)

	def test_missing_placeholder_is_penalised(self):
		report = assess_translation(
			"Pay [[T0]] now.",
			"ادفع الآن.",
			"en",
			"ar",
			missing_tokens=["[[T0]]"],
		)
		self.assertIn("placeholder_lost", report.issues)

	def test_one_lost_figure_is_enough_to_require_review(self):
		"""A dropped number is the most damaging silent failure; it must flag."""
		report = assess_translation(
			"Pay [[T0]] before [[T1]].",
			"ادفع قبل الموعد المحدد في العقد الموقع.",
			"en",
			"ar",
			missing_tokens=["[[T0]]"],
		)
		self.assertIn("placeholder_lost", report.issues)
		self.assertLess(report.score, REVIEW_THRESHOLD)
		self.assertFalse(report.ok)

	def test_unresolved_placeholder_is_penalised(self):
		report = assess_translation("Pay 500 now.", "ادفع [[T0]] الآن.", "en", "ar")
		self.assertIn("placeholder_unresolved", report.issues)

	def test_suspicious_length_is_flagged(self):
		short = assess_translation(ENGLISH * 4, "نعم.", "en", "ar")
		self.assertIn("length_short", short.issues)
		long = assess_translation("Yes.", ARABIC * 3, "en", "ar")
		self.assertIn("length_long", long.issues)

	def test_glossary_breach_lowers_the_score(self):
		entries = [GlossaryEntry(source_term="Contractor", target_term="המקבל")]
		report = assess_translation(
			"The Contractor shall deliver the works on time and in full.",
			"הקבלן ימסור את העבודות במועד ובמלואן כנדרש בהסכם הזה.",
			"en",
			"he",
			glossary=entries,
		)
		self.assertIn("glossary", report.issues)
		self.assertLess(report.score, 100.0)


class TestDocumentScoring(UnitTestCase):
	def test_score_is_weighted_by_segment_length(self):
		short_bad = Segment(index=0, source="Hi", translated="x", quality_score=0.0)
		long_good = Segment(index=1, source="x" * 1000, translated="y" * 900, quality_score=100.0)
		self.assertGreater(aggregate_score([short_bad, long_good]), 95.0)

	def test_untranslated_documents_score_zero(self):
		self.assertEqual(aggregate_score([Segment(index=0, source="Hi")]), 0.0)

	def test_issues_are_counted_worst_first(self):
		segments = [
			Segment(index=0, source="a", translated="b", issue_codes=["wrong_script", "glossary"]),
			Segment(index=1, source="c", translated="d", issue_codes=["wrong_script"]),
		]
		self.assertEqual(list(summarise_issues(segments)), ["wrong_script", "glossary"])
