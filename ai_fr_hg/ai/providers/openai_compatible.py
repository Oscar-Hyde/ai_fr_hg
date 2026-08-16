# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Adapter for any local runtime exposing an OpenAI-compatible REST API.

Covers llama.cpp server, vLLM, LM Studio, Text Generation WebUI and Ollama's
own /v1 compatibility layer. Only the local base URL differs between them.
"""

import json
import time
from collections.abc import Iterator

from frappe.utils import cint, flt

from ai_fr_hg.ai.exceptions import ProviderError
from ai_fr_hg.ai.providers.base import (
	BaseProvider,
	ChatMessage,
	CompletionResult,
	HealthStatus,
	ModelInfo,
)

OPTION_MAP = {
	"temperature": "temperature",
	"top_p": "top_p",
	"max_tokens": "max_tokens",
	"repeat_penalty": "frequency_penalty",
	"seed": "seed",
	"stop": "stop",
}


class OpenAICompatibleProvider(BaseProvider):
	provider_type = "OpenAI Compatible"
	supports_streaming = True
	supports_tools = True
	supports_embeddings = True
	supports_model_pull = False

	#: Some runtimes are served at the root, others under /v1.
	def url(self, path: str) -> str:
		base = self.base_url
		path = path.lstrip("/")
		if not base.endswith("/v1") and path.startswith("v1/"):
			return f"{base}/{path}"
		if base.endswith("/v1") and path.startswith("v1/"):
			return f"{base}/{path[3:]}"
		return f"{base}/{path}"

	def build_options(self, options: dict | None) -> dict:
		options = options or {}
		mapped: dict = {}
		for key, value in options.items():
			if value in (None, "", []):
				continue
			target = OPTION_MAP.get(key)
			if not target:
				continue
			if target == "max_tokens" or target == "seed":
				mapped[target] = cint(value)
			elif target == "stop":
				mapped[target] = value if isinstance(value, list) else [value]
			else:
				mapped[target] = flt(value)
		return mapped

	# -- chat ------------------------------------------------------------

	@staticmethod
	def _to_openai_message(message: ChatMessage) -> dict:
		"""Render a message in OpenAI's wire format.

		Images use the multimodal `content` parts array rather than Ollama's
		flat `images` list, so vision models work across both provider styles.
		"""
		payload = message.as_dict()
		images = payload.pop("images", None)
		if not images:
			return payload

		parts: list[dict] = []
		if payload.get("content"):
			parts.append({"type": "text", "text": payload["content"]})
		for image in images:
			parts.append(
				{
					"type": "image_url",
					"image_url": {"url": f"data:image/png;base64,{image}"},
				}
			)
		payload["content"] = parts
		return payload

	def chat(
		self,
		messages: list[ChatMessage],
		model: str,
		options: dict | None = None,
		tools: list[dict] | None = None,
		json_schema: dict | None = None,
	) -> CompletionResult:
		payload = {
			"model": model,
			"messages": [self._to_openai_message(m) for m in messages],
			"stream": False,
			**self.build_options(options),
		}
		if tools:
			payload["tools"] = [{"type": "function", "function": tool} for tool in tools]
			payload["tool_choice"] = "auto"
		if json_schema:
			payload["response_format"] = {
				"type": "json_schema",
				"json_schema": {"name": "response", "schema": json_schema, "strict": True},
			}
		elif (options or {}).get("json_mode"):
			payload["response_format"] = {"type": "json_object"}

		started = time.monotonic()
		response = self.request("POST", "v1/chat/completions", payload)
		data = self.parse_json(response)
		duration_ms = int((time.monotonic() - started) * 1000)

		choices = data.get("choices") or []
		if not choices:
			raise ProviderError("Runtime returned no choices.")
		message = choices[0].get("message") or {}
		usage = data.get("usage") or {}

		return CompletionResult(
			content=message.get("content") or "",
			reasoning=message.get("reasoning_content") or message.get("reasoning") or "",
			tool_calls=self._normalise_tool_calls(message.get("tool_calls")),
			prompt_tokens=cint(usage.get("prompt_tokens")),
			completion_tokens=cint(usage.get("completion_tokens")),
			total_tokens=cint(usage.get("total_tokens")),
			duration_ms=duration_ms,
			model=data.get("model") or model,
			finish_reason=choices[0].get("finish_reason"),
			raw=data,
		)

	def stream_chat(
		self,
		messages: list[ChatMessage],
		model: str,
		options: dict | None = None,
		tools: list[dict] | None = None,
	) -> Iterator[str]:
		payload = {
			"model": model,
			"messages": [m.as_dict() for m in messages],
			"stream": True,
			**self.build_options(options),
		}
		if tools:
			payload["tools"] = [{"type": "function", "function": tool} for tool in tools]

		response = self.request("POST", "v1/chat/completions", payload, stream=True)
		for line in response.iter_lines(decode_unicode=True):
			if not line or not line.startswith("data:"):
				continue
			chunk = line[5:].strip()
			if chunk == "[DONE]":
				break
			try:
				parsed = json.loads(chunk)
			except ValueError:
				continue
			for choice in parsed.get("choices") or []:
				if fragment := (choice.get("delta") or {}).get("content"):
					yield fragment

	@staticmethod
	def _normalise_tool_calls(tool_calls) -> list[dict]:
		normalised = []
		for index, call in enumerate(tool_calls or []):
			function = call.get("function") or {}
			arguments = function.get("arguments")
			if isinstance(arguments, str):
				try:
					arguments = json.loads(arguments)
				except ValueError:
					arguments = {"_raw": arguments}
			normalised.append(
				{
					"id": call.get("id") or f"call_{index}",
					"name": function.get("name"),
					"arguments": arguments or {},
				}
			)
		return normalised

	# -- embeddings ------------------------------------------------------

	def embed(self, texts: list[str], model: str, options: dict | None = None) -> list[list[float]]:
		if not texts:
			return []
		response = self.request("POST", "v1/embeddings", {"model": model, "input": texts})
		data = self.parse_json(response)
		rows = sorted(data.get("data") or [], key=lambda row: cint(row.get("index")))
		if not rows:
			raise ProviderError(f"Runtime returned no embeddings for model {model}.")
		return [[flt(v) for v in row.get("embedding") or []] for row in rows]

	# -- models ------------------------------------------------------------

	def list_models(self) -> list[ModelInfo]:
		response = self.request("GET", "v1/models", timeout=min(self.timeout, 30))
		data = self.parse_json(response)
		models = []
		for entry in data.get("data") or []:
			models.append(
				ModelInfo(
					name=entry.get("id"),
					family=entry.get("owned_by"),
					context_window=cint(entry.get("context_length")),
					raw=entry,
				)
			)
		return models

	def health_check(self) -> HealthStatus:
		started = time.monotonic()
		try:
			models = self.list_models()
		except Exception as exc:
			return HealthStatus(status="Offline", error=str(exc)[:500])

		return HealthStatus(
			status="Online" if models else "Degraded",
			latency_ms=int((time.monotonic() - started) * 1000),
			available_models=len(models),
			error=None if models else "Runtime reachable but exposes no models.",
			details={"models": [m.name for m in models][:50]},
		)


class LlamaCppProvider(OpenAICompatibleProvider):
	"""llama.cpp `llama-server`, which serves the OpenAI API at the root."""

	provider_type = "Llama.cpp"

	def health_check(self) -> HealthStatus:
		started = time.monotonic()
		try:
			response = self.request("GET", "health", timeout=min(self.timeout, 15))
			payload = self.parse_json(response)
		except Exception:
			return super().health_check()

		state = (payload.get("status") or "").lower()
		status = "Online" if state in ("ok", "no slot available") else "Degraded"
		return HealthStatus(
			status=status,
			latency_ms=int((time.monotonic() - started) * 1000),
			available_models=1,
			details=payload,
		)


class VLLMProvider(OpenAICompatibleProvider):
	"""vLLM's OpenAI-compatible server."""

	provider_type = "vLLM"


class LMStudioProvider(OpenAICompatibleProvider):
	"""LM Studio's local server."""

	provider_type = "LM Studio"


class TextGenWebUIProvider(OpenAICompatibleProvider):
	"""oobabooga Text Generation WebUI OpenAI extension."""

	provider_type = "Text Generation WebUI"
