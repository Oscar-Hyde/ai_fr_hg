# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Service health monitoring and model discovery."""

from uuid import uuid4

import frappe
from frappe import _
from frappe.utils import cint, now_datetime

from ai_fr_hg.utils.db import safe_set_value

DEGRADED_THRESHOLD = 3


def check_provider_health(provider: str, log: bool = True) -> dict:
	"""Probe one provider and persist its status."""
	from ai_fr_hg.ai.providers import get_provider

	doc = frappe.get_doc("AI Provider", provider)

	try:
		adapter = get_provider(provider)
		status = adapter.health_check()
	except Exception as exc:
		status = type(
			"Status",
			(),
			{
				"status": "Offline",
				"latency_ms": 0,
				"available_models": 0,
				"error": str(exc)[:500],
				"details": {},
				"is_online": False,
			},
		)()

	failures = cint(doc.consecutive_failures)
	failures = 0 if status.status == "Online" else failures + 1

	# A provider that keeps failing is marked Offline even if it answers slowly.
	effective = status.status
	if failures >= DEGRADED_THRESHOLD:
		effective = "Offline"

	previous = doc.status
	safe_set_value(
		"AI Provider",
		provider,
		{
			"status": effective,
			"last_health_check": now_datetime(),
			"latency_ms": cint(status.latency_ms),
			"available_model_count": cint(status.available_models),
			"consecutive_failures": failures,
			"last_error": status.error,
		},
		update_modified=False,
	)

	if log:
		entry = frappe.new_doc("AI Service Health Log")
		entry.update(
			{
				"provider": provider,
				"status": effective,
				"latency_ms": cint(status.latency_ms),
				"available_models": cint(status.available_models),
				"checked_on": now_datetime(),
				"details": frappe.as_json(getattr(status, "details", {})),
				"error_message": status.error,
			}
		)
		entry.flags.ignore_permissions = True
		entry.insert(ignore_permissions=True)

	if previous != effective:
		_notify_status_change(doc, previous, effective, status.error)

	return {
		"provider": provider,
		"status": effective,
		"latency_ms": cint(status.latency_ms),
		"available_models": cint(status.available_models),
		"error": status.error,
	}


def check_all_providers() -> list[dict]:
	"""Probe every enabled provider. Used by the scheduler."""
	results = []
	for provider in frappe.get_all("AI Provider", filters={"enabled": 1}, pluck="name"):
		save_point = f"ai_health_{uuid4().hex}"
		frappe.db.savepoint(save_point)
		try:
			results.append(check_provider_health(provider))
			frappe.db.release_savepoint(save_point)
		except Exception:
			frappe.db.rollback(save_point=save_point)
			frappe.log_error(title=f"AI health check failed: {provider}", message=frappe.get_traceback())
	return results


def _notify_status_change(doc, previous: str, current: str, error: str | None) -> None:
	"""Alert administrators when a provider changes availability."""
	from ai_fr_hg.ai.logging import write_audit_log

	write_audit_log(
		action=f"Provider {current}",
		category="Security" if current == "Offline" else "Configuration",
		severity="Critical" if current == "Offline" else "Info",
		message=f"Provider {doc.name} changed from {previous} to {current}. {error or ''}".strip(),
		reference_doctype="AI Provider",
		reference_name=doc.name,
	)

	if current != "Offline":
		return

	settings = frappe.get_cached_doc("AI Platform Settings")
	if not settings.alert_on_provider_offline:
		return

	recipients = [line.strip() for line in (settings.alert_recipients or "").splitlines() if line.strip()]
	if not recipients:
		return

	try:
		frappe.sendmail(
			recipients=recipients,
			subject=_("AI Provider Offline: {0}").format(doc.name),
			message=_("The AI provider <b>{0}</b> at {1} is no longer reachable.<br><br>Error: {2}").format(
				doc.name, doc.base_url, error or _("unknown")
			),
			reference_doctype="AI Provider",
			reference_name=doc.name,
		)
	except Exception:
		frappe.log_error(title="AI provider alert failed", message=frappe.get_traceback())


