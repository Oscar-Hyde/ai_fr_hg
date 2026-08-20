# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Pure conversation helpers (no Frappe import).

CHAT-01: select the latest N messages, then restore chronological order while
keeping a tool-call group intact when the window would otherwise start on a
Tool row.
"""

from __future__ import annotations

HISTORY_LIMIT = 20
HISTORY_STATUSES = frozenset({"Completed", "Failed", "Draft"})
ACTIVE_TURN_STATUSES = frozenset({"Pending", "Streaming", "Draft"})
TERMINAL_TURN_STATUSES = frozenset({"Completed", "Failed", "Cancelled"})

CANCEL_CACHE_PREFIX = "ai_fr_hg:turn_cancel:"
CANCEL_TTL_SECONDS = 3600


def cancel_cache_key(turn_id: str) -> str:
	return f"{CANCEL_CACHE_PREFIX}{turn_id}"


def window_latest_messages(rows_newest_first: list[dict], limit: int = HISTORY_LIMIT) -> list[dict]:
	"""Return a chronological window of the latest ``limit`` rows.

	``rows_newest_first`` must already be ordered newest → oldest. If the oldest
	selected row is a Tool message, older siblings are included until a
	non-Tool row (the assistant/user that started the tool group) is found.
	"""
	if not rows_newest_first or limit <= 0:
		return []

	selected = list(rows_newest_first[:limit])
	cursor = limit
	while (
		selected and str(selected[-1].get("role") or "").lower() == "tool" and cursor < len(rows_newest_first)
	):
		selected.append(rows_newest_first[cursor])
		cursor += 1
	selected.reverse()
	return selected


def looks_like_conversation_name(value: str | None) -> bool:
	"""Conservative conversation identity check used by route-state parsers."""
	if not value or not isinstance(value, str):
		return False
	text = value.strip()
	if not text or len(text) > 140:
		return False
	if any(char in text for char in ("/", " ", "?", "#")):
		return False
	return True
