# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Pure unit tests for the AI platform's algorithms.

These exercise chunking, vector maths, redaction, ranking and JSON parsing
without touching the database or a model runtime, so they run fast and are
safe in any environment.
"""

import math
from itertools import pairwise
from unittest.mock import patch

try:
	from frappe.tests import UnitTestCase
except ImportError:
	from unittest import TestCase as UnitTestCase

from ai_fr_hg.ai import vector
from ai_fr_hg.ai.chunking import Chunk, chunk_text, estimate_tokens, split_sentences
from ai_fr_hg.install import before_tests


class TestTestBootstrap(UnitTestCase):
	@patch("ai_fr_hg.install.frappe")
	def test_before_tests_does_not_require_erpnext(self, frappe_mock):
		# Any access to frappe.db would fail, just as querying Company fails on a
		# standalone Frappe site where ERPNext is not installed.
		frappe_mock.db = object()

		before_tests()

		frappe_mock.clear_cache.assert_called_once_with()


class TestChunking(UnitTestCase):
	def test_short_text_is_one_chunk(self):
		chunks = chunk_text("A short paragraph that easily fits.", chunk_size=1000)
		self.assertEqual(len(chunks), 1)
		self.assertEqual(chunks[0].index, 0)
		self.assertIn("short paragraph", chunks[0].content)

	def test_empty_text_yields_no_chunks(self):
		self.assertEqual(chunk_text(""), [])
		self.assertEqual(chunk_text("   \n\n  "), [])

	def test_long_text_is_split(self):
		text = "\n\n".join(f"Paragraph number {i}. " * 20 for i in range(30))
		chunks = chunk_text(text, chunk_size=500, chunk_overlap=50)

		self.assertGreater(len(chunks), 1)
		for position, chunk in enumerate(chunks):
			self.assertEqual(chunk.index, position)
			self.assertTrue(chunk.content.strip())

	def test_chunks_respect_size_budget(self):
		text = "word " * 5000
		chunks = chunk_text(text, chunk_size=400, chunk_overlap=40)
		# A chunk may overshoot slightly to avoid splitting mid-sentence.
		for chunk in chunks:
			self.assertLessEqual(len(chunk.content), 400 * 2)

	def test_headings_are_captured(self):
		text = (
			"# Introduction\n\nThis is the opening section with some detail.\n\n"
			"## Background\n\nHistorical context follows here in the second section.\n\n"
			"## Method\n\nThe method is described in this third section of the document."
		)
		chunks = chunk_text(text, chunk_size=100, chunk_overlap=10)
		headings = {chunk.heading for chunk in chunks if chunk.heading}
		self.assertTrue(headings)

	def test_overlap_preserves_continuity(self):
		"""Consecutive chunks must share text so no sentence is orphaned."""
		text = " ".join(f"sentence{i} is here." for i in range(400))
		with_overlap = chunk_text(text, chunk_size=500, chunk_overlap=150)
		without_overlap = chunk_text(text, chunk_size=500, chunk_overlap=0)

		self.assertGreater(len(with_overlap), 2)
		# Overlapping windows duplicate content, so they cover more characters.
		self.assertGreater(
			sum(len(chunk.content) for chunk in with_overlap),
			sum(len(chunk.content) for chunk in without_overlap),
		)

		# At least one adjacent pair should literally share a trailing fragment.
		shared = 0
		for previous, following in pairwise(with_overlap):
			tail = previous.content[-60:]
			if any(word and word in following.content for word in tail.split()[:3]):
				shared += 1
		self.assertGreater(shared, 0)

	def test_chunk_metadata(self):
		chunk = Chunk(content="Hello world", index=0)
		self.assertEqual(chunk.character_count, 11)
		self.assertGreater(chunk.token_count, 0)
		self.assertEqual(len(chunk.checksum), 32)

	def test_checksum_is_stable_and_distinct(self):
		self.assertEqual(Chunk("same", 0).checksum, Chunk("same", 5).checksum)
		self.assertNotEqual(Chunk("one", 0).checksum, Chunk("two", 0).checksum)

	def test_estimate_tokens(self):
		self.assertEqual(estimate_tokens(""), 0)
		self.assertGreater(estimate_tokens("a" * 400), 50)

	def test_split_sentences(self):
		sentences = split_sentences("First one. Second one! Third one? Fourth.")
		self.assertGreaterEqual(len(sentences), 4)

	def test_zero_overlap(self):
		chunks = chunk_text("word " * 2000, chunk_size=300, chunk_overlap=0)
		self.assertGreater(len(chunks), 1)


class TestVectorMath(UnitTestCase):
	def test_encode_decode_roundtrip(self):
		original = [0.5, -0.25, 0.125, 1.0, -1.0]
		decoded = vector.decode_vector(vector.encode_vector(original))

		self.assertEqual(len(decoded), len(original))
		for expected, actual in zip(original, decoded, strict=True):
			self.assertAlmostEqual(expected, actual, places=5)

	def test_encode_empty(self):
		self.assertEqual(vector.encode_vector([]), "")
		self.assertEqual(vector.decode_vector(""), [])
		self.assertEqual(vector.decode_vector(None), [])

	def test_norm(self):
		self.assertAlmostEqual(vector.norm([3.0, 4.0]), 5.0, places=5)
		self.assertEqual(vector.norm([]), 0.0)

	def test_norm_does_not_overflow_for_finite_values(self):
		self.assertTrue(math.isfinite(vector.norm([1e300, 1e300])))

	def test_normalize_produces_unit_vector(self):
		normalised = vector.normalize([3.0, 4.0])
		self.assertAlmostEqual(vector.norm(normalised), 1.0, places=5)

	def test_normalize_zero_vector_is_safe(self):
		self.assertEqual(vector.normalize([0.0, 0.0]), [0.0, 0.0])

	def test_dot_product(self):
		self.assertAlmostEqual(vector.dot([1.0, 2.0, 3.0], [4.0, 5.0, 6.0]), 32.0, places=5)

	def test_dot_with_mismatched_lengths_is_zero(self):
		"""Different dimensions mean different embedding models: never compare them."""
		self.assertEqual(vector.dot([1.0, 2.0], [1.0, 2.0, 3.0]), 0.0)
		self.assertEqual(vector.cosine_similarity([1.0, 2.0], [1.0, 2.0, 3.0]), 0.0)

	def test_cosine_similarity_identical(self):
		v = [0.1, 0.5, -0.3, 0.8]
		self.assertAlmostEqual(vector.cosine_similarity(v, v), 1.0, places=4)

	def test_cosine_similarity_orthogonal(self):
		self.assertAlmostEqual(vector.cosine_similarity([1.0, 0.0], [0.0, 1.0]), 0.0, places=5)

	def test_cosine_similarity_opposite(self):
		self.assertAlmostEqual(vector.cosine_similarity([1.0, 0.0], [-1.0, 0.0]), -1.0, places=5)

	def test_cosine_similarity_empty(self):
		self.assertEqual(vector.cosine_similarity([], [1.0]), 0.0)

	def test_rank_orders_by_similarity(self):
		query = vector.normalize([1.0, 0.0, 0.0])
		candidates = [
			("far", vector.normalize([0.0, 1.0, 0.0])),
			("near", vector.normalize([0.9, 0.1, 0.0])),
			("exact", vector.normalize([1.0, 0.0, 0.0])),
		]
		ranked = vector.rank(query, candidates, top_k=3)

		self.assertEqual([name for name, _ in ranked], ["exact", "near", "far"])
		self.assertGreaterEqual(ranked[0][1], ranked[1][1])

	def test_rank_respects_top_k(self):
		query = vector.normalize([1.0, 0.0])
		candidates = [(str(i), vector.normalize([1.0, i / 10])) for i in range(10)]
		self.assertEqual(len(vector.rank(query, candidates, top_k=3)), 3)

	def test_rank_with_no_candidates(self):
		self.assertEqual(vector.rank([1.0, 0.0], [], top_k=5), [])

	def test_encoded_vectors_survive_similarity(self):
		"""Similarity must survive the base64 round trip used for storage."""
		a = vector.normalize([0.11, 0.42, -0.37, 0.88, 0.05])
		b = vector.normalize([0.10, 0.40, -0.35, 0.90, 0.02])
		direct = vector.cosine_similarity(a, b)

		restored = vector.cosine_similarity(
			vector.decode_vector(vector.encode_vector(a)),
			vector.decode_vector(vector.encode_vector(b)),
		)
		self.assertAlmostEqual(direct, restored, places=4)


class TestEmbeddingProviderContract(UnitTestCase):
	def setUp(self):
		from ai_fr_hg.ai.engine import _validate_embedding_response

		self.validate = _validate_embedding_response

	def test_valid_vectors_are_normalised_to_floats(self):
		self.assertEqual(
			self.validate([[1, 2], [3.0, 4.0]], expected_count=2),
			[[1.0, 2.0], [3.0, 4.0]],
		)

	def test_wrong_vector_count_is_rejected(self):
		from ai_fr_hg.ai.exceptions import ProviderError

		with self.assertRaises(ProviderError):
			self.validate([[1.0, 2.0]], expected_count=2)

	def test_non_list_response_is_rejected(self):
		from ai_fr_hg.ai.exceptions import ProviderError

		with self.assertRaises(ProviderError):
			self.validate(None, expected_count=1)

	def test_non_finite_zero_boolean_and_mixed_dimensions_are_rejected(self):
		from ai_fr_hg.ai.exceptions import ProviderError

		invalid = (
			[[float("nan"), 1.0]],
			[[0.0, 0.0]],
			[[True, 1.0]],
			[["1.0", 2.0]],
			[[1.0, 2.0], [1.0, 2.0, 3.0]],
		)
		for vectors in invalid:
			with self.subTest(vectors=vectors), self.assertRaises(ProviderError):
				self.validate(vectors, expected_count=len(vectors))

	def test_configured_dimensions_are_enforced(self):
		from ai_fr_hg.ai.exceptions import ProviderError

		with self.assertRaises(ProviderError):
			self.validate([[1.0, 2.0]], expected_count=1, expected_dimensions=3)


class TestJSONParsing(UnitTestCase):
	def setUp(self):
		from ai_fr_hg.ai.intelligence import parse_json_response

		self.parse = parse_json_response

	def test_plain_json_object(self):
		self.assertEqual(self.parse('{"a": 1}'), {"a": 1})

	def test_fenced_json(self):
		self.assertEqual(self.parse('```json\n{"a": 1}\n```'), {"a": 1})

	def test_fenced_without_language(self):
		self.assertEqual(self.parse('```\n{"a": 1}\n```'), {"a": 1})

	def test_json_with_surrounding_prose(self):
		text = 'Sure, here is the result:\n{"category": "Invoice", "confidence": 92}\nHope that helps.'
		parsed = self.parse(text)
		self.assertEqual(parsed["category"], "Invoice")

	def test_json_array(self):
		self.assertEqual(self.parse("[1, 2, 3]"), [1, 2, 3])

	def test_invalid_json_returns_none(self):
		self.assertIsNone(self.parse("this is not json at all"))

	def test_empty_returns_none(self):
		self.assertIsNone(self.parse(""))
		self.assertIsNone(self.parse(None))

	def test_nested_object(self):
		parsed = self.parse('{"outer": {"inner": [1, {"deep": true}]}}')
		self.assertTrue(parsed["outer"]["inner"][1]["deep"])


class TestNetworkGuard(UnitTestCase):
	def test_local_hosts_are_recognised(self):
		from ai_fr_hg.utils.network import is_local_url

		for url in (
			"http://localhost:11434",
			"http://127.0.0.1:8080",
			"http://[::1]:8000",
			"http://192.168.1.50:11434",
			"http://10.0.0.5:11434",
			"http://172.16.4.3:11434",
		):
			self.assertTrue(is_local_url(url), f"{url} should be considered local")

	def test_public_hosts_are_rejected(self):
		from ai_fr_hg.utils.network import is_local_url

		for url in ("https://api.openai.com/v1", "http://8.8.8.8", "https://example.com"):
			self.assertFalse(is_local_url(url), f"{url} should not be considered local")

	def test_malformed_url_is_not_local(self):
		from ai_fr_hg.utils.network import is_local_url

		self.assertFalse(is_local_url(""))
		self.assertFalse(is_local_url("not a url"))


class TestReaderRegistry(UnitTestCase):
	def test_common_formats_are_registered(self):
		from ai_fr_hg.ai.readers import supported_extensions

		extensions = set(supported_extensions())
		for expected in ("pdf", "docx", "xlsx", "pptx", "odt", "ods", "txt", "md", "csv", "json", "html"):
			self.assertIn(expected, extensions)

	def test_reader_lookup_is_case_insensitive(self):
		from ai_fr_hg.ai.readers import get_reader

		self.assertIsNotNone(get_reader("report.PDF"))
		self.assertIsNotNone(get_reader("report.pdf"))

	def test_unknown_extension_returns_none(self):
		from ai_fr_hg.ai.readers import get_reader

		self.assertIsNone(get_reader("mystery.zzz"))
		self.assertIsNone(get_reader("no_extension"))

	def test_plain_text_reader_round_trip(self):
		from ai_fr_hg.ai.readers import get_reader

		reader = get_reader("notes.txt")
		result = reader.read(b"Hello, local world.", "notes.txt")
		self.assertIn("Hello, local world.", result.text)

	def test_json_reader_produces_readable_text(self):
		from ai_fr_hg.ai.readers import get_reader

		reader = get_reader("data.json")
		result = reader.read(b'{"name": "Widget", "price": 9.99}', "data.json")
		self.assertIn("Widget", result.text)

	def test_csv_reader(self):
		from ai_fr_hg.ai.readers import get_reader

		reader = get_reader("rows.csv")
		result = reader.read(b"name,qty\nBolt,10\nNut,25\n", "rows.csv")
		self.assertIn("Bolt", result.text)
		self.assertIn("25", result.text)


class TestChunkTokenEstimates(UnitTestCase):
	def test_token_count_scales_with_length(self):
		short = Chunk("a" * 100, 0)
		long = Chunk("a" * 1000, 0)
		self.assertLess(short.token_count, long.token_count)

	def test_similarity_is_symmetric(self):
		a = vector.normalize([0.3, 0.7, -0.2])
		b = vector.normalize([0.1, 0.9, 0.4])
		self.assertAlmostEqual(vector.cosine_similarity(a, b), vector.cosine_similarity(b, a), places=6)

	def test_similarity_bounded(self):
		import random

		random.seed(42)
		for _ in range(20):
			a = vector.normalize([random.uniform(-1, 1) for _ in range(16)])
			b = vector.normalize([random.uniform(-1, 1) for _ in range(16)])
			score = vector.cosine_similarity(a, b)
			self.assertTrue(-1.0001 <= score <= 1.0001, f"score {score} out of range")
			self.assertFalse(math.isnan(score))


class TestToolCallWireFormats(UnitTestCase):
	"""Each runtime family expects a different shape for tool call arguments.

	Ollama's Go API decodes `function.arguments` into a map, so sending the
	OpenAI-style JSON string makes it reply with HTTP 400 "cannot unmarshal
	string into Go struct field ...ToolCallFunctionArguments".
	"""

	@staticmethod
	def _assistant_message(arguments):
		from ai_fr_hg.ai.providers.base import ChatMessage

		return ChatMessage(
			role="assistant",
			content="",
			tool_calls=[
				{
					"id": "call_0",
					"type": "function",
					"function": {"name": "get_doc", "arguments": arguments},
				}
			],
		)

	def test_ollama_arguments_are_objects(self):
		from ai_fr_hg.ai.providers.ollama import OllamaProvider

		payload = OllamaProvider._to_ollama_message(self._assistant_message({"doctype": "User"}))
		self.assertEqual(payload["tool_calls"][0]["function"]["arguments"], {"doctype": "User"})

	def test_ollama_decodes_stringified_arguments(self):
		from ai_fr_hg.ai.providers.ollama import OllamaProvider

		payload = OllamaProvider._to_ollama_message(self._assistant_message('{"doctype": "User"}'))
		self.assertEqual(payload["tool_calls"][0]["function"]["arguments"], {"doctype": "User"})

	def test_ollama_handles_unparseable_arguments(self):
		from ai_fr_hg.ai.providers.ollama import OllamaProvider

		payload = OllamaProvider._to_ollama_message(self._assistant_message("not json"))
		self.assertEqual(payload["tool_calls"][0]["function"]["arguments"], {"_raw": "not json"})

	def test_ollama_tool_result_uses_tool_name(self):
		from ai_fr_hg.ai.providers.base import ChatMessage
		from ai_fr_hg.ai.providers.ollama import OllamaProvider

		payload = OllamaProvider._to_ollama_message(
			ChatMessage(role="tool", content="{}", name="get_doc", tool_call_id="call_0")
		)
		self.assertEqual(payload["tool_name"], "get_doc")
		self.assertNotIn("name", payload)

	def test_openai_arguments_are_json_strings(self):
		import json

		from ai_fr_hg.ai.providers.openai_compatible import OpenAICompatibleProvider

		payload = OpenAICompatibleProvider._to_openai_message(self._assistant_message({"doctype": "User"}))
		arguments = payload["tool_calls"][0]["function"]["arguments"]
		self.assertIsInstance(arguments, str)
		self.assertEqual(json.loads(arguments), {"doctype": "User"})

	def test_openai_keeps_existing_string_arguments(self):
		from ai_fr_hg.ai.providers.openai_compatible import OpenAICompatibleProvider

		payload = OpenAICompatibleProvider._to_openai_message(self._assistant_message('{"doctype": "User"}'))
		self.assertEqual(payload["tool_calls"][0]["function"]["arguments"], '{"doctype": "User"}')


class TestSimilarityThreshold(UnitTestCase):
	"""Desk users type 25 meaning 25%; the store keeps a 0–1 cosine score."""

	def test_fraction_is_unchanged(self):
		from ai_fr_hg.ai.settings import normalize_similarity_threshold

		self.assertEqual(normalize_similarity_threshold(0.25), 0.25)
		self.assertEqual(normalize_similarity_threshold(0), 0.0)
		self.assertEqual(normalize_similarity_threshold(1), 1.0)

	def test_percentage_is_converted(self):
		from ai_fr_hg.ai.settings import normalize_similarity_threshold

		self.assertAlmostEqual(normalize_similarity_threshold(25), 0.25)
		self.assertAlmostEqual(normalize_similarity_threshold(100), 1.0)
		self.assertAlmostEqual(normalize_similarity_threshold(1.5), 0.015)

	def test_out_of_range_is_rejected(self):
		from ai_fr_hg.ai.settings import normalize_similarity_threshold

		class Thrown(Exception):
			pass

		with patch("ai_fr_hg.ai.settings.frappe.throw", side_effect=Thrown):
			with self.assertRaises(Thrown):
				normalize_similarity_threshold(-0.1)
			with self.assertRaises(Thrown):
				normalize_similarity_threshold(150)


class TestDeadline(UnitTestCase):
	"""The time budget that keeps a chat turn inside the proxy's patience."""

	def make_deadline(self, budget, now=None):
		"""A deadline driven by a hand-cranked clock, so no test sleeps."""
		from ai_fr_hg.ai.deadline import Deadline

		clock = {"t": 0.0}
		deadline = Deadline(budget, clock=lambda: clock["t"])
		return deadline, clock

	def test_remaining_shrinks_as_time_passes(self):
		deadline, clock = self.make_deadline(100)
		self.assertEqual(deadline.remaining(), 100)

		clock["t"] = 30
		self.assertEqual(deadline.remaining(), 70)

	def test_remaining_never_goes_negative(self):
		deadline, clock = self.make_deadline(10)
		clock["t"] = 999
		self.assertEqual(deadline.remaining(), 0)
		self.assertTrue(deadline.expired)

	def test_clamp_caps_timeout_at_remaining_budget(self):
		deadline, clock = self.make_deadline(100)
		clock["t"] = 70

		# 30s left, minus the 2s reserve, so a 120s call is cut to 28s.
		self.assertAlmostEqual(deadline.clamp(120), 28.0)

	def test_clamp_leaves_short_timeouts_alone(self):
		deadline, _clock = self.make_deadline(100)
		self.assertEqual(deadline.clamp(5), 5)

	def test_clamp_returns_zero_when_budget_is_spent(self):
		"""Zero is the signal to give up rather than start a doomed call."""
		deadline, clock = self.make_deadline(100)
		clock["t"] = 99.5
		self.assertEqual(deadline.clamp(120), 0.0)

	def test_allows_accounts_for_the_reserve(self):
		deadline, clock = self.make_deadline(100)
		clock["t"] = 90

		# 10s left: room for 5s of work plus the 2s reserve, but not 9s.
		self.assertTrue(deadline.allows(5))
		self.assertFalse(deadline.allows(9))


