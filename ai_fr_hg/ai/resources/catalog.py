# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Resource discovery, catalog seeding and compatibility snapshot."""

from __future__ import annotations

import json
from pathlib import Path

import frappe
from frappe import _
from frappe.utils import cint, flt, now_datetime

from ai_fr_hg.ai.resources.paths import bundle_path, bundles_dir

#: Canonical resource types exposed by the marketplace.
RESOURCE_TYPES = (
	"Translation Package",
	"Translation Memory Pack",
	"AI Model",
	"AI Prompt Template",
	"AI Workflow Template",
	"Agent Capability",
	"Language Pack",
	"Knowledge Resource",
	"AI Extension",
)

#: Canonical catalog/repository names used by the built-in marketplace.
BUILTIN_REPOSITORY = "Built-in Marketplace"
BUILTIN_PUBLISHER = "Ai Fr Hg"

#: Terminal statuses that mean "no longer in the busy queue".
TERMINAL_DOWNLOAD_STATUSES = ("Completed", "Cancelled", "Failed", "Removed")
#: Statuses considered "actively in flight" for the live Downloads panel.
ACTIVE_DOWNLOAD_STATUSES = (
	"Queued",
	"Preparing",
	"Downloading",
	"Verifying",
	"Installing",
	"Registering",
	"Activating",
	"Ready",
	"Paused",
	"Retrying",
	"Waiting Dependencies",
)


def expand_resource_code(resource_code: str) -> str:
	"""Normalise an arbitrary resource code to a safe filesystem token."""
	normalised = "".join(ch for ch in str(resource_code or "") if ch.isalnum() or ch in "._-")
	return normalised.strip(".-") or "resource"


def package_manifest(resource_code: str) -> dict:
	"""Load and validate the canonical package manifest for a resource.

	Built-in resources read from the app bundle directory. External resources
	read from the downloaded site-private package after download; the catalog
	only stores the manifest metadata directly on ``AI Resource``.
	"""
	path = bundle_path(resource_code)
	if not path.exists():
		frappe.throw(_("Resource package {0} is not available.").format(resource_code), frappe.DoesNotExistError)

	with Path(path).open("r", encoding="utf-8") as handle:
		manifest = json.load(handle)
	if manifest.get("schema") != "ai-resource-package-v1":
		frappe.throw(_("Resource package {0} uses an unsupported schema.").format(resource_code))
	if not manifest.get("resource_code"):
		frappe.throw(_("Resource package {0} is missing its resource code.").format(resource_code))
	return manifest


def bundle_manifest_hash(resource_code: str) -> str:
	"""SHA-256 of the canonical bundle file, used for checksum verification."""
	import hashlib

	path = bundle_path(resource_code)
	if not path.exists():
		return ""
	data = Path(path).read_bytes()
	return hashlib.sha256(data).hexdigest()


def builtin_bundle_digest(resource_code: str) -> dict:
	"""Return the checksum and signature for a built-in bundle."""
	from ai_fr_hg.ai.resources.verification import compute_package_digest

	path = bundle_path(resource_code)
	if not path.exists():
		frappe.throw(_("Resource package {0} is not available.").format(resource_code), frappe.DoesNotExistError)
	return compute_package_digest(Path(path).read_bytes())


def refresh_builtin_catalog(user: str | None = None) -> dict:
	"""Create or update the built-in repository and its resources.

	Also computes checksums/signatures so even a first install has tamper
	detection. Returns a short summary for audit messages.
	"""
	repository = _ensure_builtin_repository()
	created = updated = skipped = 0

	for path in sorted(bundles_dir().glob("*.json")):
		try:
			manifest = package_manifest(path.stem)
		except Exception:
			frappe.log_error(
				title=f"Resource catalog skipped invalid bundle {path.name}",
				message=frappe.get_traceback(),
			)
			skipped += 1
			continue

		resource_code = manifest.get("resource_code") or path.stem
		digest = builtin_bundle_digest(resource_code)
		if frappe.db.exists("AI Resource", resource_code):
			_upsert_resource(resource_code, manifest, repository, digest)
			updated += 1
		else:
			_upsert_resource(resource_code, manifest, repository, digest)
			created += 1

		_ensure_version(resource_code, manifest, digest, repository)

	frappe.db.commit()  # nosemgrep: frappe-manual-commit

	return {
		"repository": repository,
		"created": created,
		"updated": updated,
		"skipped": skipped,
		"user": user or frappe.session.user,
		"timestamp": now_datetime().isoformat(),
	}


