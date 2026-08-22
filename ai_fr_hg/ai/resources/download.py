# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Durable download engine.

Heavy download/verify/install work runs on Frappe background workers. Every
active download is an ``AI Resource Download`` row with durable checkpoints,
so a browser refresh, worker restart or network interruption never loses the
lifecycle. Completed downloads move to ``AI Resource Install`` and are
removed from the active Downloads panel automatically.

Design notes:

* **Cooperative interruption.** Pause and cancel are honoured between chunks
  and stages. The current background job returns; resume/retry enqueues a new
  job from the checkpoint.
* **Dependency-first ordering.** A job resolves missing dependencies inline
  before installing the requested resource, so a multi-resource bundle is
  usable immediately after activation.
* **Checkpoint resume.** Built-in packages resume from the local bundle, and
  HTTP downloads use ``Range`` requests when the server supports them.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import frappe
from frappe import _
from frappe.utils import cint, flt, now_datetime

from ai_fr_hg.ai.resources.catalog import (
	ACTIVE_DOWNLOAD_STATUSES,
	expand_resource_code,
	installed_resources_map,
	package_manifest,
)
from ai_fr_hg.ai.resources.paths import bundle_path, download_path
from ai_fr_hg.ai.resources.verification import validate_manifest

#: A download job that does not make progress for this long is considered lost.
STALLED_AFTER_SECONDS = 60 * 15


def enqueue_download(
	resource_name: str,
	version: str | None = None,
	user: str | None = None,
	source_name: str | None = None,
) -> dict:
	"""Create a download record and enqueue the orchestrated background job.

	``source_name`` selects an ``AI Resource Source`` row; when omitted the
	resource's default enabled source (usually the offline-built-in bundle) is
	used. The selected source is persisted on the download so resume/retry and
	audit traces know exactly where the package came from.
	"""
	from ai_fr_hg.ai.resources.catalog import evaluate_compatibility

	user = user or frappe.session.user
	resource = frappe.get_doc("AI Resource", resource_name)
	source = resolve_download_source(resource, source_name=source_name)

	compat = evaluate_compatibility(resource.as_dict(), ignore_dependencies=True)
	if not compat["compatible"]:
		frappe.throw(_("This resource cannot be installed in the current environment: {0}").format(compat["reason"]))

	existing = _existing_active_download(resource.name)
	if existing:
		return existing.as_dict()

	manifest = _manifest_for(resource, version)
	download = frappe.new_doc("AI Resource Download")
	download.update(
		{
			"resource": resource.name,
			"resource_code": resource.resource_code,
			"resource_name": resource.resource_name,
			"resource_type": resource.resource_type,
			"version": version or resource.version,
			"source": source.get("source_name") or "",
			"source_url": source.get("source_url") or "",
			"repository": source.get("repository") or "",
			"expected_checksum": source.get("checksum") or resource.sha256,
			"expected_signature": source.get("signature") or resource.signature,
			"status": "Preparing",
			"stage": "Preparing Download",
			"progress": 0,
			"downloaded_bytes": 0,
			"total_bytes": source.get("package_size_mb") and int(float(source["package_size_mb"]) * 1024 * 1024)
			or _package_size(manifest, resource),
			"network_status": "Idle",
			"connection_quality": "Unknown",
			"user": user,
			"started_at": now_datetime(),
			"last_checkpoint": now_datetime(),
			"heartbeat": now_datetime(),
		}
	)
	download.flags.ignore_permissions = True
	download.insert(ignore_permissions=True)

	job_id = f"ai_resdl_{expand_resource_code(resource.resource_code)}_{download.name}"
	download.job_id = job_id
	download.save(ignore_permissions=True)

	frappe.enqueue(
		"ai_fr_hg.ai.resources.download._download_resource_job",
		queue="long",
		timeout=7200,
		job_id=job_id,
		deduplicate=True,
		enqueue_after_commit=True,
		download_name=download.name,
		user=user,
	)
	return download.as_dict()


