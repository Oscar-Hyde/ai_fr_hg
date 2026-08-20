"""ING-05 unit tests: structured warning contract and coercion."""
from ai_fr_hg.ai.readers.base import coerce_warnings, StructuredWarning

def test_coerce_legacy_string():
    w = coerce_warnings(["Sheet truncated"], reader="XlsxReader", source_file="a.xlsx")[0]
    assert w["code"] == "legacy"
    assert w["severity"] == "warning"
    assert w["reader"] == "XlsxReader"
    assert w["source_file"] == "a.xlsx"
    assert "Sheet truncated" in w["message"]
    assert "timestamp" in w
    assert w["stage"] == "extraction"

def test_coerce_preserves_dict():
    raw = [{"code":"truncated","category":"truncation","severity":"warning","reader":"CSVReader","source_file":"a.csv","location":"row 20000","message":"truncated","details":{"max":20000},"timestamp":"2026-01-01T00:00:00+00:00","stage":"extraction"}]
    assert coerce_warnings(raw)[0]["code"] == "truncated"

def test_structured_warning_dict():
    sw = StructuredWarning(code="archive_member", category="archive", severity="warning", reader="DocxReader", source_file="a.docx", location="member word/document.xml", message="member too large", details={"size": 60000000})
    d = sw.as_dict()
    assert d["code"] == "archive_member" and d["details"]["size"] == 60000000
