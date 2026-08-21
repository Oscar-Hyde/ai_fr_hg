# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Model type and capability policy — the canonical owner of three questions.

============================  =========================================
Question                      Finding
============================  =========================================
May this model perform this   GOV-04 — explicit model resolution must
operation type?               validate the model's declared type.
What can this provider and    PROV-02 — effective capability is
model *actually* do?          provider capability AND model capability
                              AND the last successful runtime probe.
Which model on another        PROV-01 — failover must select a
provider is equivalent?       compatible model, not reuse a name that
                              does not exist on the target runtime.
============================  =========================================

Everything above the "runtime probe cache" banner is pure: plain mappings in,
plain data out, no Frappe, no database, no network. That is what makes the
policy testable without a bench and what keeps a single implementation of the
rules rather than one copy per call site.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

#: Capabilities the platform reasons about. A capability is *effective* only
#: when the provider adapter can transport it, the model record declares it,
#: and no recent runtime probe has contradicted it.
CAPABILITIES = ("streaming", "tools", "json_mode", "vision", "embeddings")

#: Which `AI Model.model_type` values may serve a requested operation type.
#:
#: * A ``Vision`` model is a chat model that also accepts images, so it can
#:   serve ``Chat``.
#: * ``Completion`` is served by chat models because every supported local
#:   runtime exposes completion through the chat endpoint.
#: * ``Embedding`` is deliberately exclusive: asking a chat model to embed
#:   produces either an error or, worse, a plausible non-embedding response.
COMPATIBLE_MODEL_TYPES: dict[str, tuple[str, ...]] = {
	"Chat": ("Chat", "Completion", "Vision"),
	"Completion": ("Chat", "Completion"),
	"Embedding": ("Embedding",),
	"Vision": ("Vision",),
}

#: Provider adapter class attribute that backs each capability.
_PROVIDER_FLAGS = {
	"streaming": "supports_streaming",
	"tools": "supports_tools",
	"json_mode": "supports_json_mode",
	"vision": "supports_vision",
	"embeddings": "supports_embeddings",
}

#: `AI Model` field that backs each capability. ``embeddings`` has no model
#: flag: an Embedding-typed model embeds by definition.
_MODEL_FLAGS = {
	"streaming": "supports_streaming",
	"tools": "supports_tools",
	"json_mode": "supports_json_mode",
	"vision": "supports_vision",
}


def _value(source: Any, field: str, default=None):
	"""Read `field` from a Mapping, a Frappe document, or a class."""
	if source is None:
		return default
	if isinstance(source, Mapping):
		value = source.get(field, default)
	else:
		value = getattr(source, field, default)
	return default if value is None else value


def _flag(source: Any, field: str, default: bool = False) -> bool:
	"""Read `field` as a boolean, tolerating Frappe's ``0``/``1`` Check values."""
	value = _value(source, field, default)
	if isinstance(value, str):
		return value.strip().lower() not in ("", "0", "false", "no")
	return bool(value)


# ---------------------------------------------------------------------------
# GOV-04 — model type compatibility
# ---------------------------------------------------------------------------


def model_type_error(requested_type: str, model: Any) -> str | None:
	"""Return a machine-readable reason when `model` cannot serve `requested_type`.

	``None`` means the pairing is allowed. The caller turns the reason into a
	translated, user-facing message; keeping the reason a stable token is what
	lets tests assert on behaviour instead of on wording.
	"""
	model_type = _value(model, "model_type") or ""
	allowed = COMPATIBLE_MODEL_TYPES.get(requested_type)

	if allowed is None:
		return "unknown_operation_type"
	if not model_type:
		return "unknown_model_type"
	if model_type in allowed:
		return None

	# A Chat model that genuinely declares vision support may serve Vision.
	if requested_type == "Vision" and model_type in ("Chat", "Completion"):
		return None if _flag(model, "supports_vision") else "vision_not_supported"

	return "incompatible_model_type"


# ---------------------------------------------------------------------------
# PROV-02 — effective capability
# ---------------------------------------------------------------------------


def effective_capabilities(
	provider: Any,
	model: Any,
	*,
	failed_probes: Iterable[str] = (),
) -> dict[str, bool]:
	"""Intersect provider transport, model declaration, and runtime probes.

	`provider` may be an adapter instance or its class — both carry the
	``supports_*`` class attributes. `failed_probes` names capabilities the
	runtime has recently rejected; see :func:`probe_failed`.
	"""
	failed = {str(name) for name in failed_probes or ()}
	model_type = _value(model, "model_type") or ""
	capabilities: dict[str, bool] = {}

	for capability in CAPABILITIES:
		provider_ok = _flag(provider, _PROVIDER_FLAGS[capability], default=False)

		if capability == "embeddings":
			model_ok = model_type == "Embedding"
		else:
			model_ok = _flag(model, _MODEL_FLAGS[capability], default=False)

		capabilities[capability] = bool(provider_ok and model_ok and capability not in failed)

	return capabilities