def resume_download(download_name: str, user: str | None = None) -> dict:
	"""Resume a paused or retried download from its last checkpoint."""
	download = frappe.get_doc("AI Resource Download", download_name)
	user = user or frappe.session.user
	if download.status not in ("Paused", "Failed", "Retrying", "Ready"):
		frappe.throw(_("Only paused or failed downloads can be resumed."))
	download.status = "Downloading"
	download.stage = "Resuming Download"
	download.error_message = ""
	download.is_error = 0
	download.heartbeat = now_datetime()
	download.save(ignore_permissions=True)

	frappe.enqueue(
		"ai_fr_hg.ai.resources.download._download_resource_job",
		queue="long",
		timeout=7200,
		job_id=download.job_id or f"ai_resdl_{download.name}",
		deduplicate=True,
		enqueue_after_commit=True,
		download_name=download.name,
		user=user,
	)
	return download.as_dict()


def _existing_active_download(resource_name: str):
	existing = frappe.get_all(
		"AI Resource Download",
		filters={"resource": resource_name, "status": ("in", ACTIVE_DOWNLOAD_STATUSES), "is_cancelled": 0},
		order_by="creation desc",
		limit=1,
	)
	if not existing:
		return None
	return frappe.get_doc("AI Resource Download", existing[0].name)


def resolve_download_source(resource, source_name: str | None = None) -> dict:
	"""Resolve the source to download from.

	Defaults to the first enabled source, ordered by :code:`is_default` then
	priority. Returns a dict shaped for ``AI Resource Download`` fields.
	"""
	try:
		sources = frappe.get_all(
			"AI Resource Source",
			filters={"parent": resource.name, "parenttype": "AI Resource", "enabled": 1},
			fields=[
				"name",
				"source_name",
				"source_type",
				"repository",
				"source_url",
				"is_default",
				"priority",
				"checksum",
				"signature",
				"package_size_mb",
				"offline_supported",
				"requires_authorization",
			],
			order_by="is_default desc, priority asc, creation asc",
			limit=10,
		)
	except Exception:
		sources = []

	if source_name:
		for row in sources:
			if row.get("source_name") == source_name or row.get("name") == source_name:
				return dict(row)
		frappe.throw(_("Source {0} is not enabled for resource {1}.").format(source_name, resource.resource_name))

	if sources:
		return dict(sources[0])

	# Fallback: a manually-curated resource with no source rows still uses its
	# catalog source_url/checksum metadata as a single Enterprise/HTTP source.
	fallback = {
		"name": "catalog-source",
		"source_name": _("Catalog Source"),
		"source_type": "Enterprise" if resource.source_url and not resource.is_builtin else "Built-in",
		"repository": resource.repository or "",
		"source_url": resource.source_url or f"builtin://{resource.resource_code}",
		"is_default": 1,
		"priority": 9,
		"checksum": resource.sha256 or "",
		"signature": resource.signature or "",
		"package_size_mb": resource.package_size_mb or 0,
		"offline_supported": int(bool(resource.is_builtin)),
		"requires_authorization": 0,
	}
	return fallback


def _manifest_for(resource, version: str | None) -> dict:
	if resource.is_builtin:
		return package_manifest(resource.resource_code)
	if version:
		version_doc = frappe.db.exists("AI Resource Version", {"resource": resource.name, "version": version})
		if version_doc:
			return frappe.get_cached_doc("AI Resource Version", version_doc).as_dict()
	return resource.as_dict()


def _package_size(manifest: dict, resource) -> int:
	if resource.is_builtin:
		path = bundle_path(resource.resource_code)
		try:
			return path.stat().st_size
		except Exception:
			return 0
	return cint(manifest.get("package_size_bytes")) or flt(resource.package_size_mb or 0) * 1024 * 1024


