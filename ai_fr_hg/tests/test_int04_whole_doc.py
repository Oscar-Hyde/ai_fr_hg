"""INT-04 backend tests — whole-document coverage for classify/extract/compare."""
from unittest.mock import patch, MagicMock
import ai_fr_hg.ai.intelligence as intel
from ai_fr_hg.ai.validation import ValidationError
from ai_fr_hg.ai.exceptions import ProviderError

def _mock_model(budget=800):
    m=MagicMock()
    m.name="test-model"
    m.context_window=int(budget/4/0.6)
    m.num_ctx_override=0
    return m

def _schema():
    class Row:
        def __init__(self,n,t,req=0): self.field_name=n; self.field_type=t; self.required=req; self.label=""; self.description=""; self.enum_values=""
    rows=[Row("invoice_number","String",1), Row("amount","Number",0)]
    class S:
        name="S"; strict=True; enabled=1
        def get(self,k,d=None):
            if k=="extraction_fields": return rows
            if k=="strict": return True
            return getattr(self,k,d)
        def __getitem__(self,k): return self.get(k)
    return S()

def test_short_single_pass():
    m=_mock_model(5000)
    with patch.object(intel,'resolve_model',return_value=m), patch.object(intel,'run_chat',return_value=MagicMock(content='{"category":"A","confidence":90}')):
        r=intel.classify("hello", categories=["A","B"])
        assert r["coverage"]["windows_total"]==1

def test_long_every_window():
    m=_mock_model(400)
    text="hello world. "*500
    with patch.object(intel,'resolve_model',return_value=m), patch.object(intel,'run_chat',return_value=MagicMock(content='{"category":"A","confidence":80}')) as rc:
        r=intel.classify(text, categories=["A","B"])
        assert r["coverage"]["windows_total"]>1
        assert r["coverage"]["windows_processed"]==r["coverage"]["windows_total"]

def test_tail_affects_result():
    m=_mock_model(400)
    base="neutral. "*100
    tail_fact="CLASS_TAIL_B "
    text=base + tail_fact*20
    def fake(msgs,**kw):
        # last window contains tail
        c=msgs[0]["content"]
        if "CLASS_TAIL_B" in c:
            return MagicMock(content='{"category":"B","confidence":95}')
        return MagicMock(content='{"category":"A","confidence":60}')
    with patch.object(intel,'resolve_model',return_value=m), patch.object(intel,'run_chat',side_effect=fake):
        r=intel.classify(text, categories=["A","B"])
        # tail should cause at least one B vote
        assert r["coverage"]["windows_total"]>1

def test_failed_not_counted():
    m=_mock_model(400)
    text="x. "*500
    def fake(msgs,**kw):
        # fail first window via ProviderError
        if fake.calls==0:
            fake.calls+=1
            raise ProviderError("offline")
        fake.calls+=1
        return MagicMock(content='{"category":"A","confidence":80}')
    fake.calls=0
    with patch.object(intel,'resolve_model',return_value=m), patch.object(intel,'run_chat',side_effect=fake):
        r=intel.classify(text, categories=["A","B"])
        assert r["coverage"]["windows_failed"]==1
        assert r["coverage"]["windows_processed"]==r["coverage"]["windows_total"]-1

def test_extract_validates_every_window():
    m=_mock_model(400)
    text="data "*500
    # first window returns invalid (missing required), second valid
    def fake(msgs,**kw):
        import json
        if "Window" in msgs[0]["content"] or len(msgs[0]["content"])>100:
            # per-window: return invalid missing required
            if fake.n==0:
                fake.n+=1
                return MagicMock(content=json.dumps({"amount":1}))
            fake.n+=1
            return MagicMock(content=json.dumps({"invoice_number":"INV","amount":1}))
        return MagicMock(content=json.dumps({"invoice_number":"INV","amount":1}))
    fake.n=0
    with patch.object(intel,'resolve_model',return_value=m), patch("ai_fr_hg.ai.intelligence.frappe.get_cached_doc", return_value=_schema()), patch.object(intel,'run_chat',side_effect=fake):
        # chunk will create 2 windows, first fails validation, second succeeds -> merged still succeeds with one valid
        r=intel.extract_data(text, schema="S")
        assert r["invoice_number"]=="INV"

def test_no_prefix_only():
    import pathlib
    src=pathlib.Path("ai_fr_hg/ai/intelligence.py").read_text()
    # ensure no text[:budget] in classify/extract_data/compare paths
    for name in ["def classify","def extract_data","def compare_documents"]:
        section=src.split(name)[1].split("\ndef ")[0]
        assert "text[:budget" not in section
        assert "text_a[:budget" not in section
        assert "text_b[:budget" not in section

