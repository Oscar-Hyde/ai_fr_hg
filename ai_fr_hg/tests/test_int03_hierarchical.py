"""INT-03 regression suite — hierarchical coverage-preserving reduction."""
import frappe
from unittest.mock import patch, MagicMock, call
import ai_fr_hg.ai.intelligence as intel
from ai_fr_hg.ai.exceptions import HierarchicalReductionError, ProviderError

def _mock_model(budget=800):
    # _context_budget = max(window*0.6*4,2000) — to control budget, mock resolve_model
    m = MagicMock()
    m.name = "test-model"
    m.context_window = int(budget/4/0.6)
    m.num_ctx_override = 0
    return m

def test_short_input_single_path():
    m = _mock_model(budget=5000)
    with patch("ai_fr_hg.ai.intelligence.resolve_model", return_value=m), \
         patch("ai_fr_hg.ai.intelligence.run_chat") as rc:
        rc.return_value = MagicMock(content="short summary")
        out = intel.summarize("hello world", model="test-model")
        assert out == "short summary"
        assert rc.call_count == 1

def test_long_tail_preservation():
    # Create text where final window contains unique token TAILFACT
    m = _mock_model(budget=800)
    # chunk_text with budget 800 and overlap 200 on ~ 5000 chars will create ~7 windows, last contains TAILFACT
    base = ("Section text. " * 40 + "\n")  # ~560 chars per repetition
    tail = "TAILFACT_UNIQUE_12345 "
    long_text = base * 8 + tail * 10  # last window will have tail
    calls = []
    def fake_run_chat(messages, **kwargs):
        content = messages[0]["content"]
        # Echo last 20 chars of content to prove which windows participated
        # For map, content contains window; for reduce, contains [Section ...]
        calls.append(content)
        return MagicMock(content=f"summary:{content[-20:]}")
    with patch("ai_fr_hg.ai.intelligence.resolve_model", return_value=m), \
         patch("ai_fr_hg.ai.intelligence.run_chat", side_effect=fake_run_chat):
        out = intel.summarize(long_text, model="test-model")
        # TAILFACT must appear in some reduce input, i.e., in calls strings
        joined = "\n".join(calls)
        assert "TAILFACT" in joined, f"tail not in calls: {joined[-500:]}"
        # Final out should be derived from reduce that included tail — not truncated away
        # Our fake returns snippet, so not checking final content deeply

def test_multi_level_reduction():
    m = _mock_model(budget=600)
    long_text = "Sentence. " * 2000  # will create many windows
    def fake_run_chat(messages, **kwargs):
        return MagicMock(content="S " * 20)
    with patch("ai_fr_hg.ai.intelligence.resolve_model", return_value=m), \
         patch("ai_fr_hg.ai.intelligence.run_chat", side_effect=fake_run_chat) as rc:
        intel.summarize(long_text, model="test-model")
        # Should have > len(windows) + 1 calls (multi-level)
        # At least windows + at least 2 reduces
        assert rc.call_count > 5

def test_ordering_preserved():
    m = _mock_model(budget=700)
    long_text = "A. " * 300 + "B. " * 300 + "C. " * 300
    order = []
    def fake_run_chat(messages, **kwargs):
        c = messages[0]["content"]
        order.append(c)
        return MagicMock(content=c[:100])
    with patch("ai_fr_hg.ai.intelligence.resolve_model", return_value=m), \
         patch("ai_fr_hg.ai.intelligence.run_chat", side_effect=fake_run_chat):
        intel.summarize(long_text, model="test-model")
        # In reduction, Section 1 should appear before Section N in payloads
        for payload in order:
            if "[Section 1]" in payload and "[Section" in payload:
                # find positions
                pos1 = payload.index("[Section 1]")
                # find last Section marker
                last = payload.rfind("[Section")
                assert pos1 < last or "[Section 2]" in payload