def _download_source(download: object) -> dict:
	"""Load the source metadata persisted on a download row."""
	from ai_fr_hg.ai.resources.catalog import resource_sources_map

	resource = frappe.get_doc("AI Resource", download.resource)
	if download.get("source"):
		sources = resource_sources_map(resource.name).get(resource.name, [])
		match = next((row for row in sources if row.get("source_name") == download.source), None)
		if match:
			return dict(match)
	return resolve_download_source(resource, source_name=download.get("source"))


def _download_resource_job(download_name: str, user: str) -> None:
	"""Orchestrate dependencies + one resource download/install/activation."""
	try:
		_set_status(download_name, "Preparing", "Preparing Download")
		_set_status(download_name, "Waiting Dependencies", "Resolving dependencies")
		_resolve_dependencies(download_name, user)
		if _check_interrupt(download_name):
			return
		_download_and_install(download_name, user, depth=0)
	except Exception:
		_mark_failed(download_name)
		frappe.log_error(title=f"AI resource download failed: {download_name}", message=frappe.get_traceback())


def _resolve_dependencies(download_name: str, user: str) -> None:
	download = frappe.get_doc("AI Resource Download", download_name)
	resource = frappe.get_doc("AI Resource", download.resource)
	installed = installed_resources_map()
	for dependency in resource.dependencies:
		dep_code = dependency.resource_code
		if installed.get(dep_code):
			continue
		_set_message(download_name, _("Installing dependency {0}").format(dep_code))
		if not frappe.db.exists("AI Resource", dep_code):
			frappe.throw(_("Dependency {0} is not in the catalog.").format(dep_code))
		child_download = _create_dependency_download(dep_code, download_name, user)
		_download_and_install(child_download.name, user, depth=1)
		if _check_interrupt(child_download.name):
			return


def _create_dependency_download(resource_code: str, parent_download: str, user: str) -> object:
	resource = frappe.get_doc("AI Resource", resource_code)
	existing = frappe.get_all(
		"AI Resource Download",
		filters={
			"resource": resource.name,
			"status": ("in", ACTIVE_DOWNLOAD_STATUSES),
			"is_dependency": 1,
			"parent_download": parent_download,
		},
		limit=1,
	)
	if existing:
		return frappe.get_doc("AI Resource Download", existing[0].name)
	manifest = package_manifest(resource.resource_code)
	doc = frappe.new_doc("AI Resource Download")
	doc.update(
		{
			"resource": resource.name,
			"resource_code": resource.resource_code,
			"resource_name": resource.resource_name,
			"resource_type": resource.resource_type,
			"version": resource.version,
			"status": "Preparing",
			"stage": "Preparing Download",
			"progress": 0,
			"total_bytes": _package_size(manifest, resource),
			"network_status": "Idle",
			"connection_quality": "Unknown",
			"user": user,
			"is_dependency": 1,
			"parent_download": parent_download,
			"started_at": now_datetime(),
		}
	)
	doc.flags.ignore_permissions = True
	doc.insert(ignore_permissions=True)
	doc.job_id = f"ai_resdl_{expand_resource_code(resource.resource_code)}_{doc.name}"
	doc.save(ignore_permissions=True)
	return doc


