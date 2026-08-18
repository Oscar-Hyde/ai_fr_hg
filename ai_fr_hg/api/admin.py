# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Whitelisted administration endpoints.

Every method here is restricted to AI Manager / System Manager.
"""

import hashlib
import json

import frappe
from frappe import _
from frappe.utils import cint, now_datetime


def _require_manager() -> None:
	frappe.only_for(["AI Manager", "System Manager"])


def _validate_pull_parameters(provider, model_name, *, require_provider: bool = True) -> tuple[str, str]:
	if not isinstance(provider, str) or not provider.strip():
		frappe.throw(_("A valid AI Provider is required."))
	if not isinstance(model_name, str) or not model_name.strip():
		frappe.throw(_("A model name is required."))
	provider = provider.strip()
	model_name = model_name.strip()
	if len(provider) > 140 or len(model_name) > 255:
		frappe.throw(_("The provider or model name exceeds the supported length."))
	if any(ord(character) < 32 or ord(character) == 127 for character in model_name):
		frappe.throw(_("The model name cannot contain control characters."))
	if require_provider and not frappe.db.exists("AI Provider", provider):
		frappe.throw(_("AI Provider {0} does not exist.").format(provider), frappe.DoesNotExistError)
	return provider, model_name


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

	from ai_fr_hg.ai.governance import check_capability
	from ai_fr_hg.ai.logging import write_audit_log
	from ai_fr_hg.ai.monitoring import sync_provider_models

	check_capability("model_management")
	result = sync_provider_models(provider, create_missing=cint(create_missing))
	write_audit_log(
		action="Provider Models Discovered",
		category="Configuration",
		message=_("Local models were reconciled for provider {0}.").format(provider),
		details=result,
		reference_doctype="AI Provider",
		reference_name=provider,
		raise_on_error=True,
	)
	return result


@frappe.whitelist()
def pull_model(provider: str, model_name: str) -> dict:
	"""Download a model onto a runtime that supports pulling."""
	_require_manager()

	from ai_fr_hg.ai.governance import check_capability

	check_capability("model_management")
	provider, model_name = _validate_pull_parameters(provider, model_name)

	from ai_fr_hg.ai.logging import write_audit_log

	write_audit_log(
		action="Model Pull Requested",
		category="Configuration",
		severity="Warning",
		message=_("A local model pull was requested for {0} on {1}.").format(model_name, provider),
		details={"provider": provider, "model": model_name},
		reference_doctype="AI Provider",
		reference_name=provider,
		raise_on_error=True,
	)

	model_key = hashlib.sha256(f"{provider}\0{model_name}".encode()).hexdigest()[:24]
	frappe.enqueue(
		"ai_fr_hg.api.admin._pull_model_job",
		queue="long",
		timeout=7200,
		job_id=f"ai_pull_{frappe.scrub(provider)[:40]}_{model_key}",
		deduplicate=True,
		enqueue_after_commit=True,
		provider=provider,
		model_name=model_name,
		user=frappe.session.user,
	)
	return {"status": "Queued", "provider": provider, "model": model_name}


def _pull_model_job(provider: str, model_name: str, user: str) -> None:
	"""Pull and register a local model with durable start/terminal provenance."""
	from ai_fr_hg.ai.governance import check_capability
	from ai_fr_hg.ai.logging import write_audit_log
	from ai_fr_hg.ai.monitoring import sync_provider_models
	from ai_fr_hg.ai.providers import get_provider

	if frappe.session.user != user:
		frappe.throw(_("The model pull worker requester does not match its execution authority."), frappe.PermissionError)
	_require_manager()
	check_capability("model_management", user=user)
	provider, model_name = _validate_pull_parameters(provider, model_name, require_provider=False)

	# Pulling changes an external local runtime and cannot participate in the
	# MariaDB transaction. Persist intent before invoking that side effect so a
	# worker crash remains operationally reconstructable.
	write_audit_log(
		action="Model Pull Started",
		category="Configuration",
		severity="Warning",
		message=_("Started pulling local model {0} onto {1}.").format(model_name, provider),
		details={"provider": provider, "model": model_name, "requested_by": user},
		reference_doctype="AI Provider",
		reference_name=provider,
		raise_on_error=True,
	)
	frappe.db.commit()  # nosemgrep: external-side-effect-provenance-boundary

	try:
		adapter = get_provider(provider)
		adapter.pull_model(model_name)
		result = sync_provider_models(provider)
		write_audit_log(
			action="Model Pulled",
			category="Configuration",
			message=f"Model {model_name} downloaded onto {provider}.",
			details={**result, "requested_by": user},
			reference_doctype="AI Provider",
			reference_name=provider,
			raise_on_error=True,
		)
		# The pull is irreversible from MariaDB's perspective. Commit its terminal
		# registry and audit state before optional realtime notification.
		frappe.db.commit()  # nosemgrep: external-side-effect-provenance-boundary
	except Exception as exc:
		# Do not retain a partial model-registry reconciliation. The already
		# committed Started record still proves the external pull was attempted.
		frappe.db.rollback()
		write_audit_log(
			action="Model Pull Failed",
			category="Configuration",
			severity="Critical",
			message=str(exc)[:1000],
			details={"provider": provider, "model": model_name, "requested_by": user},
			reference_doctype="AI Provider",
			reference_name=provider,
			raise_on_error=True,
		)
		# The worker must fail for queue observability, so persist the terminal
		# audit before re-raising into Frappe's worker rollback boundary.
		frappe.db.commit()  # nosemgrep: external-side-effect-provenance-boundary
		frappe.log_error(title=f"AI model pull failed: {model_name}", message=frappe.get_traceback())
		try:
			frappe.publish_realtime(
				"ai_model_pulled",
				{"provider": provider, "model": model_name, "status": "Failed", "error": str(exc)},
				user=user,
			)
		except Exception:
			frappe.log_error(title="AI model pull failure notification failed", message=frappe.get_traceback())
		raise

	try:
		frappe.publish_realtime(
			"ai_model_pulled", {"provider": provider, "model": model_name, "status": "Success"}, user=user
		)
	except Exception:
		frappe.log_error(title="AI model pull notification failed", message=frappe.get_traceback())


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
	"""Aggregate manager-only payload powering the AI Control Center."""
	_require_manager()

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
	values = {"since": since, "user": user or None}

	return {
		"daily": frappe.db.sql(
			"""
			select snapshot_date, sum(request_count) as requests, sum(total_tokens) as tokens,
				sum(failure_count) as failures
			from `tabAI Usage Snapshot`
			where snapshot_date >= %(since)s and (%(user)s is null or user = %(user)s)
			group by snapshot_date
			order by snapshot_date asc
			""",
			values,
			as_dict=True,
		),
		"by_model": frappe.db.sql(
			"""
			select model, sum(request_count) as requests, sum(total_tokens) as tokens,
				avg(average_latency_ms) as avg_latency
			from `tabAI Usage Snapshot`
			where snapshot_date >= %(since)s and (%(user)s is null or user = %(user)s)
				and model is not null
			group by model
			order by requests desc
			limit 20
			""",
			values,
			as_dict=True,
		),
		"by_user": frappe.db.sql(
			"""
			select user, sum(request_count) as requests, sum(total_tokens) as tokens
			from `tabAI Usage Snapshot`
			where snapshot_date >= %(since)s and (%(user)s is null or user = %(user)s)
				and user is not null
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
	# Persist via canonical folder service so exports are organized (File & Folder §2, §7)
	try:
		from ai_fr_hg.ai.folders import create_file_with_content, get_default_folder

		folder = get_default_folder(user=frappe.session.user)
		# Prefer the configured AI Platform exports folder if it exists
		preferred = "Home/AI Platform/Exports"
		if frappe.db.exists("File", preferred):
			folder = preferred
		result = create_file_with_content(
			filename,
			frappe.as_json(payload, indent=1),
			folder=folder,
			is_private=1,
			user=frappe.session.user,
		)
		file_doc = frappe.get_doc("File", result["name"])
	except Exception:
		# Fallback to legacy direct creation if folder service fails (graceful degradation)
		frappe.log_error(title="Folder-aware export failed, falling back", message=frappe.get_traceback())
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
		details={
			"file": file_doc.name,
			"document_count": len(documents),
			"include_embeddings": bool(cint(include_embeddings)),
		},
		reference_doctype="AI Knowledge Base",
		reference_name=knowledge_base,
		raise_on_error=True,
	)

	return {"file_url": file_doc.file_url, "documents": len(documents)}