class TestTurnBudget(UnitTestCase):
	"""`turn_budget` installs the deadline the rest of the stack reads."""

	def setUp(self):
		patcher = patch("ai_fr_hg.ai.deadline.frappe")
		self.frappe_mock = patcher.start()
		self.addCleanup(patcher.stop)
		self.frappe_mock.flags = {}

	def test_budget_is_visible_inside_and_gone_after(self):
		from ai_fr_hg.ai.deadline import get_deadline, turn_budget

		with turn_budget(60) as deadline:
			self.assertIsNotNone(deadline)
			self.assertIs(get_deadline(), deadline)

		self.assertIsNone(get_deadline())

	def test_zero_disables_budgeting(self):
		"""Background jobs have no proxy in front of them and stay unbounded."""
		from ai_fr_hg.ai.deadline import clamp_timeout, get_deadline, turn_budget

		with turn_budget(0) as deadline:
			self.assertIsNone(deadline)
			self.assertIsNone(get_deadline())
			# No budget means callers keep their own timeout.
			self.assertIsNone(clamp_timeout(120))

	def test_nested_budget_cannot_extend_the_outer_one(self):
		from ai_fr_hg.ai.deadline import get_deadline, turn_budget

		with turn_budget(30) as outer:
			with turn_budget(600) as inner:
				self.assertIs(inner, outer)
			self.assertIs(get_deadline(), outer)

	def test_budget_is_restored_after_an_exception(self):
		from ai_fr_hg.ai.deadline import get_deadline, turn_budget

		with self.assertRaises(ValueError):
			with turn_budget(60):
				raise ValueError("boom")

		self.assertIsNone(get_deadline())

	def test_helpers_are_permissive_without_a_budget(self):
		from ai_fr_hg.ai.deadline import allows, expired, remaining_seconds

		self.assertTrue(allows(9999))
		self.assertFalse(expired())
		self.assertIsNone(remaining_seconds())