def _download_and_install(download_name: str, user: str, *, depth: int = 0) -> None:
	"""Download, verify, install, register and activate one resource."""
	download = frappe.get_doc("AI Resource Download", download_name)
	resource = frappe.get_doc("AI Resource", download.resource)
	source = _download_source(download)

	if _check_interrupt(download_name):
		return

	payload_bytes = _perform_download(download_name, resource, source)
	if _check_interrupt(download_name):
		return

	from ai_fr_hg.ai.resources.verification import verify_package

	expected_checksum = download.expected_checksum or source.get("checksum") or resource.sha256
	expected_signature = download.expected_signature or source.get("signature") or resource.signature
	verification = verify_package(payload_bytes, expected_checksum, expected_signature)
	_update_download(
		download_name,
		{
			"verify_progress": 100,
			"verify_message": verification.get("message") or "",
			"verify_checksum": "Verified" if verification.get("checksum_ok") else "Failed",
			"signature_status": "Verified" if verification.get("signature_ok") else "Failed",
			"corruption_detected": 0 if verification.get("checksum_ok") else 1,
		},
	)
	if not verification["ok"]:
		frappe.throw(_("Package verification failed: {0}").format(verification["message"]))

	manifest = validate_manifest(payload_bytes)
	if manifest.get("resource_code") != resource.resource_code:
		frappe.throw(_("Package resource code does not match the requested resource."))

	if _check_interrupt(download_name):
		return

	from ai_fr_hg.ai.resources.install import install_manifest

	install, targets = install_manifest(resource, manifest, download, user)

	_set_status(download_name, "Registering", "Registering resource")
	_register_version(download_name, resource, manifest, verification, install)
	register_install(install, targets, resource, manifest, download)

	_set_status(download_name, "Activating", "Activating resource")
	activate_install(install, targets, resource)
	write_event(download_name, "Activate", "Resource activated.")

	_set_status(download_name, "Ready", "Verifying readiness")
	from ai_fr_hg.ai.resources.lifecycle import verify_ready_install

	readiness = verify_ready_install(install, user=user)
	if not readiness["ready"]:
		_update_download(
			download_name,
			{"install_message": _("Installed, but readiness verification failed."), "heartbeat": now_datetime()},
		)
		frappe.throw(_("Readiness verification failed for {0}.").format(resource.resource_name))
	_update_download(
		download_name,
		{"install_message": _("Verified and ready."), "verify_message": _("Integrity and readiness passed."), "heartbeat": now_datetime()},
	)

	_set_status(download_name, "Completed", "Ready for use")
	frappe.db.set_value(
		"AI Resource Download",
		download_name,
		{"is_completed": 1, "completed_at": now_datetime()},
		update_modified=False,
	)
	_set_installed_install_status(install)

	frappe.db.commit()  # nosemgrep: frappe-manual-commit


def _perform_download(download_name: str, resource, source: dict | None = None) -> bytes:
	"""Write the package to disk (or read the built-in bundle) with live progress."""
	_set_status(download_name, "Downloading", "Downloading package")

	resolved_path = download_path(resource.resource_code)
	source = source or resolve_download_source(resource)
	source_url = (source.get("source_url") or resource.source_url or "").strip()
	source_type = source.get("source_type") or ""

	if source_type == "Built-in" or source_url.startswith("builtin://") or (resource.is_builtin and not source_url.startswith(("http://", "https://"))):
		return _download_builtin(download_name, resource, resolved_path)

	if source_type == "File" or source_url.startswith("file://"):
		return _download_file(download_name, resource, source_url, resolved_path)

	if not source_url:
		frappe.throw(_("Resource {0} has no source URL.").format(resource.resource_name))
	if source_url.startswith(("http://", "https://")):
		return _download_http(download_name, resource, source_url, resolved_path)

	frappe.throw(_("Unsupported resource URL scheme: {0}").format(source_url))


