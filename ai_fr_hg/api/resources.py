# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Whitelisted Resource Marketplace endpoints.

Layer responsibilities (Frappe v17 separation):

* API layer - permission checks, request validation, thin calls to services.
* Services - :mod:`ai_fr_hg.ai.resources` owns download/install/lifecycle.
* Storage  - ``AI Resource``, ``AI Resource Download``, ``AI Resource Install`` etc.
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import cint

from ai_fr_hg.ai.resources.catalog import (
	ACTIVE_DOWNLOAD_STATUSES,
	compute_resource_status,
	evaluate_compatibility,
	installed_resources_map,
	list_catalog,
	refresh_builtin_catalog,
)
from ai_fr_hg.ai.resources.download import enqueue_download, resume_download, write_event
from ai_fr_hg.ai.resources.lifecycle import rollback_install, uninstall_resource, update_resource
from ai_fr_hg.ai.resources.monitoring import recommendations, resource_summary, usage_metrics
from ai_fr_hg.ai.resources.recovery import retry_download

_MANAGER_ROLES = ("AI Manager", "System Manager")


def _can_manage() -> bool:
	return frappe.session.user == "Administrator" or any(role in _MANAGER_ROLES for role in frappe.get_roles())


def _require_manage() -> None:
	if not _can_manage():
		frappe.throw(_("You need the AI Manager or System Manager role to manage resources."), frappe.PermissionError)


def _require_view() -> None:
	roles = frappe.get_roles()
	if (
		frappe.session.user == "Administrator"
		or "AI Manager" in roles
		or "System Manager" in roles
		or "AI User" in roles
		or "AI Auditor" in roles
	):
		return
	frappe.throw(_("You do not have access to the resource marketplace."), frappe.PermissionError)


@frappe.whitelist()
def marketplace() -> dict:
	"""Initial dashboard payload: catalog, downloads, installed, updates, usage."""
	_require_view()
	return {
		"summary": resource_summary(),
		"catalog": list_catalog({"enabled": 1}),
		"downloads": downloads(),
		"installed": installed_resources(),
		"updates": available_updates(),
		"recommendations": recommendations(),
	}


@frappe.whitelist()
def catalog(resource_type: str | None = None, category: str | None = None, search: str = "") -> list[dict]:
	"""Browse available resources."""
	_require_view()
	filters = {"enabled": 1}
	if resource_type:
		filters["resource_type"] = resource_type
	if category:
		filters["category"] = category
	rows = list_catalog(filters)
	return _filter_search(rows, search)


@frappe.whitelist()
def resource_detail(name: str) -> dict:
	"""Full metadata, dependencies, versions, compatibility and lifecycle history."""
	_require_view()
	resource = frappe.get_doc("AI Resource", name)
	resource.check_permission("read")
	dependencies = frappe.get_all(
		"AI Resource Dependency",
		filters={"parent": resource.name, "parenttype": "AI Resource"},
		fields=["resource_code", "version_constraint", "required"],
	)
	versions = frappe.get_all(
		"AI Resource Version",
		filters={"resource": resource.name},
		fields=["name", "version", "publisher", "size_mb", "release_notes", "is_installed", "installed_on"],
		order_by="creation desc",
	)
	events = frappe.get_all(
		"AI Resource Event",
		filters={"resource": resource.name},
		fields=["name", "action", "severity", "user", "message", "creation", "version"],
		order_by="creation desc",
		limit=50,
	)
	compat = evaluate_compatibility(resource.as_dict())
	resource_dict = resource.as_dict()
	resource_dict["dependencies"] = dependencies
	resource_dict["versions"] = versions
	resource_dict["events"] = events
	resource_dict["compatibility"] = compat
	resource_dict["installed"] = installed_resources_map().get(resource.resource_code)
	resource_dict.update(
		compute_resource_status(
			resource_dict,
			install=resource_dict["installed"],
			download=None,
			installed=installed_resources_map(),
		)
	)
	return resource_dict


