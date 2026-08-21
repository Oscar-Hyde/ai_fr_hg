"""Extract translatable strings for the ai_fr_hg Frappe app.

Scans DocType JSON, workspace/page/report JSON, ``_()`` calls in Python,
``__()`` calls in JS, and ``_()`` calls in Jinja/HTML templates. Emits a
JSON keyed by source string with the categories and sample source files
where it was seen, plus the canonical ``translations/en.csv``.
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = REPO_ROOT / "ai_fr_hg"

TR_PY = re.compile(
    r"""(?<![A-Za-z_])_\(\s*"""
    r"""(?:"((?:[^"\\]|\\.)*)"|'((?:[^'\\]|\\.)*)')"""
)
TR_JS = re.compile(
    r"""(?<![A-Za-z_])__\(\s*"""
    r"""(?:"((?:[^"\\]|\\.)*)"|`((?:[^`\\]|\\.)*)`|'((?:[^'\\]|\\.)*)')"""
)


def _decode(s: str | None) -> str | None:
    if s is None:
        return s
    if "\\" in s:
        try:
            return bytes(s, "utf-8").decode("unicode_escape")
        except Exception:  # pragma: no cover - safety net
            return s
    return s


class Extractor:
    def __init__(self) -> None:
        self.strings: dict[str, dict[str, set[str]]] = defaultdict(
            lambda: {"categories": set(), "sources": set()}
        )

    def add(self, msg: str | None, category: str, source: str) -> None:
        if not msg or not isinstance(msg, str):
            return
        msg = msg.strip()
        if not msg:
            return
        self.strings[msg]["categories"].add(category)
        self.strings[msg]["sources"].add(source)

    def walk_doctype(self, path: Path) -> None:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return
        if not (isinstance(data, dict) and data.get("doctype") == "DocType"):
            return
        src = str(path.relative_to(REPO_ROOT))
        for f in data.get("fields", []) or []:
            for k in ("label", "description"):
                self.add(f.get(k), f"doctype-{k}", src)
            if f.get("fieldtype") == "Select" and isinstance(f.get("options"), str):
                for opt in f["options"].split("\n"):
                    self.add(opt.strip(), "select-option", src)
        for link in data.get("links", []) or []:
            if isinstance(link, dict):
                self.add(link.get("group"), "dashboard-link-group", src)
        for st in data.get("states", []) or []:
            if isinstance(st, dict):
                self.add(st.get("title"), "workflow-state", src)
        for act in data.get("actions", []) or []:
            if isinstance(act, dict):
                self.add(act.get("label"), "doctype-action", src)

    def walk_workspace(self, path: Path) -> None:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return
        src = str(path.relative_to(REPO_ROOT))
        for k in ("label", "title", "category"):
            self.add(data.get(k), "workspace", src)
        for section in (
            "charts",
            "shortcuts",
            "cards",
            "links",
            "number_cards",
            "quick_lists",
        ):
            for item in data.get(section, []) or []:
                if isinstance(item, dict):
                    for kk in ("label", "chart_name", "shortcut_name"):
                        self.add(item.get(kk), f"workspace-{section}", src)
        content = data.get("content")
        if isinstance(content, str):
            try:
                blocks = json.loads(content)
            except Exception:
                blocks = []
            for block in blocks or []:
                d = block.get("data") if isinstance(block, dict) else None
                if isinstance(d, dict):
                    for kk in ("text", "col", "card_name", "label"):
                        v = d.get(kk)
                        if isinstance(v, str):
                            t = re.sub(r"<[^>]+>", " ", v).strip()
                            self.add(t, "workspace-content", src)

    def walk_generic(self, path: Path, keys: tuple[str, ...], category: str) -> None:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return
        src = str(path.relative_to(REPO_ROOT))
        for k in keys:
            self.add(data.get(k), category, src)

    def walk_py(self, path: Path) -> None:
        try:
            txt = path.read_text(encoding="utf-8")
        except Exception:
            return
        src = str(path.relative_to(REPO_ROOT))
        for m in TR_PY.finditer(txt):
            s = _decode(m.group(1) if m.group(1) is not None else m.group(2))
            self.add(s, "python", src)

    def walk_js(self, path: Path) -> None:
        try:
            txt = path.read_text(encoding="utf-8")
        except Exception:
            return
        src = str(path.relative_to(REPO_ROOT))
        for m in TR_JS.finditer(txt):
            s = next((_decode(g) for g in m.groups() if g is not None), None)
            self.add(s, "js", src)

    def walk_html(self, path: Path) -> None:
        try:
            txt = path.read_text(encoding="utf-8")
        except Exception:
            return
        src = str(path.relative_to(REPO_ROOT))
        for m in TR_PY.finditer(txt):
            s = _decode(m.group(1) if m.group(1) is not None else m.group(2))
            self.add(s, "template", src)

    def run(self) -> list[dict]:
        for path in APP_ROOT.rglob("*.json"):
            p = str(path)
            name = path.name
            if "/doctype/" in p and name == path.parent.name + ".json":
                self.walk_doctype(path)
            elif "/workspace/" in p:
                self.walk_workspace(path)
            elif "/page/" in p and name == path.parent.name + ".json":
                self.walk_generic(path, ("title", "label"), "page")
            elif "/report/" in p and name == path.parent.name + ".json":
                self.walk_generic(path, ("report_name", "label"), "report")
            elif "/notification/" in p and name == path.parent.name + ".json":
                self.walk_generic(path, ("subject",), "notification")
        for path in APP_ROOT.rglob("*.py"):
            self.walk_py(path)
        for path in APP_ROOT.rglob("*.js"):
            self.walk_js(path)
        for path in APP_ROOT.rglob("*.html"):
            self.walk_html(path)

        return [
            {
                "msg": msg,
                "categories": sorted(self.strings[msg]["categories"]),
                "sources": sorted(self.strings[msg]["sources"])[:5],
            }
            for msg in sorted(self.strings, key=lambda s: (s.lower(), s))
        ]


def main() -> None:
    extractor = Extractor()
    out = extractor.run()

    out_json = REPO_ROOT / "scripts" / "translation_keys.json"
    out_json.write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print(f"Extracted {len(out)} unique strings -> {out_json.relative_to(REPO_ROOT)}")

    from collections import Counter

    counter: Counter[str] = Counter()
    for x in out:
        for cat in x["categories"]:
            counter[cat] += 1
    print("Per category:")
    for cat, n in counter.most_common():
        print(f"  {cat}: {n}")


if __name__ == "__main__":
    main()