def _download_file(download_name: str, resource, source_url: str, target: Path) -> bytes:
	"""Copy a local package file (used by offline / enterprise local sources)."""
	from urllib.parse import unquote, urlparse

	path_value = unquote(urlparse(source_url).path) if source_url.startswith("file://") else source_url
	if not path_value:
		frappe.throw(_("File source for {0} has no path.").format(resource.resource_name))
	if not Path(path_value).is_absolute():
		path_value = str(frappe.get_site_path(path_value))
	path = Path(path_value).resolve()
	site_root = Path(frappe.get_site_path()).resolve()
	app_root = bundle_path(resource.resource_code).resolve().parent.parent.parent.parent  # bundles dir ancestor
	try:
		path.relative_to(site_root)
	except ValueError:
		try:
			path.relative_to(app_root)
		except ValueError:
			frappe.throw(_("File source for {0} is outside permitted site/app paths.").format(resource.resource_name))
	if not path.exists() or not path.is_file():
		frappe.throw(_("Local source file does not exist: {0}").format(path))

	target.parent.mkdir(parents=True, exist_ok=True)
	total = path.stat().st_size
	written = 0
	start = time.monotonic()
	with open(path, "rb") as handle, open(target, "wb") as out:
		while True:
			if _check_interrupt(download_name):
				break
			chunk = handle.read(64 * 1024)
			if not chunk:
				break
			out.write(chunk)
			written += len(chunk)
			_update_progress(download_name, written, total, start)
	if written == 0:
		frappe.throw(_("Downloaded package is empty."))
	return target.read_bytes()


def _download_builtin(download_name: str, resource, target: Path) -> bytes:
	"""Stream a built-in bundle with progress updates (safe for any size)."""
	source = bundle_path(resource.resource_code)
	total = source.stat().st_size

	target.parent.mkdir(parents=True, exist_ok=True)
	written = 0
	start = time.monotonic()

	start_bytes = 0
	with open(target, "wb") as out:
		# Internal packages are small but still streamed so the same update
		# machinery drives the lifecycle for HTTP and built-in sources.
		for chunk in _iter_bundle_chunks(resource.resource_code, 64 * 1024):
			if _check_interrupt(download_name):
				break
			out.write(chunk)
			written += len(chunk)
			_update_progress(download_name, written, total, start, start_bytes=start_bytes)

	if written == 0:
		frappe.throw(_("Downloaded package is empty."))
	return target.read_bytes()


def _iter_bundle_chunks(resource_code: str, chunk_size: int):
	path = bundle_path(resource_code)
	with open(path, "rb") as handle:
		while True:
			chunk = handle.read(chunk_size)
			if not chunk:
				break
			yield chunk


def _download_http(download_name: str, resource, source_url: str, target: Path) -> bytes:
	"""Stream an external resource with checkpoint resume support."""
	_validate_download_url(source_url)

	import requests

	target.parent.mkdir(parents=True, exist_ok=True)
	checkpoint = target.stat().st_size if target.exists() else 0
	headers = {"Accept-Encoding": "identity"}
	if checkpoint:
		headers["Range"] = f"bytes={checkpoint}-"

	session = requests.Session()
	session.trust_env = False
	try:
		response = session.get(source_url, stream=True, headers=headers, timeout=60, allow_redirects=False)
		if response.status_code == 200 and checkpoint:
			# Server ignored Range: restart from zero.
			checkpoint = 0
			target.write_bytes(b"")
		elif response.status_code not in (200, 206):
			raise RuntimeError(_("HTTP {0} while downloading.").format(response.status_code))
		response.raise_for_status()

		total = int(response.headers.get("content-range", "").split("/")[-1] or response.headers.get("content-length", 0))
		if total == 0:
			total = checkpoint + resource.package_size_mb * 1024 * 1024

		written = checkpoint
		start_bytes = checkpoint
		start = time.monotonic()
		with open(target, "ab") as out:
			for chunk in response.iter_content(chunk_size=64 * 1024):
				if not chunk:
					continue
				if _check_interrupt(download_name):
					break
				out.write(chunk)
				written += len(chunk)
				_update_progress(download_name, written, total, start, start_bytes=start_bytes)
		return target.read_bytes()
	finally:
		session.close()


