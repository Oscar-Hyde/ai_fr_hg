# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Scheduled background tasks."""

import frappe
from frappe.utils import add_days, cint, now_datetime, today


def health_check() -> None:
	"""Probe provider availability. Runs on the interval set in settings.

	Wraps the provider probe so a slow or failing Ollama does not block the
	scheduler queue and starve the realtime (socket.io) worker. Returning to
	Desk triggers a scheduler tick in some bench setups; a stuck health check
	there would manifest as an xhr poll error.
	"""
	try:
		settings = frappe.get_cached_doc("AI Platform Settings")
		if not settings.platform_enabled or not settings.health_check_enabled:
			return

		interval = cint(settings.health_check_interval_minutes) or 15
		minute = now_datetime().minute
		# The cron fires every 5 minutes; only act on the configured cadence.
		if interval > 5 and minute % interval >= 5:
			return

		from ai_fr_hg.ai.monitoring import check_all_providers

		check_all_providers()
	except Exception:
		try:
			frappe.log_error(title="AI health_check failed", message=frappe.get_traceback())
		except Exception:
			pass


def sync_models() -> None:
	"""Reconcile registered models with what each runtime actually has."""
	try:
		if not frappe.db.get_single_value("AI Platform Settings", "platform_enabled"):
			return

		from ai_fr_hg.ai.monitoring import sync_all_models

		sync_all_models()
	except Exception:
		try:
			frappe.log_error(title="AI sync_models failed", message=frappe.get_traceback())
		except Exception:
			pass


def process_pending_documents() -> None:
	"""Retry documents stuck in Queued, and re-embed stale chunks."""
	try:
		if not frappe.db.get_single_value("AI Platform Settings", "platform_enabled"):
			return

		from ai_fr_hg.ai.ingestion import process_pending_documents as reconcile_documents

		# Authority, retry limits, queue deduplication, and stale-job inspection all
		# belong to the canonical ingestion service.
		reconcile_documents()

		# Chunks that were created but never embedded, e.g. the runtime was down.
		unembedded = frappe.get_all(
			"AI Document Chunk",
			filters={"embedding": ["in", ["", None]]},
			fields=["name"],
			limit=200,
		)
		if unembedded:
			from ai_fr_hg.ai.knowledge import embed_chunks

			frappe.enqueue(
				"ai_fr_hg.ai.knowledge.embed_chunks",
				queue="long",
				timeout=3600,
				job_id="ai_backfill_embeddings",
				deduplicate=True,
				chunk_names=[row.name for row in unembedded],
			)
	except Exception:
		try:
			frappe.log_error(title="AI process_pending_documents failed", message=frappe.get_traceback())
		except Exception:
			pass


def scan_pending_pattern_entities() -> None:
	"""Backfill high-precision pattern entities for indexed documents.

	Strictly opt-in: nothing runs until "Auto Pattern Scan" is enabled in AI
	Platform Settings. The scan itself only reads already-extracted content
	and writes the pattern layer's own AI Pattern Entity rows.
	"""
	try:
		if not frappe.db.get_single_value("AI Platform Settings", "platform_enabled"):
			return
		if not frappe.db.get_single_value("AI Platform Settings", "auto_scan_patterns"):
			return

		from ai_fr_hg.ai.patterns import scan_pending_documents

		scan_pending_documents()
	except Exception:
		try:
			frappe.log_error(title="AI scan_pending_pattern_entities failed", message=frappe.get_traceback())
		except Exception:
			pass


def run_scheduled_pipelines() -> None:
	"""Start pipelines whose cron schedule is due."""
	if not frappe.db.get_single_value("AI Platform Settings", "platform_enabled"):
		return

	from ai_fr_hg.ai.pipeline import claim_due_scheduled_pipelines, run_pipeline

	for name in claim_due_scheduled_pipelines():
		try:
			run_pipeline(name, trigger_source="Schedule")
		except Exception:
			frappe.log_error(title=f"AI scheduled pipeline failed: {name}", message=frappe.get_traceback())


def run_due_tasks() -> None:
	"""Claim Open AI Tasks whose due date has passed."""
	if not frappe.db.get_single_value("AI Platform Settings", "platform_enabled"):
		return
	from ai_fr_hg.ai.tasks import claim_due_tasks

	claim_due_tasks()


def rollup_usage() -> None:
	"""Recompute yesterday's usage snapshots from the execution log."""
	if not frappe.db.get_single_value("AI Platform Settings", "platform_enabled"):
		return

	yesterday = add_days(today(), -1)
	rows = frappe.db.sql(
		"""
		select
			user, model,
			count(*) as request_count,
			coalesce(sum(total_tokens), 0) as total_tokens,
			coalesce(sum(case when status = 'Failed' then 1 else 0 end), 0) as failure_count,
			coalesce(avg(duration_ms), 0) as average_latency_ms
		from `tabAI Execution Log`
		where date(creation) = %s and model is not null
		group by user, model
		""",
		(yesterday,),
		as_dict=True,
	)

	for row in rows:
		name = frappe.db.get_value(
			"AI Usage Snapshot",
			{"snapshot_date": yesterday, "user": row.user, "model": row.model},
			"name",
		)
		values = {
			"request_count": cint(row.request_count),
			"total_tokens": cint(row.total_tokens),
			"failure_count": cint(row.failure_count),
			"average_latency_ms": round(float(row.average_latency_ms or 0), 2),
		}
		if name:
			frappe.db.set_value("AI Usage Snapshot", name, values, update_modified=False)
		else:
			doc = frappe.new_doc("AI Usage Snapshot")
			doc.update({"snapshot_date": yesterday, "user": row.user, "model": row.model, **values})
			doc.flags.ignore_permissions = True
			doc.insert(ignore_permissions=True)

	frappe.db.commit()  # nosemgrep: frappe-manual-commit


def cleanup_logs() -> None:
	"""Enforce the retention windows configured in AI Platform Settings."""
	settings = frappe.get_cached_doc("AI Platform Settings")

	retention = {
		"AI Execution Log": cint(settings.execution_log_retention_days),
		"AI Service Health Log": cint(settings.health_log_retention_days),
		"AI Audit Log": cint(settings.audit_log_retention_days),
	}

	for doctype, days in retention.items():
		if not days:
			continue
		cutoff = add_days(today(), -days)
		frappe.db.delete(doctype, {"creation": ["<", cutoff]})

	# Search queries are diagnostic only; keep a short window.
	frappe.db.delete("AI Search Query", {"creation": ["<", add_days(today(), -30)]})
	frappe.db.commit()  # nosemgrep: frappe-manual-commit


def backup_knowledge() -> None:
	"""Export knowledge bases to the site's private files, when enabled."""
	settings = frappe.get_cached_doc("AI Platform Settings")
	if not settings.auto_backup_enabled:
		return

	from ai_fr_hg.api.admin import export_knowledge_base

	for kb in frappe.get_all("AI Knowledge Base", filters={"enabled": 1}, pluck="name"):
		try:
			export_knowledge_base(kb, include_embeddings=False)
		except Exception:
			frappe.log_error(title=f"AI knowledge backup failed: {kb}", message=frappe.get_traceback())
