# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class AIProvider(Document):
	_DOCTYPE_NAME = "AI Provider"

	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		adapter_path: DF.Data | None
		api_key: DF.Password | None
		available_model_count: DF.Int
		base_url: DF.Data
		consecutive_failures: DF.Int
		description: DF.SmallText | None
		enabled: DF.Check
		extra_headers: DF.Code | None
		is_default: DF.Check
		last_error: DF.SmallText | None
		last_health_check: DF.Datetime | None
		latency_ms: DF.Int
		max_concurrent_requests: DF.Int
		model_prefix: DF.Data | None
		priority: DF.Int
		provider_name: DF.Data
		provider_type: DF.Literal[
			"Ollama", "OpenAI Compatible", "Llama.cpp", "vLLM", "LM Studio", "Text Generation WebUI", "Custom"
		]
		rate_limit_per_minute: DF.Int
		request_timeout: DF.Int
		status: DF.Literal["Unknown", "Online", "Degraded", "Offline"]
		verify_ssl: DF.Check
	# end: auto-generated types

	def validate(self):
		self.validate_url()
		self.validate_default()
		self.validate_headers()

	def validate_url(self):
		from urllib.parse import urlparse

		from ai_fr_hg.utils.network import enforce_local_only

		parsed = urlparse(self.base_url or "")
		if parsed.scheme not in ("http", "https"):
			frappe.throw(_("Base URL must start with http:// or https://"))
		if not parsed.hostname:
			frappe.throw(_("Base URL must include a hostname."))

		self.base_url = (self.base_url or "").rstrip("/")
		enforce_local_only(self.base_url, _("Provider {0}").format(self.name or self.provider_name))

	def validate_default(self):
		"""Only one provider may be the default."""
		if not self.is_default:
			return
		frappe.db.set_value(
			"AI Provider",
			{"is_default": 1, "name": ("!=", self.name)},
			"is_default",
			0,
			update_modified=False,
		)

	def validate_headers(self):
		if not self.extra_headers:
			return
		import json

		try:
			parsed = json.loads(self.extra_headers)
		except ValueError as exc:
			frappe.throw(_("Extra Headers must be valid JSON: {0}").format(exc))
		if not isinstance(parsed, dict):
			frappe.throw(_("Extra Headers must be a JSON object."))

	def on_update(self):
		from ai_fr_hg.utils.network import clear_resolution_cache

		clear_resolution_cache()

	def on_trash(self):
		if models := frappe.get_all("AI Model", filters={"provider": self.name}, pluck="name"):
			frappe.throw(
				_("Cannot delete {0}: it is used by {1} model(s), including {2}.").format(
					self.name, len(models), models[0]
				)
			)

	@frappe.whitelist()
	def test_connection(self) -> dict:
		"""Probe this provider and refresh its status."""
		from ai_fr_hg.ai.monitoring import check_provider_health

		return check_provider_health(self.name)

	@frappe.whitelist()
	def discover_models(self) -> dict:
		"""Register every model this runtime reports."""
		from ai_fr_hg.ai.monitoring import sync_provider_models

		return sync_provider_models(self.name)