def _validate_download_url(source_url: str) -> None:
	"""Refuse redirection and non-private hosts unless explicitly allowed."""
	from urllib.parse import urlparse

	parsed = urlparse(source_url)
	if parsed.scheme not in ("http", "https"):
		frappe.throw(_("Resource URL must use HTTP(S)."))
	if parsed.hostname in ("localhost", "127.0.0.1", "::1"):
		return
	try:
		allowed = set((frappe.get_single("AI Platform Settings").resource_allowed_hosts or "").split("\n"))
	except Exception:
		allowed = set()
	if parsed.hostname in allowed:
		return

	import socket

	try:
		addresses = {info[4][0] for info in socket.getaddrinfo(parsed.hostname, None)}
	except (socket.gaierror, UnicodeError):
		frappe.throw(_("Resource hostname is unresolvable."))
	from ai_fr_hg.utils.netguard import is_private_address

	if not addresses or not all(is_private_address(address) for address in addresses):
		frappe.throw(_("Resource host is not on an allowed private network. Add it to Resource Allowed Hosts."))


def _update_progress(download_name: str, written: int, total: int, start: float, *, start_bytes: int = 0) -> None:
	if not total:
		return
	percent = min(100, int(written * 100 / total))
	elapsed = max(0.1, time.monotonic() - start)
	speed_kbps = int((written - start_bytes) / 1024 / elapsed)
	remaining = max(0, int((total - written) / max(speed_kbps, 1)))
	_update_download(
		download_name,
		{
			"downloaded_bytes": written,
			"total_bytes": total,
			"progress": percent,
			"transfer_speed_kbps": speed_kbps,
			"eta_seconds": remaining,
			"network_status": "Active",
			"connection_quality": _quality(speed_kbps),
			"heartbeat": now_datetime(),
			"last_checkpoint": now_datetime(),
		},
	)


def _quality(speed_kbps: int) -> str:
	if speed_kbps >= 2048:
		return "Excellent"
	if speed_kbps >= 512:
		return "Good"
	if speed_kbps >= 64:
		return "Fair"
	return "Poor"


def _register_version(download_name: str, resource, manifest: dict, verification: dict, install: str) -> None:
	version = manifest.get("version") or resource.version
	existing_name = frappe.db.exists(
		"AI Resource Version",
		{"resource": resource.name, "version": version},
	)
	if existing_name:
		version_doc = frappe.get_doc("AI Resource Version", existing_name)
		version_doc.is_installed = 1
		version_doc.installed_on = now_datetime()
		version_doc.flags.ignore_permissions = True
		version_doc.save(ignore_permissions=True)
		return
	else:
		version_doc = frappe.new_doc("AI Resource Version")
		version_doc.update(
			{
				"resource": resource.name,
				"version": version,
				"publisher": manifest.get("publisher") or resource.publisher,
				"repository": resource.repository,
				"source_url": resource.source_url,
				"sha256": verification.get("sha256") or resource.sha256,
				"signature": verification.get("signature") or resource.signature,
				"size_mb": round((verification.get("size_bytes") or 0) / 1024 / 1024, 3),
				"release_notes": "\n".join(manifest.get("release_notes") or []),
				"is_installed": 1,
				"installed_on": now_datetime(),
			}
		)
		version_doc.flags.ignore_permissions = True
		version_doc.insert(ignore_permissions=True)


def register_install(install_name: str, targets: list[dict], resource, manifest: dict, download: object) -> None:
	"""Update the install record with registered target identity."""
	install = frappe.get_doc("AI Resource Install", install_name)
	install.update(
		{
			"version": manifest.get("version") or resource.version,
			"resource_name": resource.resource_name,
			"resource_type": resource.resource_type,
			"status": "Registering",
			"registered_on": now_datetime(),
			"target_records": json.dumps(targets, default=str),
		}
	)
	install.flags.ignore_permissions = True
	install.save(ignore_permissions=True)


def activate_install(install_name: str, targets: list[dict], resource) -> None:
	install = frappe.get_doc("AI Resource Install", install_name)
	install.update(
		{
			"status": "Active",
			"is_active": 1,
			"activated": 1,
			"activated_on": now_datetime(),
			"target_records": json.dumps(targets, default=str),
		}
	)
	install.flags.ignore_permissions = True
	install.save(ignore_permissions=True)

	# Only one installed version is active at a time; retire an update's
	# predecessor while preserving it as version history.
	for previous in frappe.get_all(
		"AI Resource Install",
		filters={"resource": resource.name, "is_active": 1, "name": ("!=", install_name)},
		fields=["name"],
	):
		frappe.db.set_value(
			"AI Resource Install", previous["name"], {"status": "Superseded", "is_active": 0}, update_modified=False
		)