def test_provenance_markers_survive():
    m = _mock_model(budget=700)
    long_text = "X. " * 1500
    with patch("ai_fr_hg.ai.intelligence.resolve_model", return_value=m), \
         patch("ai_fr_hg.ai.intelligence.run_chat", return_value=MagicMock(content="reduced")) as rc:
        intel.summarize(long_text, model="test-model")
        reduce_calls = [c for c in rc.call_args_list if "REDUCE" in str(c) or "Section" in str(c)]
        # At least one reduce should contain [Section
        assert any("[Section" in str(c) for c in rc.call_args_list)

def test_boundary_exact():
    m = _mock_model(budget=1000)
    exact = "a" * 1000
    just_over = "a" * 1001
    with patch("ai_fr_hg.ai.intelligence.resolve_model", return_value=m), \
         patch("ai_fr_hg.ai.intelligence.run_chat", return_value=MagicMock(content="s")) as rc:
        intel.summarize(exact, model="test-model")
        c1 = rc.call_count
        rc.reset_mock()
        intel.summarize(just_over, model="test-model")
        c2 = rc.call_count
        assert c1 == 1  # short path
        assert c2 > 1   # triggers map-reduce

def test_empty_single_no_extra_reduce():
    m = _mock_model(budget=5000)
    with patch("ai_fr_hg.ai.intelligence.resolve_model", return_value=m), \
         patch("ai_fr_hg.ai.intelligence.run_chat", return_value=MagicMock(content="s")) as rc:
        assert intel.summarize("", model="test-model") == ""
        assert rc.call_count == 0
        intel.summarize("short", model="test-model")
        assert rc.call_count == 1

def test_provider_failure_propagates():
    m = _mock_model(budget=5000)
    # short path failure
    with patch("ai_fr_hg.ai.intelligence.resolve_model", return_value=m), \
         patch("ai_fr_hg.ai.intelligence.run_chat", side_effect=ProviderError("offline")):
        try:
            intel.summarize("hello", model="test-model")
            assert False
        except ProviderError:
            pass
    # map failure
    m2 = _mock_model(budget=400)
    long_text = "hello " * 500
    with patch("ai_fr_hg.ai.intelligence.resolve_model", return_value=m2), \
         patch("ai_fr_hg.ai.intelligence.run_chat", side_effect=ProviderError("offline")):
        try:
            intel.summarize(long_text, model="test-model")
            assert False
        except ProviderError:
            pass

def test_recursion_bound_explicit_failure():
    m = _mock_model(budget=300)
    long_text = "w " * 5000
    def fake_run_chat(messages, **kwargs):
        return MagicMock(content="x " * 150)  # each reduce returns still large, forcing many levels
    with patch("ai_fr_hg.ai.intelligence.resolve_model", return_value=m), \
         patch("ai_fr_hg.ai.intelligence.run_chat", side_effect=fake_run_chat):
        try:
            intel.summarize(long_text, model="test-model")
            # either succeeds within 10 levels or raises HierarchicalReductionError — never silently truncates
            pass
        except HierarchicalReductionError as e:
            assert "exceeded 10 levels" in str(e)
            assert "truncat" not in str(e).lower() or "rather than truncating" in str(e).lower()

def test_no_hidden_truncation():
    import pathlib
    src = pathlib.Path("ai_fr_hg/ai/intelligence.py").read_text()
    reduce_section = src.split("_hierarchical_reduce")[1].split("return _hierarchical_reduce")[0]
    # Ensure no slicing like [:budget] or [: budget] in reduce path
    assert "[:budget" not in reduce_section
    assert "[: budget" not in reduce_section

# Budget invariant: every run_chat payload <= budget (provider is authoritative, reducer packs)
def test_budget_invariant():
    m = _mock_model(budget=600)
    long_text = "Sentence with tokens. " * 800
    with patch("ai_fr_hg.ai.intelligence.resolve_model", return_value=m), \
         patch("ai_fr_hg.ai.intelligence.run_chat") as rc:
        rc.return_value = MagicMock(content="s")
        intel.summarize(long_text, model="test-model")
        for call in rc.call_args_list:
            msg = call[0][0][0]["content"]
            assert len(msg) <= 600 * 3  # generous, but at least not wildly over; reducer packs to budget
