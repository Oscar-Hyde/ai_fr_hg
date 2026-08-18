# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Cross-database identity helpers for AI Document organization names."""

from __future__ import annotations

import hashlib
import unicodedata


def organization_name_key(value: str) -> str:
	"""Return a collation-independent, case-insensitive location key.

	Database text collations disagree about accents and other Unicode characters.
	Hashing a normalized Python case-fold makes the compound uniqueness constraint
	behave identically on MariaDB and PostgreSQL while keeping the indexed value
	at a fixed, conservative length.
	"""
	normalized = unicodedata.normalize("NFC", str(value or "").casefold())
	return hashlib.sha256(normalized.encode("utf-8")).hexdigest()
