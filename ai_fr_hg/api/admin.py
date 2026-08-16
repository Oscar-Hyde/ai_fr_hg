# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Whitelisted administration endpoints.

Every method here is restricted to AI Manager / System Manager.
"""

import json

import frappe
from frappe import _
from frappe.utils import cint, now_datetime


def _require_manager() -> None:
	frappe.only_for(["AI Manager", "System Manager"])


@frappe.whitelist()
def test_provider(provider: str) -> dict:
	"""Probe a provider and report its status immediately."""
	_require_manager()

	from ai_fr_hg.ai.monitoring import check_provider_health

	return check_provider_health(provider)


@frappe.whitelist()
def test_all_providers() -> list:
	"""Probe every enabled provider."""
	_require_manager()

	from ai_fr_hg.ai.monitoring import check_all_providers

	return check_all_providers()


@frappe.whitelist()
def discover_models(provider: str, create_missing: bool = True) -> dict:
	"""Discover the models installed on a runtime and register them."""
	_require_manager()

	from ai_fr_hg.ai.monitoring import sync_provider_models

	result = sync_provider_models(provider, create_missing=cint(create_missing))
	frappe.db.commit()  # nosemgrep: frappe-manual-commit
	return result


@frappe.whitelist()
def pull_model(provider: str, model_name: str) -> dict:
	"""Download a model onto a runtime that supports pulling."""
	_require_manager()

	from ai_fr_hg.ai.governance import check_capability

	check_capability("model_management")

	frappe.enqueue(
		"ai_fr_hg.api.admin._pull_model_job",
		queue="long",
		timeout=7200,
		job_id=f"ai_pull_{provider}_{model_name}",
		deduplicate=True,
		provider=provider,
		model_name=model_name,
		user=frappe.session.user,
	)
	return {"status": "Queued", "provider": provider, "model": model_name}


def _pull_model_job(provider: str, model_name: str, user: str) -> None:
	"""Background job that pulls a model and registers it."""
	from ai_fr_hg.ai.logging import write_audit_log
	from ai_fr_hg.ai.monitoring import sync_provider_models
	from ai_fr_hg.ai.providers import get_provider

	try:
		adapter = get_provider(provider)
		adapter.pull_model(model_name)
		sync_provider_models(provider)
		frappe.db.commit()  # nosemgrep: frappe-manual-commit

		write_audit_log(
			action="Model Pulled",
			category="Configuration",
			message=f"Model {model_name} downloaded onto {provider}.",
			reference_doctype="AI Provider",
			reference_name=provider,
		)
		frappe.publish_realtime(
			"ai_model_pulled", {"provider": provider, "model": model_name, "status": "Success"}, user=user
		)
	except Exception as exc:
		frappe.log_error(title=f"AI model pull failed: {model_name}", message=frappe.get_traceback())
		frappe.publish_realtime(
			"ai_model_pulled",
			{"provider": provider, "model": model_name, "status": "Failed", "error": str(exc)},
			user=user,
		)


@frappe.whitelist()
def test_model(model: str, prompt: str = "Reply with the single word: OK") -> dict:
	"""Send a short prompt to a model to verify it responds."""
	_require_manager()

	from ai_fr_hg.ai.engine import run_chat, run_embedding

	model_doc = frappe.get_doc("AI Model", model)

	if model_doc.model_type == "Embedding":
		vectors = run_embedding(["health check"], model=model)
		return {
			"model": model,
			"status": "OK" if vectors and vectors[0] else "Failed",
			"dimensions": len(vectors[0]) if vectors and vectors[0] else 0,
		}

	result = run_chat(
		[{"role": "user", "content": prompt}],
		model=model,
		options={"max_tokens": 64},
		operation="Health Check",
		allow_failover=False,
	)
	return {
		"model": model,
		"status": "OK",
		"response": result.content,
		"duration_ms": result.duration_ms,
		"total_tokens": result.total_tokens,
		"tokens_per_second": result.tokens_per_second,
	}


@frappe.whitelist()
def get_dashboard() -> dict:
	"""Aggregate payload powering the AI Control Center."""
	from ai_fr_hg.ai.monitoring import get_platform_metrics

	metrics = get_platform_metrics()

	metrics["providers_detail"] = frappe.get_all(
		"AI Provider",
		filters={"enabled": 1},
		fields=[
			"name",
			"provider_type",
			"base_url",
			"status",
			"latency_ms",
			"available_model_count",
			"last_health_check",
			"last_error",
		],
		order_by="priority asc",
	)
	metrics["models_detail"] = frappe.get_all(
		"AI Model",
		filters={"enabled": 1},
		fields=[
			"name",
			"model_label",
			"provider",
			"model_type",
			"status",
			"total_requests",
			"total_tokens",
			"average_latency_ms",
			"last_checked",
		],
		order_by="total_requests desc",
		limit_page_length=20,
	)
	metrics["recent_errors"] = frappe.get_all(
		"AI Execution Log",
		filters={"status": "Failed"},
		fields=["name", "operation", "model", "error_message", "creation", "user"],
		order_by="creation desc",
		limit_page_length=10,
	)
	metrics["pending_approvals"] = frappe.get_all(
		"AI Tool Invocation",
		filters={"status": "Pending Approval"},
		fields=["name", "tool", "user", "arguments", "creation"],
		order_by="creation desc",
		limit_page_length=10,
	)
	metrics["active_jobs"] = _get_queue_summary()
	metrics["top_users"] = frappe.db.sql(
		"""
		select user, count(*) as requests, coalesce(sum(total_tokens), 0) as tokens
		from `tabAI Execution Log`
		where creation > date_sub(now(), interval 7 day) and user is not null
		group by user
		order by requests desc
		limit 10
		""",
		as_dict=True,
	)
	return metrics


def _get_queue_summary() -> dict:
	"""Background queue depth, tolerant of Redis being unavailable."""
	try:
		from frappe.utils.background_jobs import get_queue

		return {queue_name: len(get_queue(queue_name)) for queue_name in ("default", "short", "long")}
	except Exception:
		return {}


@frappe.whitelist()
def get_usage_report(days: int = 30, user: str | None = None) -> dict:
	"""Usage trends over the last `days` days."""
	_require_manager()

	from frappe.utils import add_days, today

	since = add_days(today(), -cint(days) or -30)
	conditions = ["snapshot_date >= %(since)s"]
	values = {"since": since}
	if user:
		conditions.append("user = %(user)s")
		values["user"] = user

	where = " and ".join(conditions)

	return {
		"daily": frappe.db.sql(
			f"""
			select snapshot_date, sum(request_count) as requests, sum(total_tokens) as tokens,
				sum(failure_count) as failures
			from `tabAI Usage Snapshot`
			where {where}
			group by snapshot_date
			order by snapshot_date asc
			""",
			values,
			as_dict=True,
		),
		"by_model": frappe.db.sql(
			f"""
			select model, sum(request_count) as requests, sum(total_tokens) as tokens,
				avg(average_latency_ms) as avg_latency
			from `tabAI Usage Snapshot`
			where {where} and model is not null
			group by model
			order by requests desc
			limit 20
			""",
			values,
			as_dict=True,
		),
		"by_user": frappe.db.sql(
			f"""
			select user, sum(request_count) as requests, sum(total_tokens) as tokens
			from `tabAI Usage Snapshot`
			where {where} and user is not null
			group by user
			order by requests desc
			limit 20
			""",
			values,
			as_dict=True,
		),
	}


@frappe.whitelist()
def export_knowledge_base(knowledge_base: str, include_embeddings: bool = False) -> dict:
	"""Export a knowledge base to a private JSON file on this site."""
	_require_manager()

	kb = frappe.get_doc("AI Knowledge Base", knowledge_base)
	documents = frappe.get_all(
		"AI Document",
		filters={"knowledge_base": knowledge_base},
		fields=[
			"name",
			"title",
			"document_type",
			"source_type",
			"content",
			"summary",
			"metadata",
			"extracted_data",
			"status",
			"checksum",
		],
		limit_page_length=0,
	)

	if cint(include_embeddings):
		for document in documents:
			document["chunks"] = frappe.get_all(
				"AI Document Chunk",
				filters={"document": document.name},
				fields=["chunk_index", "heading", "content", "embedding", "embedding_model"],
				order_by="chunk_index asc",
				limit_page_length=0,
			)

	payload = {
		"knowledge_base": kb.as_dict(no_default_fields=True),
		"documents": documents,
		"exported_on": str(now_datetime()),
		"exported_by": frappe.session.user,
		"include_embeddings": bool(cint(include_embeddings)),
	}

	filename = f"ai-kb-{frappe.scrub(knowledge_base)}-{frappe.utils.today()}.json"
	file_doc = frappe.get_doc(
		{
			"doctype": "File",
			"file_name": filename,
			"is_private": 1,
			"content": frappe.as_json(payload, indent=1),
		}
	)
	file_doc.flags.ignore_permissions = True
	file_doc.insert(ignore_permissions=True)

	from ai_fr_hg.ai.logging import write_audit_log

	write_audit_log(
		action="Knowledge Base Exported",
		category="Data",
		severity="Warning",
		message=f"{knowledge_base} exported to {filename}.",
		reference_doctype="AI Knowledge Base",
		reference_name=knowledge_base,
	)

	return {"file_url": file_doc.file_url, "documents": len(documents)}


@frappe.whitelist()
def import_knowledge_base(file_url: str, knowledge_base: str | None = None) -> dict:
	"""Import documents from a previously exported knowledge base file."""
	_require_manager()

	from ai_fr_hg.ai.ingestion import get_file_content

	content, _filename = get_file_content(file_url)
	payload = json.loads(content)

	target = knowledge_base or (payload.get("knowledge_base") or {}).get("name")
	if not target:
		frappe.throw(_("No target knowledge base could be determined."))

	if not frappe.db.exists("AI Knowledge Base", target):
		source = payload.get("knowledge_base") or {}
		kb = frappe.new_doc("AI Knowledge Base")
		kb.update(
			{
				"knowledge_base_name": target,
				"description": source.get("description"),
				"chunk_size": source.get("chunk_size"),
				"chunk_overlap": source.get("chunk_overlap"),
			}
		)
		kb.insert()

	imported = 0
	for entry in payload.get("documents") or []:
		if frappe.db.exists("AI Document", {"checksum": entry.get("checksum"), "knowledge_base": target}):
			continue
		doc = frappe.new_doc("AI Document")
		doc.update(
			{
				"title": entry.get("title"),
				"knowledge_base": target,
				"source_type": "Text",
				"content": entry.get("content"),
				"summary": entry.get("summary"),
				"document_type": entry.get("document_type"),
				"status": "Queued",
			}
		)
		doc.insert()
		imported += 1

		from ai_fr_hg.ai.ingestion import enqueue_processing

		enqueue_processing(doc.name)

	return {"knowledge_base": target, "imported": imported}


@frappe.whitelist()
def purge_logs(doctype: str, days: int = 30) -> dict:
	"""Manually purge log records older than `days`."""
	_require_manager()

	allowed = {"AI Execution Log", "AI Service Health Log", "AI Audit Log", "AI Search Query"}
	if doctype not in allowed:
		frappe.throw(_("{0} is not a purgeable log type.").format(doctype))

	from frappe.utils import add_days, today

	cutoff = add_days(today(), -cint(days))
	count = frappe.db.count(doctype, {"creation": ["<", cutoff]})
	frappe.db.delete(doctype, {"creation": ["<", cutoff]})

	from ai_fr_hg.ai.logging import write_audit_log

	write_audit_log(
		action="Logs Purged",
		category="Data",
		severity="Warning",
		message=f"{count} {doctype} records older than {days} days were deleted.",
	)
	return {"doctype": doctype, "deleted": count}


@frappe.whitelist()
def get_system_status() -> dict:
	"""Overall platform readiness, used by the setup checklist."""
	settings = frappe.get_cached_doc("AI Platform Settings")

	providers = frappe.db.count("AI Provider", {"enabled": 1})
	online = frappe.db.count("AI Provider", {"enabled": 1, "status": "Online"})
	chat_models = frappe.db.count("AI Model", {"enabled": 1, "model_type": "Chat"})
	embedding_models = frappe.db.count("AI Model", {"enabled": 1, "model_type": "Embedding"})

	checks = [
		{
			"label": _("Platform enabled"),
			"status": bool(settings.platform_enabled),
			"hint": _("Enable the platform in AI Platform Settings."),
		},
		{
			"label": _("A provider is configured"),
			"status": providers > 0,
			"hint": _("Create an AI Provider pointing at your local runtime."),
		},
		{
			"label": _("A provider is reachable"),
			"status": online > 0,
			"hint": _("Start your local runtime, then run Test All Providers."),
		},
		{
			"label": _("A chat model is registered"),
			"status": chat_models > 0,
			"hint": _("Run Discover Models on your provider."),
		},
		{
			"label": _("An embedding model is registered"),
			"status": embedding_models > 0,
			"hint": _("Install an embedding model, e.g. `ollama pull nomic-embed-text`."),
		},
		{
			"label": _("A default chat model is selected"),
			"status": bool(settings.default_chat_model),
			"hint": _("Set Default Chat Model in AI Platform Settings."),
		},
		{
			"label": _("A knowledge base exists"),
			"status": frappe.db.count("AI Knowledge Base", {"enabled": 1}) > 0,
			"hint": _("Create a knowledge base and upload documents."),
		},
		{
			"label": _("An agent is configured"),
			"status": frappe.db.count("AI Agent", {"enabled": 1}) > 0,
			"hint": _("Create an AI Agent to enable chat."),
		},
	]

	return {
		"ready": all(check["status"] for check in checks),
		"checks": checks,
		"offline_mode": bool(settings.offline_mode),
	}