@frappe.whitelist()
def start_download(name: str, version: str | None = None) -> dict:
	"""Request a resource download + install."""
	_require_manage()
	resource = frappe.get_doc("AI Resource", name)
	resource.check_permission("read")
	compat = evaluate_compatibility(resource.as_dict(), ignore_dependencies=True)
	if not compat["compatible"]:
		frappe.throw(_("Resource is not compatible: {0}").format(compat["reason"]))
	from ai_fr_hg.ai.logging import write_audit_log

	write_audit_log(
		action="AI Resource Download Requested",
		category="Configuration",
		message=_("Resource download requested: {0}.").format(resource.resource_name),
		details={"resource": resource.resource_code, "version": version or resource.version},
		reference_doctype="AI Resource",
		reference_name=resource.name,
		raise_on_error=True,
	)
	result = enqueue_download(resource.name, version=version, user=frappe.session.user)
	return result


@frappe.whitelist()
def downloads() -> list[dict]:
	"""Active/pending downloads shown in the live panel."""
	_require_view()
	rows = frappe.get_all(
		"AI Resource Download",
		filters={"status": ("in", ACTIVE_DOWNLOAD_STATUSES), "is_cancelled": 0},
		fields=[
			"name",
			"resource",
			"resource_code",
			"resource_name",
			"resource_type",
			"version",
			"status",
			"stage",
			"stage_message",
			"progress",
			"downloaded_bytes",
			"total_bytes",
			"transfer_speed_kbps",
			"eta_seconds",
			"network_status",
			"connection_quality",
			"verify_progress",
			"verify_message",
			"install_progress",
			"install_message",
			"error_message",
			"is_dependency",
			"parent_download",
			"user",
			"creation",
		],
		order_by="creation asc",
	)
	for position, row in enumerate(rows, start=1):
		row["queue_position"] = position
	return rows


@frappe.whitelist()
def download_history(limit: int = 50) -> list[dict]:
	"""Completed, failed, cancelled and removed downloads (history view)."""
	_require_view()
	return frappe.get_all(
		"AI Resource Download",
		filters={"status": ("not in", ACTIVE_DOWNLOAD_STATUSES)},
		fields=["name", "resource_code", "resource_name", "version", "status", "stage", "progress", "error_message", "user", "creation", "heartbeat"],
		order_by="creation desc",
		limit=cint(limit) or 50,
	)


@frappe.whitelist()
def download_detail(name: str) -> dict:
	"""One download's full lifecycle state."""
	_require_view()
	download = frappe.get_doc("AI Resource Download", name)
	download.check_permission("read")
	download_dict = download.as_dict()
	events = frappe.get_all(
		"AI Resource Event",
		filters={"download": name},
		fields=["name", "action", "severity", "message", "user", "creation"],
		order_by="creation desc",
		limit=50,
	)
	download_dict["events"] = events
	return download_dict


@frappe.whitelist()
def pause_download(name: str) -> dict:
	"""Pause an active download at its current checkpoint."""
	_require_manage()
	doc = frappe.get_doc("AI Resource Download", name)
	if doc.status not in ACTIVE_DOWNLOAD_STATUSES or doc.status == "Paused":
		frappe.throw(_("That download cannot be paused."))
	frappe.db.set_value("AI Resource Download", name, "pause_requested", 1, update_modified=False)
	write_event(name, "Pause", "Pause requested.", severity="Warning")
	return {"status": "Pause Requested"}


@frappe.whitelist()
def resume_download_api(name: str) -> dict:
	"""Resume a paused or failed download."""
	_require_manage()
	return resume_download(name, user=frappe.session.user)


@frappe.whitelist()
def retry_download_api(name: str) -> dict:
	"""Retry a failed or stalled download."""
	_require_manage()
	return retry_download(name, user=frappe.session.user)


@frappe.whitelist()
def cancel_download(name: str) -> dict:
	"""Cancel a download and clean up its active status."""
	_require_manage()
	doc = frappe.get_doc("AI Resource Download", name)
	if doc.status == "Completed":
		frappe.throw(_("A completed download is not cancellable."))
	frappe.db.set_value(
		"AI Resource Download",
		name,
		{"status": "Cancelled", "stage": "Cancelled", "is_cancelled": 1, "pause_requested": 0},
		update_modified=False,
	)
	write_event(name, "Cancel", "Download cancelled.", severity="Warning")
	return {"status": "Cancelled"}


