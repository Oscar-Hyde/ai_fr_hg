# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, flt, now_datetime

from ai_fr_hg.utils.db import safe_set_value


class AIModel(Document):
	_DOCTYPE_NAME = "AI Model"

	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		from ai_fr_hg.ai_core.doctype.ai_model_parameter.ai_model_parameter import AIModelParameter
		from ai_fr_hg.ai_core.doctype.ai_model_version.ai_model_version import AIModelVersion

		average_latency_ms: DF.Float
		context_window: DF.Int
		digest: DF.Data | None
		embedding_dimensions: DF.Int
		enabled: DF.Check
		family: DF.Data | None
		gpu_layers: DF.Int
		is_default: DF.Check
		keep_alive: DF.Data | None
		last_checked: DF.Datetime | None
		last_error: DF.SmallText | None
		max_concurrent_requests: DF.Int
		max_tokens: DF.Int
		model_label: DF.Data
		model_name: DF.Data
		model_type: DF.Literal["Chat", "Completion", "Embedding", "Vision"]
		num_batch: DF.Int
		num_ctx_override: DF.Int
		num_threads: DF.Int
		parameter_size: DF.Data | None
		parameters: DF.Table[AIModelParameter]
		provider: DF.Link
		quantization: DF.Data | None
		repeat_penalty: DF.Float
		size_bytes: DF.Float
		status: DF.Literal["Unknown", "Available", "Missing", "Error"]
		stop_sequences: DF.SmallText | None
		supports_json_mode: DF.Check
		supports_streaming: DF.Check
		supports_tools: DF.Check
		supports_vision: DF.Check
		system_prompt: DF.Code | None
		temperature: DF.Float
		top_k: DF.Int
		top_p: DF.Float
		total_requests: DF.Int
		total_tokens: DF.Float
		versions: DF.Table[AIModelVersion]
	# end: auto-generated types

	def validate(self):
		self.validate_supported_type()
		self.validate_defaults()
		self.validate_generation()
		self.validate_embedding()

	def validate_supported_type(self):
		"""Do not expose model roles without an executable engine contract."""
		supported = {"Chat", "Completion", "Embedding", "Vision"}
		if self.model_type not in supported:
			frappe.throw(
				_("Model Type {0} is not supported by the current execution engine.").format(
					self.model_type or _("(empty)")
				),
				frappe.ValidationError,
			)

	def validate_defaults(self):
		"""Only one model per type may be the default."""
		if not self.is_default:
			return
		frappe.db.sql(
			"""
			update `tabAI Model` set is_default = 0
			where model_type = %s and name != %s
			""",
			(self.model_type, self.name),
		)

	def validate_generation(self):
		if not 0 <= flt(self.temperature) <= 2:
			frappe.throw(_("Temperature must be between 0 and 2."))
		if not 0 <= flt(self.top_p) <= 1:
			frappe.throw(_("Top P must be between 0 and 1."))
		if cint(self.max_tokens) < 0:
			frappe.throw(_("Max Tokens cannot be negative."))
		if cint(self.context_window) < 512:
			frappe.throw(_("Context Window must be at least 512."))

	def validate_embedding(self):
		"""Changing an embedding model invalidates the vectors made with it."""
		if self.model_type != "Embedding" or self.is_new():
			return

		before = self.get_doc_before_save()
		if not before or before.model_name == self.model_name:
			return

		affected = frappe.db.count("AI Document Chunk", {"embedding_model": self.name})
		if affected:
			frappe.msgprint(
				_(
					"{0} chunks were embedded with the previous model. "
					"Re-index the affected knowledge bases so search stays accurate."
				).format(affected),
				title=_("Re-indexing Required"),
				indicator="orange",
			)

	def on_trash(self):
		if chunks := frappe.db.count("AI Document Chunk", {"embedding_model": self.name}):
			frappe.throw(
				_("Cannot delete {0}: {1} indexed chunks were embedded with it.").format(self.name, chunks)
			)

	@frappe.whitelist()
	def test_model(self) -> dict:
		"""Send a short probe prompt to this model."""
		from ai_fr_hg.api.admin import test_model

		return test_model(self.name)

	@frappe.whitelist()
	def refresh_metadata(self) -> dict:
		"""Re-read this model's metadata from the runtime."""
		from ai_fr_hg.ai.providers import get_provider

		adapter = get_provider(self.provider)
		info = adapter.show_model(self.model_name) or {}
		details = info.get("details") or {}

		safe_set_value(
			"AI Model",
			self.name,
			{
				"family": details.get("family") or self.family,
				"parameter_size": details.get("parameter_size") or self.parameter_size,
				"quantization": details.get("quantization_level") or self.quantization,
				"status": "Available",
				"last_checked": now_datetime(),
			},
			update_modified=False,
		)
		return {"model": self.name, "details": details}