def _set_installed_install_status(install_name: str) -> None:
	install = frappe.get_doc("AI Resource Install", install_name)
	install.status = "Active"
	install.is_active = 1
	install.flags.ignore_permissions = True
	install.save(ignore_permissions=True)


def _set_status(download_name: str, status: str, stage: str) -> None:
	_update_download(
		download_name,
		{
			"status": status,
			"stage": stage,
			"heartbeat": now_datetime(),
			"error_message": "",
			"is_error": 0,
		},
	)


def _set_message(download_name: str, message: str) -> None:
	_update_download(
		download_name,
		{
			"stage_message": message,
			"heartbeat": now_datetime(),
		},
	)


def _update_download(download_name: str, data: dict) -> None:
	"""Field-save a download record without triggering validation."""
	frappe.db.set_value(
		"AI Resource Download",
		download_name,
		{key: value for key, value in data.items() if value is not None},
		update_modified=False,
	)
	frappe.db.set_value("AI Resource Download", download_name, "heartbeat", now_datetime(), update_modified=False)


def _mark_failed(download_name: str) -> None:
	frappe.db.set_value(
		"AI Resource Download",
		download_name,
		{
			"status": "Failed",
			"stage": "Failed",
			"is_error": 1,
			"error_message": frappe.get_traceback(200),
			"heartbeat": now_datetime(),
		},
		update_modified=False,
	)
	write_event(download_name, "Fail", "Resource operation failed.", severity="Critical", raise_on_error=False)
	frappe.db.commit()  # nosemgrep: frappe-manual-commit


def _check_interrupt(download_name: str) -> bool:
	"""If pause/cancel was requested, set a terminal/suspended status and stop."""
	status = frappe.db.get_value("AI Resource Download", download_name, "status") or "Downloading"
	if status == "Cancelled":
		return True
	pause_requested = frappe.db.get_value("AI Resource Download", download_name, "pause_requested") or 0
	if status == "Paused" or pause_requested:
		frappe.db.set_value(
			"AI Resource Download",
			download_name,
			{"status": "Paused", "pause_requested": 0, "stage": "Paused", "heartbeat": now_datetime()},
			update_modified=False,
		)
		return True
	if status == "Failed":
		return True
	return False


def write_event(download_name: str, action: str, message: str, *, severity: str = "Info", raise_on_error: bool = True) -> None:
	"""Append a lifecycle event to the download and to the audit trail."""
	download = frappe.db.get_value(
		"AI Resource Download",
		download_name,
		["resource", "resource_code", "resource_name", "resource_type", "version", "user"],
		as_dict=True,
	)
	if not download:
		return
	event = frappe.new_doc("AI Resource Event")
	event.update(
		{
			"resource": download.resource,
			"resource_code": download.resource_code,
			"resource_name": download.resource_name,
			"resource_type": download.resource_type,
			"version": download.version,
			"action": action,
			"severity": severity,
			"user": download.user or frappe.session.user,
			"download": download_name,
			"message": message,
			"details": json.dumps({"download": download_name}, default=str),
		}
	)
	event.flags.ignore_permissions = True
	event.insert(ignore_permissions=True)

	from ai_fr_hg.ai.logging import write_audit_log

	write_audit_log(
		action=f"AI Resource {action}",
		category="Configuration",
		severity=severity,
		message=message,
		details={"resource": download.resource_code, "version": download.version, "download": download_name},
		reference_doctype="AI Resource",
		reference_name=download.resource,
		raise_on_error=raise_on_error,
	)