def _ensure_builtin_repository() -> str:
	"""Return the built-in repository name, creating it on first run."""
	if frappe.db.exists("AI Resource Repository", BUILTIN_REPOSITORY):
		return BUILTIN_REPOSITORY

	doc = frappe.new_doc("AI Resource Repository")
	doc.update(
		{
			"repository_name": BUILTIN_REPOSITORY,
			"enabled": 1,
			"is_builtin": 1,
			"repository_type": "Built-in",
			"description": _("The vetted resource catalog bundled with this app."),
		}
	)
	doc.flags.ignore_permissions = True
	doc.insert(ignore_permissions=True)
	return doc.name


def _upsert_resource(resource_code: str, manifest: dict, repository: str, digest: dict) -> str:
	"""Create or update the marketplace record for one built-in bundle."""
	requires = manifest.get("requires") or {}
	fields = {
		"resource_code": resource_code,
		"resource_name": manifest.get("resource_name") or resource_code,
		"resource_type": manifest.get("resource_type") or "AI Extension",
		"category": manifest.get("category") or "Other",
		"version": manifest.get("version") or "0.0.0",
		"publisher": manifest.get("publisher") or BUILTIN_PUBLISHER,
		"repository": repository,
		"source_url": f"builtin://{resource_code}",
		"description": manifest.get("description") or "",
		"release_notes": "\n".join(manifest.get("release_notes") or []),
		"license": manifest.get("license") or "MIT",
		"license_url": manifest.get("license_url") or "",
		"sha256": digest.get("sha256") or "",
		"signature": digest.get("signature") or "",
		"signature_verified": bool(digest.get("signature")),
		"package_size_mb": flt(digest.get("size_mb")),
		"min_disk_gb": flt(requires.get("min_disk_gb")),
		"min_ram_gb": flt(requires.get("min_ram_gb")),
		"min_frappe_version": requires.get("frappe") or "",
		"min_python_version": requires.get("python") or "",
		"last_updated": now_datetime(),
		"deprecated": 0,
		"security_restricted": 0,
		"is_builtin": 1,
		"enabled": 1,
	}
	if frappe.db.exists("AI Resource", resource_code):
		doc = frappe.get_doc("AI Resource", resource_code)
	else:
		doc = frappe.new_doc("AI Resource")
		doc.resource_code = resource_code

	doc.update(fields)

	for language in manifest.get("supported_languages") or []:
		existing = next((row for row in doc.supported_languages if row.language_code == language), None)
		if existing:
			existing.language_name = _language_label(language)
		else:
			doc.append("supported_languages", {"language_code": language, "language_name": _language_label(language)})
	for provider in manifest.get("supported_providers") or []:
		existing = next((row for row in doc.supported_providers if row.provider_name == provider), None)
		if existing:
			existing.required = 1
		else:
			doc.append("supported_providers", {"provider_name": provider, "required": 1})

	_sync_dependencies(doc, manifest.get("dependencies") or [])

	doc.flags.ignore_permissions = True
	if doc.get("__islocal"):
		doc.insert(ignore_permissions=True)
	else:
		doc.save(ignore_permissions=True)
	return doc.name


def _sync_dependencies(doc, dependencies: list) -> None:
	required_codes = {item.get("resource_code") for item in dependencies}
	existing = list(enumerate(doc.dependencies or []))
	for index, row in reversed(existing):
		if row.resource_code not in required_codes:
			doc.dependencies.pop(index)
	for item in dependencies:
		resource_code = item.get("resource_code")
		if any(row.resource_code == resource_code for row in doc.dependencies):
			continue
		doc.append(
			"dependencies",
			{
				"resource_code": resource_code,
				"version_constraint": item.get("version_constraint") or "",
				"required": cint(item.get("required", 1)),
			},
		)


def _ensure_version(resource_code: str, manifest: dict, digest: dict, repository: str) -> None:
	"""Track the catalog version as an installable/rollback version record."""
	version = manifest.get("version") or "0.0.0"
	if frappe.db.exists(
		"AI Resource Version",
		{"resource": resource_code, "version": version},
	):
		return
	for existing in frappe.get_all(
		"AI Resource Version",
		filters={"resource": resource_code, "is_current": 1},
		pluck="name",
	):
		frappe.db.set_value("AI Resource Version", existing, "is_current", 0, update_modified=False)
	doc = frappe.new_doc("AI Resource Version")
	doc.update(
		{
			"resource": resource_code,
			"version": version,
			"publisher": manifest.get("publisher") or BUILTIN_PUBLISHER,
			"repository": repository,
			"source_url": f"builtin://{resource_code}",
			"sha256": digest.get("sha256") or "",
			"signature": digest.get("signature") or "",
			"size_mb": flt(digest.get("size_mb")),
			"release_notes": "\n".join(manifest.get("release_notes") or []),
			"is_current": 1,
		}
	)
	doc.flags.ignore_permissions = True
	doc.insert(ignore_permissions=True)


