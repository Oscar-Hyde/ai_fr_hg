# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""The AI execution engine.

Every AI operation in the platform passes through :func:`run_chat` or
:func:`run_embedding`. That single funnel is what gives the platform complete
traceability: model resolution, quota enforcement, redaction, logging, metric
roll-up and failover all live here rather than being scattered across callers.
"""

import math
import time
from collections.abc import Callable
from numbers import Real
from typing import Any

import frappe
from frappe import _
from frappe.utils import cint, flt, now_datetime

from ai_fr_hg.ai.deadline import get_deadline
from ai_fr_hg.ai.exceptions import (
	DeadlineExceededError,
	ModelNotAvailableError,
	ProviderError,
	TurnCancelledError,
)
from ai_fr_hg.ai.providers import get_failover_providers, get_provider
from ai_fr_hg.ai.providers.base import ChatMessage, CompletionResult
from ai_fr_hg.utils.db import safe_set_value

#: Smallest window worth starting another provider attempt in. Below this the
#: call would be clamped to a timeout too short for any real model to answer.
MIN_ATTEMPT_SECONDS = 5.0

#: Native Frappe realtime event that carries one token delta to the Desk chat.
CHAT_TOKEN_EVENT = "ai_fr_hg:chat_token"


def publish_chat_token(conversation: str | None, turn_id: str, delta: str, user: str | None = None) -> None:
	"""Push one streamed fragment on Frappe's existing socket channel."""
	if not delta or not turn_id:
		return
	frappe.publish_realtime(
		CHAT_TOKEN_EVENT,
		{"conversation": conversation, "turn_id": turn_id, "delta": delta},
		user=user or getattr(getattr(frappe, "session", None), "user", None),
		after_commit=False,
	)


def get_settings():
	"""Cached AI Platform Settings single."""
	return frappe.get_cached_doc("AI Platform Settings")


def resolve_model(model: str | None = None, model_type: str = "Chat"):
	"""Resolve a model name, the configured default, or the best candidate.

	Raises when nothing suitable is enabled, so callers never operate on an
	implicit or missing model.
	"""
	if model:
		doc = frappe.get_cached_doc("AI Model", model)
		if not doc.enabled:
			frappe.throw(_("AI Model {0} is disabled.").format(model))
		return doc

	settings = get_settings()
	default_field = {
		"Chat": "default_chat_model",
		"Completion": "default_chat_model",
		"Embedding": "default_embedding_model",
		"Vision": "default_vision_model",
	}.get(model_type)

	if default_field and (configured := settings.get(default_field)):
		doc = frappe.get_cached_doc("AI Model", configured)
		if doc.enabled:
			return doc

	candidates = frappe.get_all(
		"AI Model",
		filters={"enabled": 1, "model_type": model_type},
		fields=["name"],
		order_by="is_default desc, creation asc",
		limit=1,
	)
	if not candidates:
		frappe.throw(
			_("No enabled {0} model is available. Register one in the AI Models workspace.").format(
				model_type
			),
			exc=ModelNotAvailableError,
			title=_("No Model"),
		)
	return frappe.get_cached_doc("AI Model", candidates[0].name)


def build_options(model_doc, overrides: dict | None = None) -> dict:
	"""Merge platform defaults, model defaults and per-call overrides."""
	settings = get_settings()
	options: dict[str, Any] = {
		"temperature": flt(model_doc.temperature)
		if model_doc.temperature is not None
		else flt(settings.default_temperature),
		"top_p": flt(model_doc.top_p) if model_doc.top_p is not None else flt(settings.default_top_p),
		"max_tokens": cint(model_doc.max_tokens) or cint(settings.default_max_tokens),
		"context_window": cint(model_doc.num_ctx_override) or cint(model_doc.context_window),
	}
	if model_doc.top_k:
		options["top_k"] = cint(model_doc.top_k)
	if model_doc.repeat_penalty:
		options["repeat_penalty"] = flt(model_doc.repeat_penalty)
	if model_doc.keep_alive:
		options["keep_alive"] = model_doc.keep_alive
	if model_doc.num_threads:
		options["num_threads"] = cint(model_doc.num_threads)
	if model_doc.num_batch:
		options["num_batch"] = cint(model_doc.num_batch)
	if model_doc.gpu_layers:
		options["gpu_layers"] = cint(model_doc.gpu_layers)
	if model_doc.stop_sequences:
		options["stop"] = [s.strip() for s in model_doc.stop_sequences.splitlines() if s.strip()]

	for row in model_doc.get("parameters") or []:
		options[row.parameter] = _cast_parameter(row.value, row.value_type)

	if overrides:
		options.update({k: v for k, v in overrides.items() if v is not None})
	return options


def _cast_parameter(value, value_type):
	import json

	if value_type == "Number":
		return flt(value)
	if value_type == "Boolean":
		return str(value).lower() in ("1", "true", "yes")
	if value_type == "JSON":
		try:
			return json.loads(value)
		except (ValueError, TypeError):
			return value
	return value