def capability_error(
	capabilities: Mapping[str, bool],
	*,
	tools: bool = False,
	json_schema: bool = False,
	images: bool = False,
	embeddings: bool = False,
) -> str | None:
	"""Return the first capability a request needs but does not effectively have.

	Called *before* the runtime request so an unsupported combination fails
	with an accurate explanation instead of an opaque HTTP 400 from the model
	server. Streaming is deliberately absent: it degrades to a blocking call
	rather than failing the request.
	"""
	if tools and not capabilities.get("tools"):
		return "tools"
	if json_schema and not capabilities.get("json_mode"):
		return "json_mode"
	if images and not capabilities.get("vision"):
		return "vision"
	if embeddings and not capabilities.get("embeddings"):
		return "embeddings"
	return None


def required_capabilities(
	*,
	tools: bool = False,
	json_schema: bool = False,
	images: bool = False,
	embeddings: bool = False,
) -> set[str]:
	"""The capability set a single call depends on, used to rank failover targets."""
	required: set[str] = set()
	if tools:
		required.add("tools")
	if json_schema:
		required.add("json_mode")
	if images:
		required.add("vision")
	if embeddings:
		required.add("embeddings")
	return required


# ---------------------------------------------------------------------------
# PROV-01 — equivalent-model selection for failover
# ---------------------------------------------------------------------------

#: Below this score a candidate is not a defensible substitute and failover
#: skips it rather than answering the user from an arbitrary model.
MIN_FAILOVER_SCORE = 1


def score_failover_candidate(
	source: Mapping,
	candidate: Mapping,
	*,
	required: Iterable[str] = (),
) -> int | None:
	"""Score `candidate` as a replacement for `source`; ``None`` means unusable.

	Hard requirements (any failure returns ``None``):

	* the candidate is enabled and lives on a different provider;
	* its declared type can serve the same operation as the source type;
	* for embeddings the vector dimensions match exactly — a different
	  dimensionality silently corrupts every similarity comparison, so this is
	  never a soft preference;
	* it declares every capability the in-flight call actually needs.

	Preferences are additive so the ordering is explainable in diagnostics.
	"""
	required = set(required or ())

	if not _flag(candidate, "enabled"):
		return None
	if _value(candidate, "provider") == _value(source, "provider"):
		return None

	source_type = _value(source, "model_type") or "Chat"
	if model_type_error(source_type, candidate) is not None:
		return None

	if source_type == "Embedding":
		source_dimensions = int(_value(source, "embedding_dimensions", 0) or 0)
		candidate_dimensions = int(_value(candidate, "embedding_dimensions", 0) or 0)
		if not source_dimensions or source_dimensions != candidate_dimensions:
			return None

	for capability in required:
		field = _MODEL_FLAGS.get(capability)
		if field and not _flag(candidate, field):
			return None
		if capability == "embeddings" and _value(candidate, "model_type") != "Embedding":
			return None

	score = MIN_FAILOVER_SCORE

	# The same runtime model name on another host is the strongest signal that
	# the answer will be equivalent.
	if _value(candidate, "model_name") and _value(candidate, "model_name") == _value(source, "model_name"):
		score += 100
	if _value(candidate, "family") and _value(candidate, "family") == _value(source, "family"):
		score += 50
	if _value(candidate, "parameter_size") and _value(candidate, "parameter_size") == _value(
		source, "parameter_size"
	):
		score += 10
	if int(_value(candidate, "context_window", 0) or 0) >= int(_value(source, "context_window", 0) or 0):
		score += 5
	if _flag(candidate, "is_default"):
		score += 3

	# Lower provider priority number wins; keep the contribution small so it
	# only breaks ties between otherwise equivalent models.
	score -= max(0, min(int(_value(candidate, "provider_priority", 0) or 0), 100)) // 10

	return score


def rank_failover_candidates(
	source: Mapping,
	candidates: Iterable[Mapping],
	*,
	required: Iterable[str] = (),
) -> list[dict]:
	"""Order usable substitutes for `source`, best first.

	Returns dicts of ``{provider, model, score}`` so the engine can log the
	*actual* provider and model identity of every attempt (PROV-01).
	"""
	ranked: list[dict] = []
	for candidate in candidates or ():
		score = score_failover_candidate(source, candidate, required=required)
		if score is None:
			continue
		ranked.append(
			{
				"provider": _value(candidate, "provider"),
				"model": _value(candidate, "name"),
				"model_name": _value(candidate, "model_name"),
				"score": score,
			}
		)

	# Sort by score, then by model name, so an identical corpus of candidates
	# always produces the same order — failover must be reproducible.
	ranked.sort(key=lambda row: (-row["score"], str(row["model"] or "")))
	return ranked