@frappe.whitelist()
def import_knowledge_base(file_url: str, knowledge_base: str | None = None) -> dict:
	"""Import documents from a previously exported knowledge base file."""
	_require_manager()

	from ai_fr_hg.ai.ingestion import get_file_content

	content, filename = get_file_content(file_url)
	try:
		payload = json.loads(content)
	except (TypeError, ValueError) as exc:
		frappe.throw(_("The knowledge export is not valid JSON: {0}").format(exc))
	if not isinstance(payload, dict):
		frappe.throw(_("The knowledge export root must be a JSON object."))
	documents = payload.get("documents") or []
	if not isinstance(documents, list):
		frappe.throw(_("The knowledge export documents value must be a JSON array."))
	if not isinstance(payload.get("knowledge_base") or {}, dict):
		frappe.throw(_("The knowledge export knowledge_base value must be a JSON object."))
	# Validate the complete import before creating records or registering any
	# after-commit jobs. This makes malformed exports fail atomically and with a
	# row-specific error rather than depending on a later DocType exception.
	for index, entry in enumerate(documents, start=1):
		if not isinstance(entry, dict):
			frappe.throw(_("Knowledge export document row {0} must be a JSON object.").format(index))
		if not isinstance(entry.get("title"), str) or not entry["title"].strip():
			frappe.throw(_("Knowledge export document row {0} requires a title.").format(index))
		if not isinstance(entry.get("content"), str) or not entry["content"].strip():
			frappe.throw(_("Knowledge export document row {0} requires text content.").format(index))

	target = knowledge_base or (payload.get("knowledge_base") or {}).get("name")
	if not isinstance(target, str) or not target.strip():
		frappe.throw(_("No valid target knowledge base could be determined."))
	target = target.strip()

	if not frappe.db.exists("AI Knowledge Base", target):
		source = payload.get("knowledge_base") or {}
		kb = frappe.new_doc("AI Knowledge Base")
		kb.knowledge_base_name = target
		for fieldname in ("description", "chunk_size", "chunk_overlap"):
			if source.get(fieldname) is not None:
				kb.set(fieldname, source[fieldname])
		kb.insert()

	imported = 0
	for entry in documents:
		# Exported checksums describe the original source bytes (for example a
		# PDF), while imports intentionally become Text sources. Compare the
		# actual exported text without sending a potentially large LongText value
		# back through an equality filter or trusting the supplied checksum.
		content_checksum = hashlib.sha256(entry["content"].encode("utf-8")).hexdigest()
		duplicate = frappe.db.sql(
			"""
			select name
			from `tabAI Document`
			where knowledge_base = %s and title = %s and sha2(content, 256) = %s
			limit 1
			""",
			(target, entry["title"], content_checksum),
		)
		if duplicate:
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
		# This endpoint always registers its own canonical processing job. Avoid
		# the controller's optional auto-process hook registering a second
		# after-commit callback for the same deterministic job id.
		doc.flags.skip_auto_process = True
		doc.insert()
		imported += 1

		from ai_fr_hg.ai.ingestion import enqueue_processing

		enqueue_processing(doc.name)

	from ai_fr_hg.ai.logging import write_audit_log

	write_audit_log(
		action="Knowledge Base Imported",
		category="Data",
		severity="Warning",
		message=_("Imported {0} documents into knowledge base {1}.").format(imported, target),
		details={"source_file_url": file_url, "source_filename": filename, "document_count": imported},
		reference_doctype="AI Knowledge Base",
		reference_name=target,
		raise_on_error=True,
	)
	return {"knowledge_base": target, "imported": imported}


@frappe.whitelist()
def purge_logs(doctype: str, days: int = 30) -> dict:
	"""Manually purge log records older than `days`."""
	_require_manager()

	allowed = {"AI Execution Log", "AI Service Health Log", "AI Audit Log", "AI Search Query"}
	if doctype not in allowed:
		frappe.throw(_("{0} is not a purgeable log type.").format(doctype))

	from frappe.utils import add_days, today

	days = cint(days)
	if days < 1:
		frappe.throw(_("Log retention days must be at least 1."))
	cutoff = add_days(today(), -days)
	count = frappe.db.count(doctype, {"creation": ["<", cutoff]})
	frappe.db.delete(doctype, {"creation": ["<", cutoff]})

	from ai_fr_hg.ai.logging import write_audit_log

	write_audit_log(
		action="Logs Purged",
		category="Data",
		severity="Warning",
		message=f"{count} {doctype} records older than {days} days were deleted.",
		details={"purged_doctype": doctype, "cutoff": str(cutoff), "deleted": count},
		raise_on_error=True,
	)
	return {"doctype": doctype, "deleted": count}


@frappe.whitelist()
def get_system_status() -> dict:
	"""Manager-only platform readiness used by the setup checklist."""
	_require_manager()

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
