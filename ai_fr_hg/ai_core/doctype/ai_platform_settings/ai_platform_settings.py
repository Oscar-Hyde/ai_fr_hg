# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, flt


class AIPlatformSettings(Document):
	_DOCTYPE_NAME = "AI Platform Settings"

	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		alert_on_provider_offline: DF.Check
		alert_recipients: DF.SmallText | None
		allowed_hosts: DF.SmallText | None
		audit_log_retention_days: DF.Int
		auto_backup_enabled: DF.Check
		auto_embed_on_ingest: DF.Check
		auto_process_documents: DF.Check
		default_agent: DF.Link | None
		default_chat_model: DF.Link | None
		default_chunk_overlap: DF.Int
		default_chunk_size: DF.Int
		default_context_window: DF.Int
		default_embedding_model: DF.Link | None
		default_max_tokens: DF.Int
		default_system_prompt: DF.Code | None
		default_temperature: DF.Float
		default_top_k: DF.Int
		default_top_p: DF.Float
		default_vision_model: DF.Link | None
		enable_hybrid_search: DF.Check
		encrypt_documents: DF.Check
		execution_log_retention_days: DF.Int
		health_check_enabled: DF.Check
		health_check_interval_minutes: DF.Int
		health_log_retention_days: DF.Int
		log_prompts: DF.Check
		log_responses: DF.Check
		max_context_characters: DF.Int
		max_document_size_mb: DF.Int
		max_requests_per_user_per_hour: DF.Int
		max_retries: DF.Int
		max_tokens_per_user_per_day: DF.Int
		ocr_enabled: DF.Check
		offline_mode: DF.Check
		platform_enabled: DF.Check
		processing_queue: DF.Literal["default", "short", "long"]
		redact_patterns: DF.SmallText | None
		request_timeout: DF.Int
		require_tool_approval: DF.Check
		similarity_threshold: DF.Float
		storage_folder: DF.Data | None
		streaming_enabled: DF.Check
	# end: auto-generated types

	def validate(self):
		self.validate_intervals()
		self.validate_model_types()
		self.validate_redaction_patterns()

	def validate_intervals(self):
		if cint(self.request_timeout) < 5:
			frappe.throw(_("Request Timeout must be at least 5 seconds."))
		if cint(self.default_chunk_overlap) >= cint(self.default_chunk_size):
			frappe.throw(_("Chunk Overlap must be smaller than Chunk Size."))
		if not 0 <= flt(self.similarity_threshold) <= 1:
			frappe.throw(_("Similarity Threshold must be between 0 and 1."))
		if cint(self.health_check_interval_minutes) < 1:
			self.health_check_interval_minutes = 15

	def validate_model_types(self):
		"""A model selected as a default must actually be of that type."""
		expected = {
			"default_chat_model": ("Chat", "Vision"),
			"default_embedding_model": ("Embedding",),
			"default_vision_model": ("Vision",),
		}
		for fieldname, allowed in expected.items():
			if not self.get(fieldname):
				continue
			model_type = frappe.db.get_value("AI Model", self.get(fieldname), "model_type")
			if model_type not in allowed:
				frappe.throw(
					_("{0} must be a {1} model, but {2} is a {3} model.").format(
						_(self.meta.get_label(fieldname)),
						" or ".join(allowed),
						self.get(fieldname),
						model_type,
					)
				)

	def validate_redaction_patterns(self):
		import re as _re

		for line in (self.redact_patterns or "").splitlines():
			if not line.strip():
				continue
			try:
				_re.compile(line.strip())
			except _re.error as exc:
				frappe.throw(_("Invalid redaction pattern {0}: {1}").format(line, exc))

	def on_update(self):
		from ai_fr_hg.ai.logging import clear_pattern_cache
		from ai_fr_hg.utils.network import clear_resolution_cache

		clear_pattern_cache()
		clear_resolution_cache()
		frappe.clear_cache(doctype="AI Platform Settings")
