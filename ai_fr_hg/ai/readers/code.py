# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Source code reader — recognizes programming files and preserves structure.

Design contract (Part 1 §6.2 "Source Code"):

* **Recognize programming files.** A broad extension registry maps a file to
  its language, so source is never silently treated as anonymous plain text.
* **Preserve structure.** Python is parsed with the standard library ``ast``,
  which yields exact, correct symbol structure with no new dependency. Every
  other language uses a deliberately conservative line-anchored scanner that
  reports only what it can see reliably.
* **Support analysis workflows.** Structure blocks carry the symbol kind, name
  and 1-based line number, so downstream consumers can cite a definition.

**Honesty rule.** The non-Python scanner is a heuristic, not a parser. It
cannot see constructs split across lines and it does not understand nested
scopes or comments in every dialect. Results therefore declare
``structure_fidelity``: ``"parsed"`` for Python, ``"heuristic"`` elsewhere. No
caller should treat a heuristic symbol list as a complete or authoritative
index of the file. Claiming otherwise would be the "declared only" pattern the
architecture decisions exist to prevent.
"""

from __future__ import annotations

import re

from ai_fr_hg.ai.readers.base import BaseReader, ReadResult

#: Extension -> language label. This is the recognition authority for source
#: files; the reader registry maps each of these to `CodeReader`.
LANGUAGES: dict[str, str] = {
	"py": "Python",
	"pyi": "Python",
	"js": "JavaScript",
	"jsx": "JavaScript",
	"mjs": "JavaScript",
	"cjs": "JavaScript",
	"ts": "TypeScript",
	"tsx": "TypeScript",
	"java": "Java",
	"kt": "Kotlin",
	"kts": "Kotlin",
	"go": "Go",
	"rb": "Ruby",
	"rs": "Rust",
	"php": "PHP",
	"c": "C",
	"h": "C",
	"cpp": "C++",
	"cc": "C++",
	"cxx": "C++",
	"hpp": "C++",
	"hh": "C++",
	"cs": "C#",
	"swift": "Swift",
	"scala": "Scala",
	"sh": "Shell",
	"bash": "Shell",
	"zsh": "Shell",
	"ps1": "PowerShell",
	"sql": "SQL",
	"r": "R",
	"lua": "Lua",
	"pl": "Perl",
	"pm": "Perl",
	"dart": "Dart",
	"ex": "Elixir",
	"exs": "Elixir",
	"erl": "Erlang",
	"hs": "Haskell",
	"m": "Objective-C",
	"vb": "Visual Basic",
	"groovy": "Groovy",
	"tf": "Terraform",
}

#: Conservative, line-anchored definition patterns for non-Python languages.
#: Each is linear-time (no nested quantifiers) to stay ReDoS-safe, matching the
#: safety posture of `ai.patterns`.
_HEURISTIC_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
	(re.compile(r"^\s*(?:export\s+)?(?:default\s+)?class\s+([A-Za-z_$][\w$]{0,80})"), "class"),
	(
		re.compile(
			r"^\s*(?:public|private|protected|internal)?\s*(?:static\s+)?(?:struct|interface|enum|trait|protocol)\s+([A-Za-z_$][\w$]{0,80})"
		),
		"type",
	),
	(re.compile(r"^\s*(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_$][\w$]{0,80})"), "function"),
	(re.compile(r"^\s*(?:pub\s+)?(?:async\s+)?fn\s+([A-Za-z_$][\w$]{0,80})"), "function"),
	(re.compile(r"^\s*func\s+(?:\([^)]{0,120}\)\s*)?([A-Za-z_$][\w$]{0,80})"), "function"),
	(re.compile(r"^\s*def\s+([A-Za-z_$][\w$!?]{0,80})"), "function"),
	(re.compile(r"^\s*sub\s+([A-Za-z_$][\w$]{0,80})"), "function"),
	(
		re.compile(
			r"^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]{0,80})\s*=\s*"
			r"(?:async\s+)?(?:function\b|\([^)]{0,120}\)\s*=>|[A-Za-z_$][\w$]{0,80}\s*=>)"
		),
		"function",
	),
	(
		re.compile(
			r"^\s*(?:CREATE|create)\s+(?:OR\s+REPLACE\s+)?(?:TABLE|VIEW|FUNCTION|PROCEDURE|INDEX)\s+(?:IF\s+NOT\s+EXISTS\s+)?[`\"\[]?([A-Za-z_][\w.$]{0,80})"
		),
		"definition",
	),
)

#: Import/include statements worth recording as dependency evidence. Ordered:
#: the ES-module form is tried before the bare `import x` form so that
#: `import { a } from "mod"` records the module, not the binding list.
_IMPORT_PATTERNS: tuple[re.Pattern[str], ...] = (
	re.compile(r"""^\s*import\s.{0,200}?\sfrom\s+['"]([^'"]{1,120})['"]"""),
	re.compile(r"""^\s*.{0,120}?\b(?:import|require)\s*\(\s*['"]([^'"]{1,120})['"]\s*\)"""),
	re.compile(r"""^\s*import\s+['"]([^'"]{1,120})['"]"""),
	re.compile(r"^\s*from\s+([\w.]{1,120})\s+import\b"),
	re.compile(r"^\s*#include\s*[<\"]([^>\"]{1,120})[>\"]"),
	re.compile(r"^\s*import\s+([\w.*]{1,120})\s*;?\s*$"),
	re.compile(r"""^\s*(?:use|require|require_relative)\s+['"]?([\w.:/\\-]{1,120})['"]?\s*;?\s*$"""),
)

