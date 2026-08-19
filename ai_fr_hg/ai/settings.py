# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Shared helpers for AI Platform Settings values."""

import frappe
from frappe import _
from frappe.utils import flt


def coerce_turn_budget(configured) -> int:
	"""Return seconds for one interactive turn. ``None`` and ``0`` mean unlimited."""
	if configured is None:
		return 0
	try:
		return max(0, int(configured))
	except (TypeError, ValueError):
		return 0


def should_stream_completion(*, requested: bool, enabled: bool, offer_tools) -> bool:
	"""Stream only the final, tool-free completion of an interactive turn."""
	return bool(requested and enabled and not offer_tools)


def normalize_similarity_threshold(value, *, fieldname: str = "Similarity Threshold") -> float:
	"""Return a cosine threshold in ``[0, 1]``.

	Administrators often type a percentage (``25`` meaning 25%). Values
	strictly between 1 and 100 are treated as percentages. ``1`` stays ``1``
	(only exact matches). Anything else is rejected.
	"""
	score = flt(value)
	if 1 < score <= 100:
		score = round(score / 100.0, 6)
	if not 0 <= score <= 1:
		frappe.throw(
			_("{0} must be between 0 and 1 (or 1–100 as a percentage). {1} is not valid.").format(
				_(fieldname), value
			)
		)
	return score
