# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Pure unit tests for the high-precision pattern extraction layer.

These exercise the ported tokenizer, canonicalization and provenance quoting
without touching the database, mirroring the File Analysis reference
semantics: deterministic, bounded, and immune to pathological inputs.
"""

import time

try:
	from frappe.tests import UnitTestCase
except ImportError:
	from unittest import TestCase as UnitTestCase

from ai_fr_hg.ai.patterns import (
	MAX_SCAN_CHARS,
	canonicalize_pattern_value,
	extract_pattern_entities,
	persistable_pattern_type,
)


def _by_type(entities, entity_type):
	return [entity for entity in entities if entity["entity_type"] == entity_type]


class TestPatternExtraction(UnitTestCase):
	def test_extracts_each_high_precision_type(self):
		text = (
			"Contact john.doe@example.com or visit https://example.com/docs.\n"
			"Legacy link: www.example.org/page. Phone: +359 88 123 4567.\n"
			"Server 192.168.10.42 is flagged. File hash 5d41402abc4b2a76b9719d911017c592.\n"
			"Signed on 2024-03-05, approved 25/12/2024, due May 8, 2025.\n"
			"References INV-2024-0817 and CONTRACT/ACME/0042.\n"
			"Total $1,234.50 plus 250.00 EUR outstanding."
		)
		entities = extract_pattern_entities(text)
		types = {entity["entity_type"] for entity in entities}

		self.assertIn("email", types)
		self.assertIn("url", types)
		self.assertIn("phone", types)
		self.assertIn("ip", types)
		self.assertIn("hash", types)
		self.assertIn("date", types)
		self.assertIn("identifier", types)
		self.assertIn("money", types)

		self.assertEqual(_by_type(entities, "email")[0]["value"], "john.doe@example.com")
		# Both URL spellings are captured.
		self.assertEqual({entity["value"] for entity in _by_type(entities, "url")}, {
			"https://example.com/docs",
			"www.example.org/page",
		})
		self.assertEqual(_by_type(entities, "ip")[0]["value"], "192.168.10.42")
		self.assertEqual(_by_type(entities, "identifier")[0]["normalized_value"], "inv-2024-0817")

	def test_surface_variants_merge_under_canonical_identity(self):
		entities = extract_pattern_entities("Mail John@Example.COM now; again john@example.com.")
		emails = _by_type(entities, "email")
		self.assertEqual(len(emails), 1)
		self.assertEqual(emails[0]["occurrences"], 2)
		self.assertEqual(emails[0]["value"], "John@Example.COM")
		self.assertEqual(emails[0]["normalized_value"], "john@example.com")

	def test_trailing_punctuation_is_trimmed(self):
		entities = extract_pattern_entities("See www.example.com. Then (see https://x.io/y).")
		urls = {entity["value"] for entity in _by_type(entities, "url")}
		self.assertIn("www.example.com", urls)
		self.assertIn("https://x.io/y", urls)

	def test_max_entities_cap_is_respected(self):
		text = " ".join(f"user{i}@example.com" for i in range(50))
		self.assertEqual(len(extract_pattern_entities(text, max_entities=10)), 10)

	def test_oversized_values_are_skipped(self):
		long_url = "https://example.com/" + "a" * 300
		self.assertEqual(extract_pattern_entities(f"See {long_url} now."), [])

	def test_empty_text_yields_nothing(self):
		self.assertEqual(extract_pattern_entities(""), [])
		self.assertEqual(extract_pattern_entities(None), [])

	def test_giant_text_is_sampled_head_and_tail(self):
		filler = "x" * (MAX_SCAN_CHARS + 100_000)
		text = "start mark INV-0001 here\n" + filler + "\nend mark PO-9001 there"
		entities = extract_pattern_entities(text)
		identifiers = {entity["value"].upper() for entity in _by_type(entities, "identifier")}
		self.assertEqual(identifiers, {"INV-0001", "PO-9001"})

	def test_provenance_quote_is_bounded_and_located(self):
		text = "Invoice INV-2024-0817 was issued to the department on file."
		entity = _by_type(extract_pattern_entities(text), "identifier")[0]
		self.assertEqual(text[entity["first_offset"] :].index("INV-2024-0817"), 0)
		self.assertLessEqual(len(entity["context_quote"]), 220)
		self.assertIn("Invoice", entity["context_quote"])

	def test_pathological_numeric_dump_stays_linear(self):
		# Long digit runs used to trigger catastrophic backtracking on greedy
		# phone patterns; the ported regexes must stay linear.
		started = time.monotonic()
		entities = extract_pattern_entities("9" * 300_000)
		elapsed = time.monotonic() - started
		self.assertLess(elapsed, 5.0)
		self.assertIsInstance(entities, list)


class TestCanonicalization(UnitTestCase):
	def test_dates_normalize_to_iso(self):
		self.assertEqual(canonicalize_pattern_value("date", "2024-03-05"), "2024-03-05")
		self.assertEqual(canonicalize_pattern_value("date", "25/12/2024"), "2024-12-25")
		self.assertEqual(canonicalize_pattern_value("date", "12/24/2024"), "2024-12-24")
		# Ambiguous dates keep the month-first reading, like the reference.
		self.assertEqual(canonicalize_pattern_value("date", "05/06/2024"), "2024-05-06")
		self.assertEqual(canonicalize_pattern_value("date", "1/2/68"), "2068-01-02")
		self.assertEqual(canonicalize_pattern_value("date", "1/2/70"), "1970-01-02")

	def test_money_strips_grouping_noise(self):
		self.assertEqual(canonicalize_pattern_value("money", "$1,234.50"), "$1234.50")
		self.assertEqual(canonicalize_pattern_value("money", "250.00 EUR"), "250.00eur")

	def test_identifiers_collapse_to_dashes(self):
		self.assertEqual(canonicalize_pattern_value("identifier", "PO_1234"), "po-1234")
		# Spaces are removed outright; only underscores become dashes.
		self.assertEqual(canonicalize_pattern_value("identifier", "INV 2024 0817"), "inv20240817")

	def test_other_types_casefold_and_collapse(self):
		self.assertEqual(canonicalize_pattern_value("email", "  John@X.IO "), "john@x.io")
		self.assertEqual(canonicalize_pattern_value("custom", "  Mixed   Case "), "mixed case")

	def test_unknown_types_land_in_custom(self):
		self.assertEqual(persistable_pattern_type("organization"), "custom")
		self.assertEqual(persistable_pattern_type("EMAIL"), "email")
		self.assertEqual(persistable_pattern_type(""), "custom")
		self.assertEqual(persistable_pattern_type(None), "custom")