@frappe.whitelist()
def installed_resources() -> list[dict]:
	"""Installed resources, newest first."""
	_require_view()
	return frappe.get_all(
		"AI Resource Install",
		filters={"is_active": 1},
		fields=[
			"name",
			"resource",
			"resource_code",
			"resource_name",
			"resource_type",
			"version",
			"status",
			"installed_by",
			"installed_on",
			"use_count",
			"last_used",
			"health_status",
			"requires_update",
		],
		order_by="creation desc",
	)


@frappe.whitelist()
def available_updates() -> list[dict]:
	"""Installed resources that have a newer catalog version."""
	_require_view()
	rows = frappe.get_all("AI Resource Install", filters={"is_active": 1, "status": "Update Available"}, fields=["*"], limit=200)
	return rows


@frappe.whitelist()
def update_resource_api(name: str) -> dict:
	"""Update an installed resource to the newest catalog version."""
	_require_manage()
	return update_resource(name, user=frappe.session.user)


@frappe.whitelist()
def rollback_api(install_name: str) -> dict:
	"""Roll back an install to the previous snapshot."""
	_require_manage()
	return rollback_install(install_name, user=frappe.session.user)


@frappe.whitelist()
def remove_api(install_name: str) -> dict:
	"""Remove/deactivate an installed resource."""
	_require_manage()
	return uninstall_resource(install_name, user=frappe.session.user)


@frappe.whitelist()
def history(resource_code: str, limit: int = 100) -> dict:
	"""Full event + version history for one resource."""
	_require_view()
	resource = frappe.db.get_value("AI Resource", {"resource_code": resource_code}, "name")
	if not resource:
		frappe.throw(_("Resource not found."), frappe.DoesNotExistError)
	events = frappe.get_all(
		"AI Resource Event",
		filters={"resource": resource},
		fields=["name", "action", "severity", "user", "message", "creation", "version"],
		order_by="creation desc",
		limit=cint(limit) or 100,
	)
	versions = frappe.get_all(
		"AI Resource Version",
		filters={"resource": resource},
		fields=["name", "version", "publisher", "size_mb", "release_notes", "is_installed", "installed_on"],
		order_by="creation desc",
	)
	installs = frappe.get_all(
		"AI Resource Install",
		filters={"resource": resource},
		fields=["name", "version", "status", "installed_by", "installed_on", "removed_on", "use_count", "health_status"],
		order_by="creation desc",
	)
	return {"resource": resource, "events": events, "versions": versions, "installs": installs}


@frappe.whitelist()
def usage() -> dict:
	"""Usage and health metrics for installed resources."""
	_require_view()
	return {"summary": resource_summary(), "metrics": usage_metrics()}


@frappe.whitelist()
def recommendations_api(limit: int = 6) -> list[dict]:
	"""Smart recommendations based on usage and capability gaps."""
	_require_view()
	return recommendations(limit=cint(limit) or 6)


@frappe.whitelist()
def repositories() -> list[dict]:
	"""Active resource repositories."""
	_require_view()
	return frappe.get_all(
		"AI Resource Repository",
		filters={"enabled": 1},
		fields=["name", "repository_name", "repository_type", "description", "is_builtin", "last_synced"],
	)


@frappe.whitelist()
def sync_catalog() -> dict:
	"""Rescan built-in bundles and refresh catalog metadata (manager only)."""
	_require_manage()
	result = refresh_builtin_catalog(user=frappe.session.user)
	from ai_fr_hg.ai.logging import write_audit_log

	write_audit_log(
		action="AI Resource Catalog Synced",
		category="Configuration",
		message=_("Built-in resource catalog refreshed."),
		details=result,
		reference_doctype="AI Resource",
		raise_on_error=True,
	)
	return result


def _filter_search(rows: list[dict], search: str) -> list[dict]:
	search = (search or "").strip().lower()
	if not search:
		return rows
	return [
		row
		for row in rows
		if search in (row.get("resource_name") or "").lower()
		or search in (row.get("resource_code") or "").lower()
		or search in (row.get("description") or "").lower()
		or search in (row.get("publisher") or "").lower()
	]
