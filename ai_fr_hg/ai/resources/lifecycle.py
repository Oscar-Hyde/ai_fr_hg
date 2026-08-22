# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Lifecycle operations after installation: update, rollback, remove, monitor."""

from __future__ import annotations

import json

import frappe
from frappe import _
from frappe.utils import cint, now_datetime

from ai_fr_hg.ai.resources.catalog import installed_resources_map


def update_resource(resource_code: str, user: str | None = None) -> dict:
	"""Queue an update for an installed resource whose catalog has a newer version."""
	from ai_fr_hg.ai.resources.download import enqueue_download

	user = user or frappe.session.user
	install = _active_install(resource_code)
	if not install:
		frappe.throw(_("Resource {0} is not installed.").format(resource_code))

	resource = frappe.get_doc("AI Resource", resource_code)
	if resource.version == install.version:
		frappe.throw(_("Resource {0} is already up to date.").format(resource.resource_name))

	if install.status == "Update Available":
		return enqueue_download(resource.name, user=user)

	install.status = "Update Available"
	install.requires_update = 1
	install.flags.ignore_permissions = True
	install.save(ignore_permissions=True)
	write_event_for_install(install.name, "Update", "Update requested.", user=user)
	return enqueue_download(resource.name, version=resource.version, user=user)


def rollback_install(install_name: str, user: str | None = None) -> dict:
	"""Reinstall the previous version snapshot and retire the current install.

	The snapshot is the exact package JSON stored at install time, so a rollback
	never depends on the catalog having the old version online.
	"""
	user = user or frappe.session.user
	install = frappe.get_doc("AI Resource Install", install_name)
	previous_name = frappe.db.get_value(
		"AI Resource Install",
		{
			"resource": install.resource,
			"version": ("!=", install.version),
			"name": ("!=", install_name),
		},
		"name",
		order_by="creation desc",
	)
	if not previous_name:
		frappe.throw(_("There is no earlier version to roll back to."))
	previous = frappe.get_doc("AI Resource Install", previous_name)

	from ai_fr_hg.ai.resources.paths import install_path

	snapshot = install_path(previous.resource_code, previous.version)
	if not snapshot.exists():
		frappe.throw(_("No offline snapshot is available for version {0}.").format(previous.version))

	install.status = "Rollback Pending"
	install.flags.ignore_permissions = True
	install.save(ignore_permissions=True)

	frappe.enqueue(
		"ai_fr_hg.ai.resources.lifecycle._rollback_job",
		queue="long",
		timeout=3600,
		install_name=install_name,
		previous_install_name=previous_name,
		user=user,
	)
	return {"status": "Queued", "install": install_name, "target_version": previous.version, "user": user}


def _rollback_job(install_name: str, previous_install_name: str, user: str) -> None:
	"""Background rollback: reinstall the snapshot and activate the old version."""
	from ai_fr_hg.ai.resources.install import install_manifest
	from ai_fr_hg.ai.resources.paths import install_path

	import json as _json

	current = frappe.get_doc("AI Resource Install", install_name)
	previous = frappe.get_doc("AI Resource Install", previous_install_name)
	snapshot = install_path(previous.resource_code, previous.version)
	manifest = _json.loads(snapshot.read_text(encoding="utf-8"))
	resource = frappe.get_doc("AI Resource", previous.resource)

	synthetic = _synthetic_download(current)
	new_install_name, targets = install_manifest(resource, manifest, synthetic, user)
	frappe.db.set_value("AI Resource Install", current.name, {"status": "Superseded", "is_active": 0}, update_modified=False)
	frappe.db.set_value(
		"AI Resource Install",
		new_install_name,
		{"status": "Active", "is_active": 1, "activated": 1, "target_records": _json.dumps(targets, default=str)},
		update_modified=False,
	)
	frappe.db.set_value(
		"AI Resource Download",
		synthetic.name,
		{"status": "Completed", "is_completed": 1, "stage": "Rollback complete"},
		update_modified=False,
	)
	write_event_for_install(new_install_name, "Rollback", f"Rolled back to {previous.version}.", user=user)
	frappe.db.commit()  # nosemgrep: frappe-manual-commit


def _synthetic_download(install) -> object:
	"""Create a lightweight synthetic download so installers can write events."""
	download = frappe.new_doc("AI Resource Download")
	download.resource = install.resource
	download.resource_code = install.resource_code
	download.resource_name = install.resource_name
	download.resource_type = install.resource_type
	download.version = install.version
	download.status = "Installing"
	download.stage = "Installing package"
	download.is_dependency = 1
	download.user = frappe.session.user
	download.flags.ignore_permissions = True
	download.insert(ignore_permissions=True)
	return download


def uninstall_resource(install_name: str, user: str | None = None) -> dict:
	"""Deactivate an installed resource without deleting user data or targets."""
	user = user or frappe.session.user
	install = frappe.get_doc("AI Resource Install", install_name)
	if install.status in ("Removed",):
		frappe.throw(_("Resource is already removed."))

	_deactivate_targets(install)
	install.status = "Removed"
	install.is_active = 0
	install.activated = 0
	install.removed_on = now_datetime()
	install.removed_by = user
	install.flags.ignore_permissions = True
	install.save(ignore_permissions=True)
	write_event_for_install(install_name, "Remove", "Resource removed from use.", user=user)
	return {"status": "Removed", "install": install.name, "resource": install.resource_code}


def record_resource_use(resource_code: str, delta: int = 1, health: str | None = None) -> None:
	"""Increment usage and optionally record health for monitoring."""
	install = _active_install(resource_code)
	if not install:
		return
	install.use_count = cint(install.use_count) + cint(delta)
	install.last_used = now_datetime()
	if health:
		install.health_status = health
	install.flags.ignore_permissions = True
	install.save(ignore_permissions=True)


def _active_install(resource_code: str):
	install = installed_resources_map().get(resource_code)
	if not install:
		return None
	return frappe.get_doc("AI Resource Install", install["name"])


def _deactivate_targets(install) -> None:
	try:
		targets = json.loads(install.target_records or "[]")
	except Exception:
		targets = []
	for target in targets:
		doctype = target.get("doctype")
		name = target.get("name")
		if not doctype or not name or not frappe.db.exists(doctype, name):
			continue
		if doctype in ("AI Prompt Template", "AI Pipeline", "AI Skill", "AI Knowledge Base", "AI Translation Glossary", "AI Model"):
			doc = frappe.get_doc(doctype, name)
			doc.enabled = 0
			doc.flags.ignore_permissions = True
			doc.save(ignore_permissions=True)


def write_event_for_install(install_name: str, action: str, message: str, *, user: str | None = None) -> None:
	"""Append an event tied to the install record."""
	install = frappe.db.get_value(
		"AI Resource Install",
		install_name,
		["resource", "resource_code", "resource_name", "resource_type", "version"],
		as_dict=True,
	)
	if not install:
		return
	event = frappe.new_doc("AI Resource Event")
	event.update(
		{
			"resource": install.resource,
			"resource_code": install.resource_code,
			"resource_name": install.resource_name,
			"resource_type": install.resource_type,
			"version": install.version,
			"action": action,
			"user": user or frappe.session.user,
			"install": install_name,
			"message": message,
			"details": json.dumps({"install": install_name}, default=str),
		}
	)
	event.flags.ignore_permissions = True
	event.insert(ignore_permissions=True)
