# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Normalize controls that previously implied unsupported behavior.

The patch deliberately preserves documents, models, and model-version child
rows. The encryption flag never encrypted stored data, so resetting it removes
a false security signal. Legacy reranker model records remain available for
audit but cannot be selected for execution.
"""

import frappe

_UNSUPPORTED_RERANKER_MESSAGE = (
	"Disabled during upgrade: reranking has no supported execution path in this release."
)


def execute() -> None:
	frappe.db.set_single_value("AI Platform Settings", "encrypt_documents", 0)

	for name in frappe.get_all(
		"AI Model",
		filters={"model_type": "Reranker"},
		pluck="name",
	):
		frappe.db.set_value(
			"AI Model",
			name,
			{
				"enabled": 0,
				"is_default": 0,
				"last_error": _UNSUPPORTED_RERANKER_MESSAGE,
			},
			update_modified=False,
		)
