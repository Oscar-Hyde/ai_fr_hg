# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class AIResourceDownload(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		completed_at: DF.Datetime | None
		connection_quality: DF.Literal["Unknown", "Poor", "Fair", "Good", "Excellent"]
		corruption_detected: DF.Check
		downloaded_bytes: DF.Int
		error_message: DF.SmallText | None
		eta_seconds: DF.Int
		heartbeat: DF.Datetime | None
		install_message: DF.Data | None
		install_progress: DF.Percent
		install_stage: DF.Data | None
		is_cancelled: DF.Check
		is_completed: DF.Check
		is_dependency: DF.Check
		is_error: DF.Check
		job_id: DF.Data | None
		last_checkpoint: DF.Datetime | None
		naming_series: DF.Literal["RESDL-.YYYY.-.#####"]
		network_status: DF.Data | None
		parent_download: DF.Link | None
		pause_requested: DF.Check
		progress: DF.Percent
		queue_position: DF.Int
		resource: DF.Link
		resource_code: DF.Data
		resource_name: DF.Data
		resource_type: DF.Literal[
			"Translation Package",
			"Translation Memory Pack",
			"AI Model",
			"AI Prompt Template",
			"AI Workflow Template",
			"Agent Capability",
			"Language Pack",
			"Knowledge Resource",
			"AI Extension",
		]
		signature_status: DF.Data | None
		stage: DF.Data | None
		stage_message: DF.SmallText | None
		started_at: DF.Datetime | None
		status: DF.Literal[
			"Queued",
			"Preparing",
			"Downloading",
			"Waiting Dependencies",
			"Verifying",
			"Installing",
			"Registering",
			"Activating",
			"Ready",
			"Completed",
			"Paused",
			"Failed",
			"Cancelled",
			"Removed",
			"Retrying",
		]
		target_records: DF.Code | None
		total_bytes: DF.Int
		transfer_speed_kbps: DF.Int
		user: DF.Link | None
		verify_checksum: DF.Data | None
		verify_message: DF.Data | None
		verify_progress: DF.Percent
		version: DF.Data
	# end: auto-generated types

	def validate(self):
		if self.status == "Completed":
			self.is_completed = 1
		if self.status in ("Cancelled", "Removed"):
			self.is_cancelled = 1

	def pause(self):
		"""Cooperative pause; the running worker stops at the next checkpoint."""
		if self.status in ("Completed", "Cancelled", "Failed", "Removed"):
			frappe.throw(_("A terminal download cannot be paused."))
		self.pause_requested = 1
		self.save()

	def cancel(self):
		"""Cancel an active download (internal; the API endpoint owns permissions)."""
		from ai_fr_hg.ai.resources.download import write_event

		if self.status == "Completed":
			frappe.throw(_("A completed download cannot be cancelled."))
		self.status = "Cancelled"
		self.stage = "Cancelled"
		self.is_cancelled = 1
		self.save()
		write_event(self.name, "Cancel", "Download cancelled.", severity="Warning")
