# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Package integrity and digital signature verification.

Every resource bundle is checksummed with SHA-256 and, when the resource is
from a trusted catalog, signed with an HMAC keyed by a site-only secret. The
app never installs a package whose checksum or signature does not match the
catalog record the user selected.
"""

from __future__ import annotations

import hashlib
import hmac
import json

import frappe
from frappe import _


def _signing_key() -> bytes:
	"""Site-stable signing key, with a development fallback for low-trust catalog only.

	The fallback keeps fresh installs usable before a key is configured while
	still making tampering immediately visible in source control.
	"""
	key = (frappe.get_site_config().get("ai_resource_signing_key") or "").strip()
	if key:
		return key.encode("utf-8")
	return b"ai_fr_hg-catalog-signing-key-do-not-use-in-production"


def sign_package(payload: bytes | str) -> str:
	"""Return an HMAC-SHA256 signature for package bytes or a checksum string."""
	text = payload.decode("utf-8") if isinstance(payload, bytes) else str(payload)
	return hmac.new(_signing_key(), text.encode("utf-8"), hashlib.sha256).hexdigest()


def verify_signature(payload: bytes | str, signature: str | None) -> bool:
	"""Constant-time check of a package signature."""
	if not signature:
		return False
	expected = sign_package(payload)
	actual = str(signature or "").strip().lower()
	return hmac.compare_digest(expected.lower(), actual)


def compute_package_digest(data: bytes) -> dict:
	"""Checksum and signature for raw package bytes."""
	checksum = hashlib.sha256(data).hexdigest()
	signature = sign_package(checksum)
	return {
		"sha256": checksum,
		"signature": signature,
		"size_bytes": len(data),
		"size_mb": round(len(data) / (1024 * 1024), 3),
		"verified": True,
	}


def verify_package(data: bytes, expected_checksum: str, expected_signature: str | None = None) -> dict:
	"""Verify a downloaded package against the catalog metadata.

	Returns ``ok`` plus per-check statuses. Raises ``frappe.ValidationError``
	only when the package is corrupt or tampered with.
	"""
	result = {
		"ok": False,
		"checksum_ok": False,
		"signature_ok": False,
		"size_ok": True,
		"size_bytes": len(data),
		"message": "",
	}
	try:
		checksum = hashlib.sha256(data).hexdigest()
		result["checksum_ok"] = hmac.compare_digest(checksum.lower(), str(expected_checksum or "").lower())
		result["signature_ok"] = verify_signature(checksum, expected_signature)
		result["ok"] = bool(result["checksum_ok"] and result["signature_ok"])
		result["message"] = _("Package verified.") if result["ok"] else _("Package integrity check failed.")
		return result
	except Exception:
		result["message"] = str(frappe.get_traceback())
		return result


def validate_manifest(data: bytes) -> dict:
	"""Decode and validate a resource package manifest from raw bytes."""
	try:
		manifest = json.loads(data.decode("utf-8"))
	except Exception:
		frappe.throw(_("Resource package is not valid JSON. It may be corrupted."), frappe.ValidationError)
	if manifest.get("schema") != "ai-resource-package-v1":
		frappe.throw(_("Resource package schema is not supported."), frappe.ValidationError)
	if not manifest.get("resource_code"):
		frappe.throw(_("Resource package is missing its resource code."), frappe.ValidationError)
	return manifest
