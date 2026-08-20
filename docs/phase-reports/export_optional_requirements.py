#!/usr/bin/env python3
"""Write declared optional extras (except the `all` alias) as a requirements file."""

from __future__ import annotations

import pathlib
import sys
import tomllib


def main() -> int:
	if len(sys.argv) != 2:
		raise SystemExit("usage: export_optional_requirements.py OUTPUT")

	pyproject = pathlib.Path("pyproject.toml")
	metadata = tomllib.loads(pyproject.read_text())
	groups = metadata["project"]["optional-dependencies"]
	requirements = {
		requirement.strip()
		for group, entries in groups.items()
		if group != "all"
		for requirement in entries
	}
	if not requirements or "" in requirements:
		raise SystemExit("Concrete optional dependency declarations are missing or empty")

	pathlib.Path(sys.argv[1]).write_text("\n".join(sorted(requirements, key=str.casefold)) + "\n")
	return 0


if __name__ == "__main__":
	raise SystemExit(main())
