# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class AIExecutionLog(Document):
	_DOCTYPE_NAME = "AI Execution Log"

	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		completion_tokens: DF.Int
		conversation: DF.Link | None
		duration_ms: DF.Int
		error_message: DF.SmallText | None
		finished_at: DF.Datetime | None
		model: DF.Link | None
		operation: DF.Literal[
			"Chat",
			"Completion",
			"Embedding",
			"Summarize",
			"Classify",
			"Extract",
			"Compare",
			"Rerank",
			"Tool Call",
			"Health Check",
		]
		pipeline_run: DF.Link | None
		prompt_text: DF.LongText | None
		prompt_tokens: DF.Int
		provider: DF.Link | None
		queue_time_ms: DF.Int
		reference_doctype: DF.Link | None
		reference_name: DF.DynamicLink | None
		request_payload: DF.Code | None
		response_payload: DF.Code | None
		response_text: DF.LongText | None
		retry_count: DF.Int
		started_at: DF.Datetime | None
		status: DF.Literal["Queued", "Running", "Success", "Failed", "Cancelled"]
		tokens_per_second: DF.Float
		total_tokens: DF.Int
		traceback: DF.Code | None
		user: DF.Link | None
	# end: auto-generated types
