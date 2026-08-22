# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Automatic recovery for interrupted background resource operations."""

from __future__ import annotations

import time

import frappe
from frappe.utils import now_datetime

from ai_fr_hg.ai.resources.catalog import ACTIVE_DOWNLOAD_STATUSES
from ai_fr_hg.ai.resources.download import STALLED_AFTER_SECONDS


def recover_interrupted_downloads() -> int:
	"""Mark work that stopped heartbeating as retryable, then restore checkpoints.

	Returns the number of downloads moved to a recoverable state. The scheduler
	calls this on a short cadence; it never starts new download jobs, so it
	cannot create a thundering herd after a worker restart.
	"""
	recovered = 0
	stalled = _stalled_downloads()
	for row in stalled:
		try:
			frappe.db.set_value(
				"AI Resource Download",
				row.name,
				{
					"status": "Retrying",
					"stage": "Recovered from stale worker",
					"pause_requested": 0,
					"is_error": 1,
					"error_message": "Worker stopped updating this download. Resume or retry to continue.",
					"heartbeat": now_datetime(),
				},
				update_modified=False,
			)
			from ai_fr_hg.ai.resources.download import write_event

			write_event(row.name, "Recover", "Stale download detected and marked for retry.", severity="Warning")
			recovered += 1
		except Exception:
			frappe.log_error(title="AI Resource recovery failed", message=frappe.get_traceback())
	frappe.db.commit()  # nosemgrep: frappe-manual-commit
	return recovered


def _stalled_downloads() -> list[dict]:
	try:
		rows = frappe.get_all(
			"AI Resource Download",
			filters={"status": ("in", ACTIVE_DOWNLOAD_STATUSES), "is_cancelled": 0},
			fields=["name", "heartbeat", "status"],
		)
	except Exception:
		return []
	stalled = []
	for row in rows:
		heartbeat = row.get("heartbeat")
		if not heartbeat:
			continue
		try:
			delta = (now_datetime() - heartbeat).total_seconds()
		except Exception:
			continue
		if delta >= STALLED_AFTER_SECONDS:
			stalled.append(row)
	return stalled


def retry_download(download_name: str, user: str | None = None) -> dict:
	"""Retry a failed/stalled download from its checkpoint."""
	from ai_fr_hg.ai.resources.download import resume_download

	user = user or frappe.session.user
	frappe.db.set_value("AI Resource Download", download_name, "status", "Retrying", update_modified=False)
	return resume_download(download_name, user=user)
