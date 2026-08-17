# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Provider adapter contract.

Every local runtime (Ollama, llama.cpp, vLLM, LM Studio, ...) is reached through
a subclass of :class:`BaseProvider`. Adding support for a new runtime means
adding one subclass and registering it - no other part of the platform changes.
"""

import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

import frappe
from frappe import _
from frappe.utils import cint, flt

from ai_fr_hg.ai.exceptions import (
	DeadlineExceededError,
	ProviderError,
	ProviderOfflineError,
	ProviderTimeoutError,
)
from ai_fr_hg.utils.network import enforce_local_only


@dataclass
class ChatMessage:
	"""A single message in a chat exchange."""

	role: str
	content: str
	name: str | None = None
	tool_call_id: str | None = None
	tool_calls: list[dict] | None = None
	#: Base64-encoded images for vision models, without a data URI prefix.
	images: list[str] | None = None

	def as_dict(self) -> dict:
		payload: dict[str, Any] = {"role": self.role, "content": self.content or ""}
		if self.name:
			payload["name"] = self.name
		if self.tool_call_id:
			payload["tool_call_id"] = self.tool_call_id
		if self.tool_calls:
			payload["tool_calls"] = self.tool_calls
		if self.images:
			payload["images"] = self.images
		return payload


@dataclass
class CompletionResult:
	"""Normalised response returned by every provider adapter."""

	content: str = ""
	reasoning: str = ""
	tool_calls: list[dict] = field(default_factory=list)
	prompt_tokens: int = 0
	completion_tokens: int = 0
	total_tokens: int = 0
	duration_ms: int = 0
	model: str | None = None
	finish_reason: str | None = None
	raw: dict = field(default_factory=dict)

	@property
	def tokens_per_second(self) -> float:
		if not self.duration_ms or not self.completion_tokens:
			return 0.0
		return round(self.completion_tokens / (self.duration_ms / 1000), 2)


@dataclass
class ModelInfo:
	"""A model as reported by the runtime."""

	name: str
	digest: str | None = None
	size: int = 0
	family: str | None = None
	parameter_size: str | None = None
	quantization: str | None = None
	context_window: int = 0
	modified_at: str | None = None
	raw: dict = field(default_factory=dict)


@dataclass
class HealthStatus:
	"""Result of a provider reachability probe."""

	status: str = "Unknown"
	latency_ms: int = 0
	available_models: int = 0
	error: str | None = None
	details: dict = field(default_factory=dict)

	@property
	def is_online(self) -> bool:
		return self.status == "Online"


class BaseProvider:
	"""Contract implemented by every AI runtime adapter.

	Subclasses must implement :meth:`chat`, :meth:`embed`, :meth:`list_models`
	and :meth:`health_check`. Everything else has a working default.
	"""

	provider_type: str = "Custom"
	supports_streaming: bool = True
	supports_tools: bool = False
	supports_embeddings: bool = True
	supports_model_pull: bool = False

	def __init__(self, provider_doc):
		self.doc = provider_doc
		self.name = provider_doc.name
		self.base_url = (provider_doc.base_url or "").rstrip("/")
		self.timeout = cint(provider_doc.request_timeout) or 120
		self.verify_ssl = bool(provider_doc.verify_ssl)

	# -- helpers ---------------------------------------------------------

	def get_api_key(self) -> str | None:
		try:
			return self.doc.get_password("api_key", raise_exception=False)
		except Exception:
			return None

	def get_headers(self) -> dict:
		headers = {"Content-Type": "application/json", "Accept": "application/json"}
		if api_key := self.get_api_key():
			headers["Authorization"] = f"Bearer {api_key}"
		if extra := self.doc.extra_headers:
			try:
				headers.update(json.loads(extra))
			except (ValueError, TypeError):
				frappe.log_error(
					title="AI Provider: invalid extra headers",
					message=f"Provider {self.name} has malformed Extra Headers JSON.",
				)
		return headers

	def url(self, path: str) -> str:
		return f"{self.base_url}/{path.lstrip('/')}"

	def request(
		self,
		method: str,
		path: str,
		payload: dict | None = None,
		timeout: int | None = None,
		stream: bool = False,
	):
		"""Perform an HTTP call against the runtime, guarded by local-only mode.

		The socket timeout is clamped to whatever remains of the request's
		time budget, so a slow runtime can never hold the connection past the
		deadline the caller promised to honour.
		"""
		import requests

		from ai_fr_hg.ai.deadline import clamp_timeout

		url = self.url(path)
		enforce_local_only(url, _("Provider {0}").format(self.name))

		effective_timeout = timeout or self.timeout
		if (clamped := clamp_timeout(effective_timeout)) is not None:
			if not clamped:
				raise DeadlineExceededError(
					_("Provider {0} was not called: the request time budget is exhausted.").format(self.name)
				)
			effective_timeout = clamped

		try:
			response = requests.request(
				method,
				url,
				json=payload,
				headers=self.get_headers(),
				timeout=effective_timeout,
				verify=self.verify_ssl,
				stream=stream,
			)
		except requests.exceptions.ConnectTimeout as exc:
			raise ProviderTimeoutError(
				_("Provider {0} timed out while connecting to {1}.").format(self.name, url)
			) from exc
		except requests.exceptions.ReadTimeout as exc:
			raise ProviderTimeoutError(
				_("Provider {0} did not respond within {1}s.").format(self.name, round(effective_timeout))
			) from exc
		except requests.exceptions.ConnectionError as exc:
			raise ProviderOfflineError(
				_("Provider {0} is unreachable at {1}. Is the runtime running?").format(self.name, url)
			) from exc
		except requests.exceptions.RequestException as exc:
			raise ProviderError(_("Provider {0} request failed: {1}").format(self.name, exc)) from exc

		if response.status_code >= 400:
			raise ProviderError(
				_("Provider {0} returned HTTP {1}: {2}").format(
					self.name, response.status_code, (response.text or "")[:500]
				)
			)
		return response

	@staticmethod
	def parse_json(response) -> dict:
		try:
			return response.json()
		except ValueError as exc:
			raise ProviderError(_("Provider returned a non-JSON response.")) from exc

	# -- contract --------------------------------------------------------

	def chat(
		self,
		messages: list[ChatMessage],
		model: str,
		options: dict | None = None,
		tools: list[dict] | None = None,
		json_schema: dict | None = None,
	) -> CompletionResult:
		"""Run a chat completion and return a normalised result."""
		raise NotImplementedError

	def stream_chat(
		self,
		messages: list[ChatMessage],
		model: str,
		options: dict | None = None,
		tools: list[dict] | None = None,
	) -> Iterator[str]:
		"""Yield response fragments as they are produced."""
		raise NotImplementedError

	def embed(self, texts: list[str], model: str, options: dict | None = None) -> list[list[float]]:
		"""Return one embedding vector per input text."""
		raise NotImplementedError

	def list_models(self) -> list[ModelInfo]:
		"""Return the models currently installed on the runtime."""
		raise NotImplementedError

	def health_check(self) -> HealthStatus:
		"""Probe the runtime and report reachability."""
		raise NotImplementedError

	def pull_model(self, model: str) -> dict:
		"""Download a model onto the runtime, when the runtime supports it."""
		frappe.throw(_("Provider type {0} does not support pulling models.").format(self.provider_type))

	def delete_model(self, model: str) -> dict:
		frappe.throw(_("Provider type {0} does not support deleting models.").format(self.provider_type))

	def show_model(self, model: str) -> dict:
		"""Return runtime metadata for a single model."""
		return {}

	# -- shared option mapping -------------------------------------------

	def build_options(self, options: dict | None) -> dict:
		"""Normalise generation options into this runtime's vocabulary."""
		return dict(options or {})

	def __repr__(self) -> str:
		return f"<{self.__class__.__name__} {self.name} {self.base_url}>"