def _language_label(code: str) -> str:
	return {
		"ar": "Arabic",
		"en": "English",
		"he": "Hebrew",
		"es": "Spanish",
		"fr": "French",
		"de": "German",
	}.get(code, code)


def list_catalog(
	filters: dict | None = None,
	*,
	return_fieldnames: tuple[str, ...] | None = None,
) -> list[dict]:
	"""Return available resources with a computed lifecycle status.

	The UI uses this as its primary discovery feed. It intentionally does only
	read work; compatibility is computed against installed resources and the
	runtime configuration without mutating anything.
	"""
	filters = normalize_catalog_filters(filters or {})
	fieldnames = return_fieldnames or (
		"name",
		"resource_code",
		"resource_name",
		"resource_type",
		"category",
		"version",
		"publisher",
		"repository",
		"description",
		"package_size_mb",
		"license",
		"deprecated",
		"security_restricted",
		"enabled",
		"is_builtin",
		"last_updated",
		"min_disk_gb",
		"min_ram_gb",
		"min_frappe_version",
		"min_python_version",
		"sha256",
		"signature",
		"signature_verified",
	)

	rows = frappe.get_all(
		"AI Resource",
		filters=filters,
		fields=list(fieldnames),
		order_by="category asc, resource_name asc",
		limit=200,
	)

	installed = installed_resources_map()
	active_downloads = active_downloads_map()

	result = []
	for row in rows:
		data = dict(row)
		install = installed.get(data["resource_code"])
		download = active_downloads.get(data["resource_code"])
		data.update(
			compute_resource_status(
				data,
				install=install,
				download=download,
				installed=installed,
			)
		)
		result.append(data)
	return result


def normalize_catalog_filters(filters: dict) -> dict:
	"""Keep only safe catalog filters."""
	allowed = {
		"resource_type",
		"category",
		"repository",
		"enabled",
		"deprecated",
		"security_restricted",
		"is_builtin",
	}
	result = {key: value for key, value in (filters or {}).items() if key in allowed and value not in (None, "", [])}
	if "category" in result and not isinstance(result["category"], list):
		result["category"] = str(result["category"])
	return result


def compute_resource_status(row: dict, *, install: dict | None, download: dict | None, installed: dict) -> dict:
	"""Compute the display status for a resource row.

	Returns a dict of status fields the UI can render without doing more work.
	"""
	if row.get("security_restricted"):
		status = "Security Restricted"
		reason = _("Security is restricted for this resource.")
	elif row.get("deprecated"):
		status = "Deprecated"
		reason = _("This resource is no longer recommended.")
	elif download:
		status = "Downloading"
		reason = _("Download is in progress (stage: {0}).").format(download.get("stage") or download.get("status"))
	elif install and install.get("status") == "Active":
		status = "Installed"
		reason = _("Installed version {0}.").format(install.get("version"))
	elif install and install.get("status") == "Update Available":
		status = "Update Available"
		reason = _("Version {0} is available.").format(row.get("version"))
	elif install and install.get("status") == "Update Failed":
		status = "Update Failed"
		reason = _("A previous update failed. Roll back or retry.")
	else:
		row["dependencies"] = _resource_dependency_map(row.get("name"))
		compatibility = evaluate_compatibility(row, installed=installed)
		if not compatibility["compatible"]:
			# Missing dependencies are auto-resolved during install, so they
			# should not block the Download action - only platform/provider
			# incompatibility should.
			dependency_only = evaluate_compatibility(row, installed=installed, ignore_dependencies=True)
			if dependency_only["compatible"]:
				status = "Available"
				reason = _("Compatible. Dependencies will be installed automatically.")
				compatibility = dependency_only
			else:
				status = "Incompatible"
				reason = compatibility["reason"]
		else:
			status = "Available"
			reason = _("Compatible with this installation.")

	return {
		"status": status,
		"reason": reason,
		"compatible": evaluate_compatibility_row(row, installed=installed),
		"requires_update": bool(install and install.get("status") == "Update Available"),
		"is_installed": bool(install and install.get("status") == "Active"),
	}