# ---------------------------------------------------------------------------
# Runtime probe cache — the only Frappe-aware part of this module
# ---------------------------------------------------------------------------

#: How long a rejected capability stays rejected. Long enough to stop a request
#: storm re-testing a model that cannot do tool calling, short enough that
#: swapping the runtime model recovers without operator action.
PROBE_TTL_SECONDS = 3600

#: Substrings a local runtime uses when it refuses a capability outright.
_PROBE_SIGNATURES = {
	"tools": ("does not support tools", "tool calls are not supported", "tool_choice"),
	"json_mode": ("does not support json", "response_format", "format must be"),
	"vision": ("does not support images", "image input", "vision"),
}


def _probe_key(model: str, capability: str) -> str:
	return f"ai_fr_hg:capability_probe:{model}:{capability}"


def probe_failed(model: str | None, capability: str) -> bool:
	"""True when the runtime recently refused `capability` for `model`."""
	if not model:
		return False
	try:
		import frappe

		return bool(frappe.cache().get_value(_probe_key(model, capability)))
	except Exception:
		# A cache outage must not invent a capability restriction; declared
		# capability remains authoritative and the call fails honestly at the
		# runtime instead.
		return False


def failed_probes(model: str | None) -> set[str]:
	"""Every capability currently marked as refused by the runtime."""
	return {capability for capability in CAPABILITIES if probe_failed(model, capability)}


def record_probe_failure(model: str | None, capability: str) -> None:
	"""Remember that the runtime refused `capability`, bounded by a TTL."""
	if not model or capability not in CAPABILITIES:
		return
	try:
		import frappe

		frappe.cache().set_value(_probe_key(model, capability), 1, expires_in_sec=PROBE_TTL_SECONDS)
	except Exception:
		pass


def clear_probe(model: str | None, capability: str | None = None) -> None:
	"""Forget probe failures for a model, or for one capability of it."""
	if not model:
		return
	try:
		import frappe

		for name in [capability] if capability else list(CAPABILITIES):
			frappe.cache().delete_value(_probe_key(model, name))
	except Exception:
		pass


def classify_capability_failure(message: str | None) -> str | None:
	"""Map a runtime error message onto the capability it refused, if any.

	Only used to *narrow* future requests; an unrecognised error never marks a
	capability as missing.
	"""
	if not message:
		return None
	lowered = str(message).lower()
	for capability, signatures in _PROBE_SIGNATURES.items():
		if any(signature in lowered for signature in signatures):
			return capability
	return None


# ---------------------------------------------------------------------------
# Discovery defaults (PROV-02)
# ---------------------------------------------------------------------------

#: Runtime model-name tokens that reliably indicate image input support.
VISION_NAME_TOKENS = (
	"llava",
	"bakllava",
	"vision",
	"moondream",
	"minicpm-v",
	"qwen-vl",
	"qwen2-vl",
	"qwen2.5-vl",
	"internvl",
	"pixtral",
	"gemma3",
)


def looks_like_vision_model(model_name: str | None) -> bool:
	"""Heuristic used only to seed a *default*, never to override an operator."""
	lowered = (model_name or "").lower()
	return any(token in lowered for token in VISION_NAME_TOKENS)


def discovery_capability_defaults(provider: Any, model_type: str, model_name: str) -> dict[str, int]:
	"""Capability flags to seed on a newly discovered model.

	The platform previously gated only on the provider adapter, so a discovered
	model behaved as though it had every capability its runtime could
	transport. Seeding the record with exactly that keeps discovery
	non-regressive while making the fields real controls an operator can now
	switch off. A refusal from the runtime narrows them further through the
	probe cache.
	"""
	if model_type == "Embedding":
		return {
			"supports_streaming": 0,
			"supports_tools": 0,
			"supports_json_mode": 0,
			"supports_vision": 0,
		}

	return {
		"supports_streaming": int(_flag(provider, "supports_streaming", True)),
		"supports_tools": int(_flag(provider, "supports_tools", False)),
		"supports_json_mode": int(_flag(provider, "supports_json_mode", False)),
		"supports_vision": int(
			_flag(provider, "supports_vision", False)
			and (model_type == "Vision" or looks_like_vision_model(model_name))
		),
	}
