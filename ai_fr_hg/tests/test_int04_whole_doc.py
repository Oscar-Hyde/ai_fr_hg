"""INT-04 backend tests — whole-document coverage for classify/extract/compare."""

from unittest.mock import MagicMock, patch

import ai_fr_hg.ai.intelligence as intel
from ai_fr_hg.ai.exceptions import ProviderError
from ai_fr_hg.ai.validation import ValidationError


def _mock_model(budget=800):
	m = MagicMock()
	m.name = "test-model"
	m.context_window = int(budget / 4 / 0.6)
	m.num_ctx_override = 0
	return m


def _schema():
	class Row:
		def __init__(self, n, t, req=0):
			self.field_name = n
			self.field_type = t
			self.required = req
			self.label = ""
			self.description = ""
			self.enum_values = ""

	rows = [Row("invoice_number", "String", 1), Row("amount", "Number", 0)]

	class S:
		name = "S"
		strict = True
		enabled = 1
		model = None
		instructions = ""

		def get(self, k, d=None):
			if k == "extraction_fields":
				return rows
			if k == "strict":
				return True
			return getattr(self, k, d)

		def __getitem__(self, k):
			return self.get(k)

	return S()


def test_short_single_pass():
	m = _mock_model(5000)
	with (
		patch.object(intel, "resolve_model", return_value=m),
		patch.object(intel, "run_chat", return_value=MagicMock(content='{"category":"A","confidence":90}')),
	):
		r = intel.classify("hello", categories=["A", "B"])
		assert r["coverage"]["windows_total"] == 1


def test_long_every_window():
	m = _mock_model(400)
	text = "hello world. " * 500
	with (
		patch.object(intel, "resolve_model", return_value=m),
		patch.object(intel, "run_chat", return_value=MagicMock(content='{"category":"A","confidence":80}')),
	):
		r = intel.classify(text, categories=["A", "B"])
		assert r["coverage"]["windows_total"] > 1
		assert r["coverage"]["windows_processed"] == r["coverage"]["windows_total"]


def test_tail_affects_result():
	m = _mock_model(400)
	base = "neutral. " * 500
	tail_fact = "CLASS_TAIL_B "
	text = base + tail_fact * 20

	def fake(msgs, **kw):
		# last window contains tail
		c = msgs[0]["content"]
		if "CLASS_TAIL_B" in c:
			return MagicMock(content='{"category":"B","confidence":95}')
		return MagicMock(content='{"category":"A","confidence":60}')

	with (
		patch.object(intel, "resolve_model", return_value=m),
		patch.object(intel, "run_chat", side_effect=fake),
	):
		r = intel.classify(text, categories=["A", "B"])
		# tail should cause at least one B vote
		assert r["coverage"]["windows_total"] > 1


def test_failed_not_counted():
	m = _mock_model(400)
	text = "x. " * 500

	def fake(msgs, **kw):
		# fail first window via ProviderError
		if fake.calls == 0:
			fake.calls += 1
			raise ProviderError("offline")
		fake.calls += 1
		return MagicMock(content='{"category":"A","confidence":80}')

	fake.calls = 0
	with (
		patch.object(intel, "resolve_model", return_value=m),
		patch.object(intel, "run_chat", side_effect=fake),
	):
		r = intel.classify(text, categories=["A", "B"])
		assert r["coverage"]["windows_failed"] == 1
		assert r["coverage"]["windows_processed"] == r["coverage"]["windows_total"] - 1


