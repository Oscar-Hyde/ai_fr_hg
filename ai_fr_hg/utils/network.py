# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Network guards that keep the platform strictly local.

The platform is designed to run without any external cloud dependency. When
`Strict Local Only Mode` is enabled in AI Platform Settings every outbound
provider URL is validated against loopback / private address ranges before a
request is made, so a misconfiguration cannot silently ship prompts or
documents to a third party.
"""

import ipaddress
import socket
from functools import lru_cache
from urllib.parse import urlparse

import frappe
from frappe import _

from ai_fr_hg.ai.exceptions import LocalOnlyViolation

LOCAL_HOSTNAMES = {
	"localhost",
	"localhost.localdomain",
	"ip6-localhost",
	"ip6-loopback",
	"host.docker.internal",
	"host.containers.internal",
}


@lru_cache(maxsize=512)
def _resolve(hostname: str) -> tuple[str, ...]:
	"""Resolve a hostname to its IP addresses. Cached, since hosts are static locally."""
	try:
		infos = socket.getaddrinfo(hostname, None)
	except (socket.gaierror, UnicodeError):
		return ()
	return tuple({info[4][0] for info in infos})


def is_local_address(address: str) -> bool:
	"""True when `address` is a loopback, link-local, or private-network IP."""
	try:
		ip = ipaddress.ip_address(address)
	except ValueError:
		return False
	return bool(ip.is_loopback or ip.is_private or ip.is_link_local or ip.is_unspecified)


def is_local_url(url: str, allowed_hosts: set[str] | None = None) -> bool:
	"""True when `url` points at the local machine or the local network."""
	if not url:
		return False

	hostname = (urlparse(url).hostname or "").lower()
	if not hostname:
		return False

	if hostname in LOCAL_HOSTNAMES or hostname.endswith(".local") or hostname.endswith(".internal"):
		return True

	if allowed_hosts and hostname in {h.lower() for h in allowed_hosts}:
		return True

	if is_local_address(hostname):
		return True

	resolved = _resolve(hostname)
	return bool(resolved) and all(is_local_address(address) for address in resolved)


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
	_resolve.cache_clear()