class TestInteractiveDefaults(UnitTestCase):
	def test_document_wait_is_short(self):
		from ai_fr_hg.ai.ingestion import DEFAULT_WAIT_SECONDS

		# Interactive chat must not sit on a 45s poll before the model starts.
		self.assertLessEqual(DEFAULT_WAIT_SECONDS, 10)


class TestTurnBudgetConfig(UnitTestCase):
	def test_none_and_zero_are_unlimited(self):
		from ai_fr_hg.ai.settings import coerce_turn_budget

		self.assertEqual(coerce_turn_budget(None), 0)
		self.assertEqual(coerce_turn_budget(0), 0)
		self.assertEqual(coerce_turn_budget("0"), 0)

	def test_positive_values_are_kept(self):
		from ai_fr_hg.ai.settings import coerce_turn_budget

		self.assertEqual(coerce_turn_budget(90), 90)
		self.assertEqual(coerce_turn_budget("45"), 45)


class TestLanguageDetection(UnitTestCase):
	"""Document language is detected from extracted text, without extra packages."""

	def test_empty_and_short_text_are_unknown(self):
		from ai_fr_hg.ai.language import detect_language

		self.assertEqual(detect_language(""), "")
		self.assertEqual(detect_language(None), "")
		self.assertEqual(detect_language("   \n"), "")
		self.assertEqual(detect_language("too short"), "")

	def test_bulgarian_is_first_class(self):
		from ai_fr_hg.ai.language import detect_language, language_name

		text = (
			"Това е документ за България. В него се описва как да се работи "
			"с файловете, които са качени към базата знания, и какво трябва "
			"да се направи за обработката им."
		)
		self.assertEqual(detect_language(text), "bg")
		self.assertEqual(language_name("bg"), "Bulgarian")

	def test_english_is_detected(self):
		from ai_fr_hg.ai.language import detect_language

		text = (
			"This is the report for the project and the team. The results are "
			"from the meeting on Monday with the client and the notes that follow."
		)
		self.assertEqual(detect_language(text), "en")

	def test_russian_is_not_bulgarian(self):
		from ai_fr_hg.ai.language import detect_language

		text = (
			"Это документ о том, что он и она должны сделать для проекта. "
			"Они были в офисе и это было важно для его работы."
		)
		self.assertEqual(detect_language(text), "ru")

	def test_german_and_french(self):
		from ai_fr_hg.ai.language import detect_language

		german = "Das ist der Bericht und die Analyse von dem Projekt mit einer neuen Methode."
		french = "Le rapport et les notes de la réunion sont dans le dossier pour une revue."
		self.assertEqual(detect_language(german), "de")
		self.assertEqual(detect_language(french), "fr")

	def test_script_gates(self):
		from ai_fr_hg.ai.language import detect_language

		self.assertEqual(detect_language("这是一份中文文件内容足够长可以识别语言了"), "zh")
		self.assertEqual(detect_language("これはひらがなと漢字が混ざった日本語の文書です"), "ja")
		self.assertEqual(detect_language("이것은 한글로 작성된 문서이며 언어를 식별합니다"), "ko")
		self.assertEqual(detect_language("هذا مستند مكتوب باللغة العربية وهو طويل بما يكفي"), "ar")
		self.assertEqual(detect_language("Αυτό είναι ένα ελληνικό κείμενο για αναγνώριση"), "el")

	def test_stored_code_wins_over_detection(self):
		from ai_fr_hg.ai.language import resolve_document_language

		english = "This is the report for the project and the team with the results."
		self.assertEqual(resolve_document_language("bg", english), "bg")
		self.assertEqual(resolve_document_language("", english), "en")
		self.assertEqual(resolve_document_language(None, ""), "")

	def test_build_context_labels_language(self):
		from ai_fr_hg.ai.knowledge import RetrievedChunk, build_context

		context = build_context(
			[
				RetrievedChunk(
					chunk="c1",
					document="DOC-1",
					document_title="Договор",
					knowledge_base="KB",
					content="Това е текст на договора за тази услуга и за клиента.",
					score=0.9,
					language="bg",
				)
			],
			max_characters=4000,
		)
		self.assertIn("language=Bulgarian", context)
		self.assertIn("Договор", context)

	def test_build_context_detects_language_when_field_is_empty(self):
		from ai_fr_hg.ai.knowledge import RetrievedChunk, build_context

		context = build_context(
			[
				RetrievedChunk(
					chunk="c1",
					document="DOC-1",
					document_title="Notes",
					knowledge_base="KB",
					content="This is the report for the project and the team with the results.",
					score=0.9,
				)
			],
			max_characters=4000,
		)
		self.assertIn("language=English", context)