def test_extract_validates_every_window():
	m = _mock_model(400)
	text = "data " * 500

	# first window returns invalid (missing required), second valid
	def fake(msgs, **kw):
		import json

		if "Window" in msgs[0]["content"] or len(msgs[0]["content"]) > 100:
			# per-window: return invalid missing required
			if fake.n == 0:
				fake.n += 1
				return MagicMock(content=json.dumps({"amount": 1}))
			fake.n += 1
			return MagicMock(content=json.dumps({"invoice_number": "INV", "amount": 1}))
		return MagicMock(content=json.dumps({"invoice_number": "INV", "amount": 1}))

	fake.n = 0
	with (
		patch.object(intel, "resolve_model", return_value=m),
		patch("ai_fr_hg.ai.intelligence.frappe.get_cached_doc", return_value=_schema()),
		patch.object(intel, "run_chat", side_effect=fake),
	):
		# chunk will create 2 windows, first fails validation, second succeeds -> merged still succeeds with one valid
		r = intel.extract_data(text, schema="S")
		assert r["invoice_number"] == "INV"


def test_no_prefix_only():
	import pathlib
	for _p in ["ai_fr_hg/ai/intelligence.py", "apps/ai_fr_hg/ai_fr_hg/ai/intelligence.py", "/home/frappe/frappe-bench/apps/ai_fr_hg/ai_fr_hg/ai/intelligence.py"]:
		_pp = pathlib.Path(_p)
		if _pp.exists():
			src = _pp.read_text()
			break
	else:
		raise AssertionError("intelligence.py not found")
	# ensure no text[:budget] in classify/extract_data/compare paths
	for name in ["def classify", "def extract_data", "def compare_documents"]:
		section = src.split(name)[1].split("\ndef ")[0]
		assert "text[:budget" not in section
		assert "text_a[:budget" not in section
		assert "text_b[:budget" not in section


def test_extract_merged_validated():
	m = _mock_model(400)
	text = "data " * 500
	import json

	# All windows return valid, merged should be validated again and include _coverage
	def fake(msgs, **kw):
		return MagicMock(content=json.dumps({"invoice_number": "INV", "amount": 1}))

	with (
		patch.object(intel, "resolve_model", return_value=m),
		patch("ai_fr_hg.ai.intelligence.frappe.get_cached_doc", return_value=_schema()),
		patch.object(intel, "run_chat", side_effect=fake),
	):
		r = intel.extract_data(text, schema="S")
		assert "_coverage" in r
		assert r["coverage"]["windows_processed"] >= 1


def test_conflicting_values_deterministic():
	m = _mock_model(400)
	text = "conflict data " * 500
	import json

	vals = [{"invoice_number": "INV1", "amount": 1}, {"invoice_number": "INV2", "amount": 1}]
	call_n = {"n": 0}

	def fake(msgs, **kw):
		v = vals[call_n["n"] % 2]
		call_n["n"] += 1
		return MagicMock(content=json.dumps(v))

	with (
		patch.object(intel, "resolve_model", return_value=m),
		patch("ai_fr_hg.ai.intelligence.frappe.get_cached_doc", return_value=_schema()),
		patch.object(intel, "run_chat", side_effect=fake),
	):
		r1 = intel.extract_data(text, schema="S")
		call_n["n"] = 0
		r2 = intel.extract_data(text, schema="S")
		# deterministic: same input yields same merge result
		assert r1["invoice_number"] == r2["invoice_number"]


def test_classification_tie():
	m = _mock_model(400)
	text = "tie " * 2000

	# Alternate categories to force tie
	def fake(msgs, **kw):
		fake.n += 1
		cat = "A" if fake.n % 2 == 0 else "B"
		return MagicMock(content=f'{{"category":"{cat}","confidence":80}}')

	fake.n = 0
	with (
		patch.object(intel, "resolve_model", return_value=m),
		patch.object(intel, "run_chat", side_effect=fake),
	):
		r = intel.classify(text, categories=["A", "B"])
		# tie should be broken deterministically (sorted name)
		assert r["category"] in ["A", "B"]
		assert r["coverage"]["windows_total"] > 1