def evaluate_compatibility(
	resource: dict,
	*,
	installed: dict | None = None,
	ignore_dependencies: bool = False,
) -> dict:
	"""Evaluate whether the current environment can use a resource.

	``ignore_dependencies`` is used by the download engine: a missing dependency
	is not a reason to refuse the main resource because the engine resolves it
	inline before installation.
	"""
	installed = installed or installed_resources_map()
	reason = _("Compatible.")
	compatible = True
	checks = [
		{"check": "Frappe version", "ok": True, "detail": frappe.__version__ or "17"},
		{"check": "Python version", "ok": True, "detail": "3.14"},
	]
	min_frappe = resource.get("min_frappe_version") or resource.get("requires_frappe") or ""
	if min_frappe and not _satisfies(min_frappe, "17.0.0"):
		compatible = False
		reason = _("This resource requires Frappe {0}.").format(min_frappe)
		checks.append({"check": "Frappe version", "ok": False, "detail": min_frappe})

	min_python = resource.get("min_python_version") or ""
	if min_python and ">=3.14,<3.15" in min_python:
		checks.append({"check": "Python version", "ok": True, "detail": _("Python 3.14 only.")})

	if not ignore_dependencies:
		for dependency in resource.get("dependencies", []):
			dep_code = dependency.get("resource_code") or dependency
			if not installed.get(dep_code):
				compatible = False
				if not reason or reason == _("Compatible."):
					reason = _("Missing dependency: {0}.").format(dep_code)
				checks.append({"check": f"Dependency {dep_code}", "ok": False, "detail": _("Not installed.")})
			else:
				checks.append({"check": f"Dependency {dep_code}", "ok": True, "detail": installed[dep_code].get("version")})

	# A compatible resource still requires a registered runtime for model bundles.
	if resource.get("resource_type") == "AI Model":
		provider_found = False
		for provider in resource.get("supported_providers", []):
			name = provider if isinstance(provider, str) else provider.get("provider_name")
			if name and frappe.db.exists("AI Provider", name) and frappe.db.get_value("AI Provider", name, "enabled"):
				provider_found = True
		if not provider_found:
			compatible = False
			reason = _("No compatible AI provider is registered or enabled.")
			checks.append({"check": "AI Provider", "ok": False, "detail": _("Missing.")})

	return {"compatible": compatible, "reason": reason, "checks": checks}


def evaluate_compatibility_row(row: dict, *, installed: dict | None = None) -> dict:
	"""Compatibility for a plain resource dict fetched by ``get_all``."""
	resource = dict(row)
	resource["dependencies"] = [
		{"resource_code": item["resource_code"]} for item in _resource_dependency_map(resource.get("name"))
	]
	return evaluate_compatibility(resource, installed=installed)


def _satisfies(constraint: str, current: str) -> bool:
	"""Tiny semver-ish constraint evaluator for version compatibility."""
	import re

	match = re.match(r"([><=!]*)\s*(\d+(?:\.\d+)*)", str(constraint) or "")
	if not match:
		return True
	op = match.group(1) or ">="
	target = match.group(2)
	current = current.split("+")[0].split("-")[0]
	try:
		parts = lambda value: tuple(int(part) for part in value.split("."))  # noqa: E731
		a, b = parts(current), parts(target)
	except ValueError:
		return True
	if op in ("", ">="):
		return a >= b
	if op == ">":
		return a > b
	if op == "<":
		return a < b
	if op == "<=":
		return a <= b
	if op == "==":
		return a == b
	if op == "!=":
		return a != b
	return True


def installed_resources_map() -> dict[str, dict]:
	"""Map resource_code -> active install record, keyed by canonical code."""
	result = {}
	try:
		rows = frappe.get_all(
			"AI Resource Install",
			filters={"is_active": 1, "status": ("in", ["Active", "Update Available"])},
			fields=["name", "resource", "resource_code", "resource_type", "version", "status", "installed_on"],
		)
	except Exception:
		return result
	for row in rows:
		result[row["resource_code"]] = dict(row)
	return result


def active_downloads_map() -> dict[str, dict]:
	"""Map resource_code -> newest active download record."""
	result = {}
	try:
		rows = frappe.get_all(
			"AI Resource Download",
			filters={"status": ("in", ACTIVE_DOWNLOAD_STATUSES), "is_cancelled": 0},
			fields=["name", "resource", "resource_code", "resource_name", "version", "status", "stage"],
			order_by="creation desc",
		)
	except Exception:
		return result
	for row in rows:
		result.setdefault(row["resource_code"], dict(row))
	return result


def _resource_dependency_map(resource_name: str) -> list[dict]:
	try:
		return frappe.get_all(
			"AI Resource Dependency",
			filters={"parent": resource_name, "parenttype": "AI Resource"},
			fields=["resource_code", "version_constraint", "required"],
		)
	except Exception:
		return []