#: Bounds keep a generated or vendored file from producing unbounded evidence.
MAX_SYMBOLS = 500
MAX_IMPORTS = 200


def language_for(filename: str) -> str | None:
	"""Language label for a filename, or None when it is not source code."""
	extension = filename.rsplit(".", 1)[-1].lower() if filename and "." in filename else ""
	return LANGUAGES.get(extension)


class CodeReader(BaseReader):
	"""Read a source file, preserving its symbol structure."""

	label = "Source Code"
	version = "1.0"

	def read(self, content: bytes, filename: str) -> ReadResult:
		raw = self.decode(content)
		language = language_for(filename) or "Unknown"
		warnings: list[str] = []

		if language == "Python":
			structure, imports, fidelity, parse_warning = self._read_python(raw)
			if parse_warning:
				warnings.append(parse_warning)
		else:
			structure, imports = self._read_heuristic(raw)
			fidelity = "heuristic"

		if len(structure) >= MAX_SYMBOLS:
			warnings.append(f"Only the first {MAX_SYMBOLS} symbols were recorded; the file contains more.")

		lines = raw.splitlines()
		metadata = {
			"format": "code",
			"language": language,
			"line_count": len(lines),
			"symbol_count": len(structure),
			"import_count": len(imports),
			# Never let a consumer mistake heuristics for a parse.
			"structure_fidelity": fidelity,
			"imports": imports[:MAX_IMPORTS],
		}

		return ReadResult(
			# Source code is returned verbatim: `clean()` collapses runs of
			# whitespace, which would destroy indentation and therefore meaning
			# in indentation-sensitive languages.
			text=raw.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", ""),
			page_count=1,
			metadata=metadata,
			warnings=warnings,
			structure=structure,
		)

	# -- Python: exact structure via the standard library --------------------

	def _read_python(self, raw: str) -> tuple[list[dict], list[str], str, str | None]:
		"""Parse Python with `ast`, falling back to heuristics on syntax errors."""
		import ast

		try:
			tree = ast.parse(raw)
		except SyntaxError as exc:
			structure, imports = self._read_heuristic(raw)
			return (
				structure,
				imports,
				"heuristic",
				f"Python file could not be parsed ({exc.msg} at line {exc.lineno}); "
				"structure was recovered heuristically.",
			)
		except (ValueError, RecursionError) as exc:
			structure, imports = self._read_heuristic(raw)
			return structure, imports, "heuristic", f"Python file could not be parsed: {exc}"

		structure: list[dict] = []
		imports: list[str] = []

		def qualified(stack: list[str], name: str) -> str:
			return ".".join([*stack, name])

		def walk(node, stack: list[str]) -> None:
			for child in ast.iter_child_nodes(node):
				if len(structure) >= MAX_SYMBOLS:
					return
				if isinstance(child, ast.ClassDef):
					structure.append(
						{
							"kind": "class",
							"name": qualified(stack, child.name),
							"line": child.lineno,
							"decorators": len(child.decorator_list),
						}
					)
					walk(child, [*stack, child.name])
				elif isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
					structure.append(
						{
							"kind": "method" if stack else "function",
							"name": qualified(stack, child.name),
							"line": child.lineno,
							"is_async": isinstance(child, ast.AsyncFunctionDef),
							"arguments": [argument.arg for argument in child.args.args][:20],
						}
					)
					walk(child, [*stack, child.name])
				elif isinstance(child, ast.Import):
					for alias in child.names:
						if len(imports) < MAX_IMPORTS:
							imports.append(alias.name)
				elif isinstance(child, ast.ImportFrom):
					module = child.module or ""
					for alias in child.names:
						if len(imports) < MAX_IMPORTS:
							imports.append(f"{module}.{alias.name}" if module else alias.name)
				else:
					walk(child, stack)

		walk(tree, [])
		if docstring := ast.get_docstring(tree):
			structure.insert(0, {"kind": "module_docstring", "text": docstring[:500], "line": 1})
		return structure, imports, "parsed", None

	# -- Everything else: conservative line scanning -------------------------

	def _read_heuristic(self, raw: str) -> tuple[list[dict], list[str]]:
		structure: list[dict] = []
		imports: list[str] = []
		for number, line in enumerate(raw.splitlines(), start=1):
			if len(line) > 500:
				continue
			if len(structure) < MAX_SYMBOLS:
				for pattern, kind in _HEURISTIC_PATTERNS:
					match = pattern.match(line)
					if match:
						structure.append({"kind": kind, "name": match.group(1)[:120], "line": number})
						break
			if len(imports) < MAX_IMPORTS:
				for pattern in _IMPORT_PATTERNS:
					match = pattern.match(line)
					if match:
						value = match.group(1).strip().rstrip(";").strip("'\"")
						if value:
							imports.append(value[:120])
						break
		return structure, imports