def sync_provider_models(provider: str, create_missing: bool = True) -> dict:
	"""Discover models on a runtime and reconcile them with `AI Model` records."""
	from ai_fr_hg.ai.providers import get_provider

	adapter = get_provider(provider)
	discovered = adapter.list_models()
	discovered_names = {model.name for model in discovered}

	registered = {
		row.model_name: row.name
		for row in frappe.get_all("AI Model", filters={"provider": provider}, fields=["name", "model_name"])
	}

	created, updated, missing, unsupported = [], [], [], []
	default_chat = frappe.db.get_single_value("AI Platform Settings", "default_chat_model")

	for model in discovered:
		model_type = _guess_model_type(model.name)
		if model_type is None and model.name not in registered:
			unsupported.append(model.name)
			continue
		if model.name in registered:
			safe_set_value(
				"AI Model",
				registered[model.name],
				{
					"status": "Available",
					"last_checked": now_datetime(),
					"digest": model.digest,
					"size_bytes": cint(model.size),
					"family": model.family,
					"parameter_size": model.parameter_size,
					"quantization": model.quantization,
					"last_error": None,
				},
				update_modified=False,
			)
			updated.append(model.name)
			continue

		if not create_missing:
			continue

		label = f"{model.name} ({provider})"
		if frappe.db.exists("AI Model", label):
			continue

		save_point = f"ai_model_discovery_{uuid4().hex}"
		frappe.db.savepoint(save_point)
		try:
			doc = frappe.new_doc("AI Model")
			doc.update(
				{
					"model_label": label,
					"provider": provider,
					"model_name": model.name,
					"model_type": model_type,
					"family": model.family,
					"parameter_size": model.parameter_size,
					"quantization": model.quantization,
					"context_window": cint(model.context_window) or 8192,
					"digest": model.digest,
					"size_bytes": cint(model.size),
					"status": "Available",
					"last_checked": now_datetime(),
					"enabled": 1,
				}
			)
			doc.flags.ignore_permissions = True
			doc.insert(ignore_permissions=True)

			if doc.model_type == "Chat" and not default_chat:
				safe_set_value(
					"AI Platform Settings",
					"AI Platform Settings",
					"default_chat_model",
					doc.name,
					update_modified=False,
				)
				default_chat = doc.name
			created.append(model.name)
			frappe.db.release_savepoint(save_point)
		except frappe.DuplicateEntryError:
			# Another discovery/pull/sync worker can create the same model concurrently
			# without rolling back models reconciled earlier in this batch.
			frappe.db.rollback(save_point=save_point)
			frappe.db.release_savepoint(save_point)
		finally:
			frappe.clear_cache(doctype="AI Model")

	# Flag registered models the runtime no longer reports.
	for model_name, record in registered.items():
		if model_name not in discovered_names:
			safe_set_value(
				"AI Model",
				record,
				{
					"status": "Missing",
					"last_checked": now_datetime(),
					"last_error": "Not reported by the runtime.",
				},
				update_modified=False,
			)
			missing.append(model_name)

	if not default_chat:
		existing_chat = frappe.db.get_value(
			"AI Model",
			{"provider": provider, "enabled": 1, "model_type": "Chat"},
			"name",
			order_by="is_default desc, creation asc",
		)
		if existing_chat:
			safe_set_value(
				"AI Platform Settings",
				"AI Platform Settings",
				"default_chat_model",
				existing_chat,
				update_modified=False,
			)

	return {
		"provider": provider,
		"discovered": len(discovered),
		"created": created,
		"updated": updated,
		"missing": missing,
		"unsupported": unsupported,
	}


def _guess_model_type(model_name: str) -> str | None:
	"""Infer an executable model role, or return None for known unsupported roles."""
	lowered = (model_name or "").lower()

	if any(token in lowered for token in ("embed", "bge", "gte", "e5-", "minilm", "nomic")):
		return "Embedding"
	if any(token in lowered for token in ("rerank", "reranker")):
		return None
	if any(token in lowered for token in ("llava", "vision", "-vl", "bakllava", "moondream", "minicpm-v")):
		return "Vision"
	return "Chat"


def sync_all_models() -> list[dict]:
	"""Discover models across every enabled provider."""
	results = []
	for provider in frappe.get_all(
		"AI Provider", filters={"enabled": 1, "status": ["!=", "Offline"]}, pluck="name"
	):
		try:
			results.append(sync_provider_models(provider))
		except Exception:
			frappe.log_error(title=f"AI model sync failed: {provider}", message=frappe.get_traceback())
	frappe.db.commit()  # nosemgrep: frappe-manual-commit
	return results


def get_platform_metrics() -> dict:
	"""Aggregate counters powering the operations dashboard."""
	from frappe.utils import add_to_date

	since_24h = add_to_date(now_datetime(), hours=-24)

	totals = frappe.db.sql(
		"""
		select
			count(*) as requests,
			coalesce(sum(total_tokens), 0) as tokens,
			coalesce(avg(duration_ms), 0) as avg_latency,
			coalesce(sum(case when status = 'Failed' then 1 else 0 end), 0) as failures
		from `tabAI Execution Log`
		where creation > %s
		""",
		(since_24h,),
		as_dict=True,
	)[0]

	return {
		"providers": {
			"total": frappe.db.count("AI Provider", {"enabled": 1}),
			"online": frappe.db.count("AI Provider", {"enabled": 1, "status": "Online"}),
			"offline": frappe.db.count("AI Provider", {"enabled": 1, "status": "Offline"}),
		},
		"models": {
			"total": frappe.db.count("AI Model", {"enabled": 1}),
			"available": frappe.db.count("AI Model", {"enabled": 1, "status": "Available"}),
			"missing": frappe.db.count("AI Model", {"enabled": 1, "status": "Missing"}),
		},
		"knowledge": {
			"knowledge_bases": frappe.db.count("AI Knowledge Base", {"enabled": 1}),
			"documents": frappe.db.count("AI Document"),
			"indexed": frappe.db.count("AI Document", {"status": "Indexed"}),
			"failed": frappe.db.count("AI Document", {"status": "Failed"}),
			"chunks": frappe.db.count("AI Document Chunk"),
		},
		"activity_24h": {
			"requests": cint(totals.requests),
			"tokens": cint(totals.tokens),
			"average_latency_ms": round(float(totals.avg_latency or 0), 2),
			"failures": cint(totals.failures),
			"conversations": frappe.db.count("AI Conversation", {"creation": [">", since_24h]}),
		},
	}
