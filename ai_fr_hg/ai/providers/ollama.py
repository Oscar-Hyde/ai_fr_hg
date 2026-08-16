# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Adapter for the Ollama local runtime (https://ollama.com).

Ollama is the platform's primary engine. It exposes a native REST API on
http://localhost:11434 and requires no API key.
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

# Frappe/platform option name -> Ollama option name
OPTION_MAP = {
	"temperature": "temperature",
	"top_p": "top_p",
	"top_k": "top_k",
	"max_tokens": "num_predict",
	"repeat_penalty": "repeat_penalty",
	"context_window": "num_ctx",
	"num_ctx": "num_ctx",
	"num_threads": "num_thread",
	"num_batch": "num_batch",
	"gpu_layers": "num_gpu",
	"seed": "seed",
	"stop": "stop",
}


class OllamaProvider(BaseProvider):
	provider_type = "Ollama"
	supports_streaming = True
	supports_tools = True
	supports_embeddings = True
	supports_model_pull = True

	def build_options(self, options: dict | None) -> dict:
		options = options or {}
		mapped: dict = {}
		for key, value in options.items():
			if value in (None, "", []):
				continue
			target = OPTION_MAP.get(key)
			if not target:
				continue
			if target in ("num_predict", "num_ctx", "num_thread", "num_batch", "num_gpu", "top_k", "seed"):
				mapped[target] = cint(value)
			elif target == "stop":
				mapped[target] = value if isinstance(value, list) else [value]
			else:
				mapped[target] = flt(value)
		return mapped

	# -- chat ------------------------------------------------------------

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
			"messages": [m.as_dict() for m in messages],
			"stream": False,
			"options": self.build_options(options),
		}
		if keep_alive := (options or {}).get("keep_alive"):
			payload["keep_alive"] = keep_alive
		if tools:
			payload["tools"] = tools
		if json_schema:
			payload["format"] = json_schema
		elif (options or {}).get("json_mode"):
			payload["format"] = "json"

		started = time.monotonic()
		response = self.request("POST", "/api/chat", payload)
		data = self.parse_json(response)
		duration_ms = int((time.monotonic() - started) * 1000)

		message = data.get("message") or {}
		prompt_tokens = cint(data.get("prompt_eval_count"))
		completion_tokens = cint(data.get("eval_count"))

		return CompletionResult(
			content=message.get("content") or "",
			reasoning=message.get("thinking") or "",
			tool_calls=self._normalise_tool_calls(message.get("tool_calls")),
			prompt_tokens=prompt_tokens,
			completion_tokens=completion_tokens,
			total_tokens=prompt_tokens + completion_tokens,
			duration_ms=duration_ms,
			model=data.get("model") or model,
			finish_reason=data.get("done_reason"),
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
			"options": self.build_options(options),
		}
		if tools:
			payload["tools"] = tools

		response = self.request("POST", "/api/chat", payload, stream=True)
		for line in response.iter_lines(decode_unicode=True):
			if not line:
				continue
			try:
				chunk = json.loads(line)
			except ValueError:
				continue
			if chunk.get("done"):
				break
			if fragment := (chunk.get("message") or {}).get("content"):
				yield fragment

	@staticmethod
	def _normalise_tool_calls(tool_calls) -> list[dict]:
		"""Convert Ollama tool calls into the platform's canonical shape."""
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
		payload = {"model": model, "input": texts}
		if opts := self.build_options(options):
			payload["options"] = opts

		response = self.request("POST", "/api/embed", payload)
		data = self.parse_json(response)
		embeddings = data.get("embeddings")

		if embeddings is None and data.get("embedding") is not None:
			# Older single-input endpoint shape.
			embeddings = [data["embedding"]]
		if not embeddings:
			raise ProviderError(f"Ollama returned no embeddings for model {model}.")
		return [[flt(v) for v in vector] for vector in embeddings]

	# -- model management -------------------------------------------------

	def list_models(self) -> list[ModelInfo]:
		response = self.request("GET", "/api/tags", timeout=min(self.timeout, 30))
		data = self.parse_json(response)
		models = []
		for entry in data.get("models") or []:
			details = entry.get("details") or {}
			models.append(
				ModelInfo(
					name=entry.get("name") or entry.get("model"),
					digest=entry.get("digest"),
					size=cint(entry.get("size")),
					family=details.get("family"),
					parameter_size=details.get("parameter_size"),
					quantization=details.get("quantization_level"),
					modified_at=entry.get("modified_at"),
					raw=entry,
				)
			)
		return models

	def show_model(self, model: str) -> dict:
		response = self.request("POST", "/api/show", {"model": model}, timeout=min(self.timeout, 30))
		return self.parse_json(response)

	def pull_model(self, model: str) -> dict:
		"""Stream a model download and return the final status line."""
		response = self.request(
			"POST", "/api/pull", {"model": model, "stream": True}, timeout=self.timeout * 10, stream=True
		)
		last: dict = {}
		for line in response.iter_lines(decode_unicode=True):
			if not line:
				continue
			try:
				last = json.loads(line)
			except ValueError:
				continue
			if error := last.get("error"):
				raise ProviderError(f"Ollama pull failed: {error}")
		return last

	def delete_model(self, model: str) -> dict:
		self.request("DELETE", "/api/delete", {"model": model}, timeout=min(self.timeout, 60))
		return {"status": "deleted", "model": model}

	# -- health -----------------------------------------------------------

	def health_check(self) -> HealthStatus:
		started = time.monotonic()
		try:
			models = self.list_models()
		except Exception as exc:
			return HealthStatus(status="Offline", error=str(exc)[:500])

		latency_ms = int((time.monotonic() - started) * 1000)
		return HealthStatus(
			status="Online" if models else "Degraded",
			latency_ms=latency_ms,
			available_models=len(models),
			error=None if models else "Runtime reachable but no models are installed.",
			details={"models": [m.name for m in models][:50]},
		)