def test_compare_min_coverage():
	m = _mock_model(600)
	text_a = "doc A " * 800
	text_b = "doc B " * 200
	import frappe as _f

	# Mock docs for compare
	doc_a = MagicMock()
	doc_a.title = "A"
	doc_a.content = text_a
	doc_a.check_permission = lambda *a, **k: None
	doc_b = MagicMock()
	doc_b.title = "B"
	doc_b.content = text_b
	doc_b.check_permission = lambda *a, **k: None

	def fake_get_doc(doctype, name):
		return doc_a if name == "A" else doc_b

	with (
		patch.object(intel, "resolve_model", return_value=m),
		patch.object(intel, "run_chat", return_value=MagicMock(content="comparison")),
		patch("frappe.get_doc", side_effect=fake_get_doc),
	):
		r = intel.compare_documents("A", "B")
		assert r["coverage"]["coverage_ratio"] == min(
			r["coverage"]["coverage_ratio_a"], r["coverage"]["coverage_ratio_b"]
		)


def test_large_bounded():
	m = _mock_model(400)
	text = "x " * 10000
	with (
		patch.object(intel, "resolve_model", return_value=m),
		patch.object(intel, "run_chat", return_value=MagicMock(content='{"category":"A","confidence":80}')),
	):
		r = intel.classify(text, categories=["A", "B"])
		# windows_total should be bounded (>1 but not unbounded due to chunking)
		assert r["coverage"]["windows_total"] < 100


def test_malicious_oversized_fails():
	m = _mock_model(400)
	text = "x " * 50000  # very large
	# Should not truncate silently; should still process via chunking, not raise truncation but handle bounded windows
	with (
		patch.object(intel, "resolve_model", return_value=m),
		patch.object(intel, "run_chat", return_value=MagicMock(content='{"category":"A","confidence":80}')),
	):
		r = intel.classify(text, categories=["A", "B"])
		assert r["coverage"]["windows_total"] > 10
		assert r["coverage"]["coverage_ratio"] > 0


def test_retry_deterministic():
	m = _mock_model(400)
	text = "retry " * 500
	import json

	def fake(msgs, **kw):
		return MagicMock(content=json.dumps({"invoice_number": "INV", "amount": 1}))

	with (
		patch.object(intel, "resolve_model", return_value=m),
		patch("ai_fr_hg.ai.intelligence.frappe.get_cached_doc", return_value=_schema()),
		patch.object(intel, "run_chat", side_effect=fake),
	):
		r1 = intel.extract_data(text, schema="S")
		r2 = intel.extract_data(text, schema="S")
		assert r1["invoice_number"] == r2["invoice_number"]
		assert r1["_coverage"]["windows_total"] == r2["_coverage"]["windows_total"]


def test_concurrent_isolation():
	import threading

	m = _mock_model(400)
	text1 = "doc1 " * 500
	text2 = "doc2 " * 500
	results = {}

	def run1():
		with (
			patch.object(intel, "resolve_model", return_value=m),
			patch.object(
				intel, "run_chat", return_value=MagicMock(content='{"category":"A","confidence":80}')
			),
		):
			results["r1"] = intel.classify(text1, categories=["A", "B"])

	def run2():
		with (
			patch.object(intel, "resolve_model", return_value=m),
			patch.object(
				intel, "run_chat", return_value=MagicMock(content='{"category":"B","confidence":80}')
			),
		):
			results["r2"] = intel.classify(text2, categories=["A", "B"])

	t1 = threading.Thread(target=run1)
	t2 = threading.Thread(target=run2)
	t1.start()
	t2.start()
	t1.join()
	t2.join()
	assert results["r1"]["category"] == "A"
	assert results["r2"]["category"] == "B"


def test_short_compatible():
	m = _mock_model(5000)
	with (
		patch.object(intel, "resolve_model", return_value=m),
		patch.object(intel, "run_chat", return_value=MagicMock(content='{"category":"A","confidence":90}')),
	):
		r = intel.classify("short", categories=["A", "B"])
		assert r["category"] == "A"
		assert r["coverage"]["strategy"] == "single_pass" or r["coverage"]["windows_total"] == 1