class TestStreamingDecision(UnitTestCase):
	def test_streams_only_the_final_tool_free_completion(self):
		from ai_fr_hg.ai.settings import should_stream_completion

		self.assertTrue(should_stream_completion(requested=True, enabled=True, offer_tools=None))
		self.assertFalse(should_stream_completion(requested=True, enabled=True, offer_tools=[{"name": "search"}]))
		self.assertFalse(should_stream_completion(requested=True, enabled=False, offer_tools=None))
		self.assertFalse(should_stream_completion(requested=False, enabled=True, offer_tools=None))

	def test_stream_fallback_uses_blocking_chat_when_no_tokens_arrived(self):
		from types import SimpleNamespace
		from unittest.mock import Mock

		from ai_fr_hg.ai.engine import _complete_chat
		from ai_fr_hg.ai.providers.base import CompletionResult

		provider = SimpleNamespace(supports_streaming=True)
		provider.stream_chat = Mock(side_effect=RuntimeError("stream dropped before first token"))
		provider.chat = Mock(return_value=CompletionResult(content="blocking answer"))
		tokens = []
		result = _complete_chat(
			provider,
			[],
			model="test",
			options={},
			tools=None,
			json_schema=None,
			on_token=tokens.append,
		)
		self.assertEqual(result.content, "blocking answer")
		self.assertEqual(tokens, [])
		provider.chat.assert_called_once()

	def test_stream_success_publishes_every_fragment(self):
		from types import SimpleNamespace

		from ai_fr_hg.ai.engine import _complete_chat

		provider = SimpleNamespace(supports_streaming=True)
		provider.stream_chat = lambda *args, **kwargs: iter(["Hel", "lo"])
		provider.chat = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not fall back"))
		tokens = []
		result = _complete_chat(
			provider,
			[],
			model="test",
			options={},
			tools=None,
			json_schema=None,
			on_token=tokens.append,
		)
		self.assertEqual(tokens, ["Hel", "lo"])
		self.assertEqual(result.content, "Hello")
		self.assertTrue(result.raw.get("streamed"))

	def test_mid_stream_failure_does_not_start_a_second_completion(self):
		from types import SimpleNamespace
		from unittest.mock import Mock

		from ai_fr_hg.ai.engine import _complete_chat

		def broken_stream(*args, **kwargs):
			yield "Hel"
			raise RuntimeError("socket dropped")

		provider = SimpleNamespace(supports_streaming=True)
		provider.stream_chat = broken_stream
		provider.chat = Mock()
		with self.assertRaises(RuntimeError):
			_complete_chat(
				provider,
				[],
				model="test",
				options={},
				tools=None,
				json_schema=None,
				on_token=lambda delta: None,
			)
		provider.chat.assert_not_called()
