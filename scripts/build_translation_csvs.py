"""Build ``translations/en.csv``, ``ar.csv`` and ``he.csv`` from the extracted
key list and a per-language translations JSON.

Frappe reads each row as ``source, translation, context`` (context optional).
We deliberately emit two columns (``source`` and ``translation``) so a missing
translation is unambiguous: an entry that isn't yet localised is simply absent
from ``ar.csv`` / ``he.csv``.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
KEYS_JSON = REPO_ROOT / "scripts" / "translation_keys.json"
TR_DIR = REPO_ROOT / "ai_fr_hg" / "translations"


def write_csv(path: Path, rows: list[tuple[str, str]]) -> None:
	path.parent.mkdir(parents=True, exist_ok=True)
	with path.open("w", encoding="utf-8", newline="") as f:
		w = csv.writer(f, lineterminator="\n")
		for row in rows:
			w.writerow(row)


def load_language(code: str) -> dict[str, str]:
	lang_file = REPO_ROOT / "scripts" / "translations" / f"{code}.json"
	if not lang_file.exists():
		return {}
	return json.loads(lang_file.read_text(encoding="utf-8"))


def main() -> None:
	keys = json.loads(KEYS_JSON.read_text(encoding="utf-8"))
	sources = [x["msg"] for x in keys]

	# en.csv: identity mapping (source == translation) so Frappe treats English
	# as a first-class registered locale.
	en_rows = [(s, s) for s in sources]
	write_csv(TR_DIR / "en.csv", en_rows)

	for code in ("ar", "he"):
		mapping = load_language(code)
		rows = [(s, mapping[s]) for s in sources if s in mapping]
		write_csv(TR_DIR / f"{code}.csv", rows)
		covered = len(rows)
		total = len(sources)
		pct = (100 * covered) // max(total, 1)
		print(f"{code}.csv: {covered}/{total} strings ({pct}%)")

	print(f"en.csv: {len(en_rows)} strings")


if __name__ == "__main__":
	main()