def normalise_messages(messages) -> list[ChatMessage]:
	"""Accept dicts or ChatMessage objects and return ChatMessage objects."""
	normalised = []
	for message in messages or []:
		if isinstance(message, ChatMessage):
			normalised.append(message)
			continue
		role = (message.get("role") or "user").lower()
		normalised.append(
			ChatMessage(
				role=role,
				content=message.get("content") or "",
				name=message.get("name"),
				tool_call_id=message.get("tool_call_id"),
				tool_calls=message.get("tool_calls"),
				images=message.get("images"),
			)
		)
	return normalised


def run_chat(
	messages,
	model: str | None = None,
	options: dict | None = None,
	tools: list[dict] | None = None,
	json_schema: dict | None = None,
	operation: str = "Chat",
	reference_doctype: str | None = None,
	reference_name: str | None = None,
	conversation: str | None = None,
	pipeline_run: str | None = None,
	allow_failover: bool = True,
	on_token: Callable[[str], None] | None = None,
	turn_id: str | None = None,
) -> CompletionResult:
	"""Execute a chat completion with full logging, quota checks and failover."""
	from ai_fr_hg.ai.governance import check_quota, record_usage
	from ai_fr_hg.ai.logging import finish_execution_log, start_execution_log

	settings = get_settings()
	if not settings.platform_enabled:
		frappe.throw(_("The AI Platform is disabled in AI Platform Settings."))

	model_doc = resolve_model(model, "Chat")
	check_quota(model_doc)

	chat_messages = normalise_messages(messages)
	merged_options = build_options(model_doc, options)

	log = start_execution_log(
		operation=operation,
		model=model_doc,
		messages=chat_messages,
		options=merged_options,
		reference_doctype=reference_doctype,
		reference_name=reference_name,
		conversation=conversation,
		pipeline_run=pipeline_run,
	)

	attempts = [model_doc.provider]
	if allow_failover:
		attempts += get_failover_providers(exclude=model_doc.provider)

	last_error: Exception | None = None
	max_retries = cint(settings.max_retries)
	deadline = get_deadline()

	for attempt_index, provider_name in enumerate(attempts):
		# Failing over costs at least another full round trip. If the budget
		# cannot fund one, stop here and report the failure we already have
		# rather than burning the remaining time on a call we must abandon.
		if deadline and attempt_index and not deadline.allows(MIN_ATTEMPT_SECONDS):
			break

		for retry in range(max_retries + 1):
			try:
				provider = get_provider(provider_name)
				result = _complete_chat(
					provider,
					chat_messages,
					model=model_doc.model_name,
					options=merged_options,
					tools=tools,
					json_schema=json_schema,
					on_token=on_token,
					turn_id=turn_id,
				)
				finish_execution_log(log, result, provider=provider_name, retry_count=retry)
				update_model_metrics(model_doc.name, result)
				record_usage(model_doc.name, result.total_tokens)
				return result
			except TurnCancelledError as exc:
				last_error = exc
				break
			except Exception as exc:
				last_error = exc
				if not (retry < max_retries and _is_retryable(exc)):
					break
				# Never sleep away time the request no longer has, and never
				# retry into a budget too small to hold the attempt.
				backoff = min(2**retry, 8)
				if deadline and not deadline.allows(backoff + MIN_ATTEMPT_SECONDS):
					break
				time.sleep(backoff)

		if isinstance(last_error, (DeadlineExceededError, TurnCancelledError)):
			break  # out of time or cancelled; further providers would fail identically
		if attempt_index < len(attempts) - 1:
			frappe.log_error(
				title="AI failover",
				message=f"Provider {provider_name} failed ({last_error}); trying the next provider.",
			)

	finish_execution_log(log, None, error=last_error)
	raise last_error or ProviderError(_("Chat completion failed."))


def _complete_chat(
	provider,
	messages,
	*,
	model: str,
	options: dict,
	tools: list[dict] | None,
	json_schema: dict | None,
	on_token: Callable[[str], None] | None,
	turn_id: str | None = None,
) -> CompletionResult:
	"""Use the provider stream when requested; fall back to blocking chat."""
	if on_token and getattr(provider, "supports_streaming", False) and not json_schema:
		streamed: list[str] = []

		def emit(delta: str) -> None:
			if not delta:
				return
			streamed.append(delta)
			on_token(delta)

		try:
			result = _complete_via_stream(
				provider,
				messages,
				model=model,
				options=options,
				tools=tools,
				on_token=emit,
				turn_id=turn_id,
			)
			if streamed:
				result.raw["streamed"] = True
				return result
		except TurnCancelledError:
			raise
		except Exception:
			if streamed:
				raise
	return provider.chat(messages, model=model, options=options, tools=tools, json_schema=json_schema)


