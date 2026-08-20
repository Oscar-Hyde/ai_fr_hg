# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Network guards that keep the platform strictly local (settings-aware side).

The platform is designed to run without any external cloud dependency. When
`Strict Local Only Mode` is enabled in AI Platform Settings every outbound
provider URL is validated against loopback / private address ranges before a
request is made, so a misconfiguration cannot silently ship prompts or
documents to a third party.

Policy evaluation and connection-level enforcement live in the Frappe-free
:mod:`ai_fr_hg.utils.netguard` module; this module owns the platform settings
inputs (strict mode, allowlist) and translates violations into platform
errors.
"""

from urllib.parse import urlparse

import frappe
from frappe import _

from ai_fr_hg.ai.exceptions import LocalOnlyViolation
from ai_fr_hg.utils import netguard
from ai_fr_hg.utils.netguard import clear_host_cache, is_local_url


def get_allowed_hosts() -> set[str]:
	"""Extra hostnames the administrator has explicitly permitted."""
	raw = frappe.db.get_single_value("AI Platform Settings", "allowed_hosts") or ""
	return {line.strip().lower() for line in raw.splitlines() if line.strip()}


def enforce_local_only(url: str, label: str | None = None) -> None:
	"""Raise when `url` would leave the local network while strict mode is on.

	Called before every outbound provider request.
	"""
	if not frappe.db.get_single_value("AI Platform Settings", "offline_mode"):
		return

	if is_local_url(url, get_allowed_hosts()):
		return

	frappe.throw(
		_(
			"{0} points to {1}, which is outside the local network. "
			"Disable Strict Local Only Mode or add the host to Additional Allowed Hosts "
			"in AI Platform Settings."
		).format(label or _("Endpoint"), urlparse(url).hostname or url),
		exc=LocalOnlyViolation,
		title=_("Local Only Mode"),
	)


def clear_resolution_cache() -> None:
	"""Drop cached DNS answers. Called when settings or providers change."""
	clear_host_cache()


__all__ = [
	"clear_resolution_cache",
	"enforce_local_only",
	"get_allowed_hosts",
	"is_local_url",
	"netguard",
]
