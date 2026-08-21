# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Provider registry.

Resolves an `AI Provider` document to a concrete adapter instance. Other Frappe
apps extend the platform with new runtimes through the `ai_providers` hook::

    # in your app's hooks.py
    ai_providers = {
        "My Runtime": "my_app.providers.my_runtime.MyRuntimeProvider",
    }

The chosen class must derive from
:class:`ai_fr_hg.ai.providers.base.BaseProvider`.
"""

import frappe
from frappe import _

from ai_fr_hg.ai.providers.base import BaseProvider
from ai_fr_hg.ai.providers.ollama import OllamaProvider
from ai_fr_hg.ai.providers.openai_compatible import (
	LlamaCppProvider,
	LMStudioProvider,
	OpenAICompatibleProvider,
	TextGenWebUIProvider,
	VLLMProvider,
)

BUILTIN_PROVIDERS: dict[str, type[BaseProvider]] = {
	"Ollama": OllamaProvider,
	"OpenAI Compatible": OpenAICompatibleProvider,
	"Llama.cpp": LlamaCppProvider,
	"vLLM": VLLMProvider,
	"LM Studio": LMStudioProvider,
	"Text Generation WebUI": TextGenWebUIProvider,
}


def get_provider_classes() -> dict[str, type[BaseProvider]]:
	"""Built-in adapters merged with any contributed by installed apps."""
	classes = dict(BUILTIN_PROVIDERS)
	for provider_type, dotted_path in (frappe.get_hooks("ai_providers") or {}).items():
		if isinstance(dotted_path, list):
			dotted_path = dotted_path[-1]
		try:
			classes[provider_type] = frappe.get_attr(dotted_path)
		except Exception:
			frappe.log_error(
				title="AI Provider registry",
				message=f"Could not load provider adapter {dotted_path} for type {provider_type}.",
			)
	return classes


def get_provider_class(provider_type: str, adapter_path: str | None = None) -> type[BaseProvider]:
	"""Resolve the adapter class for a provider type without instantiating it.

	Capability policy (PROV-02) needs the class-level ``supports_*`` flags
	before any call is made, and instantiating an adapter is not free. Keeping
	the lookup here means the provider registry stays the only place that knows
	how a provider type maps to a class.
	"""
	if provider_type == "Custom":
		if not adapter_path:
			frappe.throw(_("A Custom provider requires a Custom Adapter Path."))
		try:
			provider_class = frappe.get_attr(adapter_path)
		except Exception as exc:
			frappe.throw(_("Could not load custom adapter {0}: {1}").format(adapter_path, str(exc)))
	else:
		provider_class = get_provider_classes().get(provider_type)
		if not provider_class:
			frappe.throw(_("No adapter is registered for provider type {0}.").format(provider_type))

	if not issubclass(provider_class, BaseProvider):
		frappe.throw(_("Adapter for {0} must derive from BaseProvider.").format(provider_type))
	return provider_class


def get_provider_class_for(provider: str) -> type[BaseProvider]:
	"""The adapter class backing a configured `AI Provider` record."""
	doc = frappe.get_cached_doc("AI Provider", provider)
	return get_provider_class(doc.provider_type, doc.adapter_path)


def get_provider(provider: str | None = None) -> BaseProvider:
	"""Return a ready adapter for `provider`, or for the default provider."""
	doc = frappe.get_cached_doc("AI Provider", provider) if provider else get_default_provider_doc()

	if not doc.enabled:
		frappe.throw(_("AI Provider {0} is disabled.").format(doc.name))

	return get_provider_class(doc.provider_type, doc.adapter_path)(doc)


def get_default_provider_doc():
	"""The provider flagged as default, else the enabled one with best priority."""
	name = frappe.db.get_value("AI Provider", {"is_default": 1, "enabled": 1}, "name")
	if not name:
		candidates = frappe.get_all(
			"AI Provider",
			filters={"enabled": 1},
			fields=["name"],
			order_by="priority asc, creation asc",
			limit=1,
		)
		if not candidates:
			frappe.throw(
				_("No AI Provider is configured. Create one in the AI Administration workspace."),
				title=_("No Provider"),
			)
		name = candidates[0].name
	return frappe.get_cached_doc("AI Provider", name)


def get_failover_provider_rows(exclude: str | None = None) -> list[dict]:
	"""Enabled, non-offline providers with their priority, best first.

	PROV-01 needs the priority value, not just the order, so an equivalent
	model on a preferred provider can outrank a same-named model on a
	deprioritised one.
	"""
	rows = frappe.get_all(
		"AI Provider",
		filters={"enabled": 1, "status": ["!=", "Offline"]},
		fields=["name", "priority"],
		order_by="priority asc, creation asc",
	)
	return [{"name": row.name, "priority": row.priority or 0} for row in rows if row.name != exclude]


def get_failover_providers(exclude: str | None = None) -> list[str]:
	"""Enabled providers ordered by priority, used for automatic failover."""
	return [row["name"] for row in get_failover_provider_rows(exclude=exclude)]