def _complete_via_stream(
	provider,
	messages,
	*,
	model: str,
	options: dict,
	tools: list[dict] | None,
	on_token,
	turn_id: str | None = None,
) -> CompletionResult:
	from ai_fr_hg.ai.conversation import is_turn_cancelled

	started = time.monotonic()
	parts: list[str] = []
	for fragment in provider.stream_chat(messages, model=model, options=options, tools=tools):
		if turn_id and is_turn_cancelled(turn_id):
			raise TurnCancelledError(partial="".join(parts))
		if not fragment:
			continue
		parts.append(fragment)
		on_token(fragment)
	content = "".join(parts)
	duration_ms = int((time.monotonic() - started) * 1000)
	approx_tokens = max(1, len(content) // 4) if content else 0
	return CompletionResult(
		content=content,
		duration_ms=duration_ms,
		model=model,
		completion_tokens=approx_tokens,
		total_tokens=approx_tokens,
		raw={"streamed": True},
	)


def _is_retryable(exc: Exception) -> bool:
	from ai_fr_hg.ai.exceptions import ProviderOfflineError, ProviderTimeoutError

	return isinstance(exc, ProviderTimeoutError | ProviderOfflineError)


def run_embedding(
	texts: list[str],
	model: str | None = None,
	operation: str = "Embedding",
	reference_doctype: str | None = None,
	reference_name: str | None = None,
) -> list[list[float]]:
	"""Embed a batch of texts using the configured embedding model."""
	from ai_fr_hg.ai.logging import finish_execution_log, start_execution_log

	if not texts:
		return []

	settings = get_settings()
	if not settings.platform_enabled:
		frappe.throw(_("The AI Platform is disabled in AI Platform Settings."))

	model_doc = resolve_model(model, "Embedding")
	log = start_execution_log(
		operation=operation,
		model=model_doc,
		messages=None,
		options={"batch_size": len(texts)},
		reference_doctype=reference_doctype,
		reference_name=reference_name,
	)

	started = time.monotonic()
	try:
		provider = get_provider(model_doc.provider)
		vectors = _validate_embedding_response(
			provider.embed(texts, model=model_doc.model_name),
			expected_count=len(texts),
			expected_dimensions=cint(model_doc.embedding_dimensions),
		)
	except Exception as exc:
		finish_execution_log(log, None, error=exc)
		raise

	duration_ms = int((time.monotonic() - started) * 1000)
	result = CompletionResult(
		content=f"{len(vectors)} vectors",
		duration_ms=duration_ms,
		model=model_doc.model_name,
	)
	finish_execution_log(log, result, provider=model_doc.provider)

	if vectors and not model_doc.embedding_dimensions:
		safe_set_value(
			"AI Model", model_doc.name, "embedding_dimensions", len(vectors[0]), update_modified=False
		)
	return vectors


def _validate_embedding_response(
	vectors,
	*,
	expected_count: int,
	expected_dimensions: int = 0,
) -> list[list[float]]:
	"""Validate the provider contract before any caller can persist vectors."""
	if not isinstance(vectors, (list, tuple)):
		raise ProviderError(_("The embedding provider returned a non-list response."))
	if len(vectors) != expected_count:
		raise ProviderError(
			_("The embedding provider returned {0} vectors for {1} inputs.").format(
				len(vectors), expected_count
			)
		)

	validated: list[list[float]] = []
	dimensions = expected_dimensions
	for index, vector in enumerate(vectors, start=1):
		if not isinstance(vector, (list, tuple)) or not vector:
			raise ProviderError(_("Embedding vector {0} is empty or malformed.").format(index))
		if any(isinstance(value, bool) or not isinstance(value, Real) for value in vector):
			raise ProviderError(_("Embedding vector {0} contains non-numeric values.").format(index))
		numeric = [float(value) for value in vector]
		if not all(math.isfinite(value) for value in numeric) or not any(numeric):
			raise ProviderError(_("Embedding vector {0} contains invalid values.").format(index))
		if dimensions and len(numeric) != dimensions:
			raise ProviderError(
				_("Embedding vector {0} has {1} dimensions; expected {2}.").format(
					index, len(numeric), dimensions
				)
			)
		dimensions = dimensions or len(numeric)
		validated.append(numeric)
	return validated


def update_model_metrics(model: str, result: CompletionResult) -> None:
	"""Roll running request/token/latency statistics onto the model record.

	Uses an atomic SQL update so simultaneous requests cannot produce a
	``1020`` timestamp mismatch or lose counters.
	"""
	if not frappe.db.exists("AI Model", model):
		return

	frappe.db.sql(
		"""
		update `tabAI Model`
		set total_requests = coalesce(total_requests, 0) + 1,
			total_tokens = coalesce(total_tokens, 0) + %s,
			average_latency_ms = (
				(coalesce(average_latency_ms, 0) * coalesce(total_requests, 0) + %s)
				/ (coalesce(total_requests, 0) + 1)
			),
			status = 'Available',
			last_checked = %s,
			last_error = null
		where name = %s
		""",
		(cint(result.total_tokens), cint(result.duration_ms), now_datetime(), model),
	)
