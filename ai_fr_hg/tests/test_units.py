# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Pure unit tests for the AI platform's algorithms.

These exercise chunking, vector maths, redaction, ranking and JSON parsing
without touching the database or a model runtime, so they run fast and are
safe in any environment.
"""

import math
from itertools import pairwise

from frappe.tests import UnitTestCase

from ai_fr_hg.ai import vector
from ai_fr_hg.ai.chunking import Chunk, chunk_text, estimate_tokens, split_sentences


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
		for expected in ("pdf", "docx", "xlsx", "pptx", "txt", "md", "csv", "json", "html"):
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
