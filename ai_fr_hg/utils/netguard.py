# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Connection-level enforcement for the strictly-local provider transport.

This module is deliberately free of Frappe imports so the whole guard can be
exercised against a real loopback HTTP server in plain-Python unit tests
(:mod:`ai_fr_hg.tests.test_netguard_units`). :mod:`ai_fr_hg.utils.network`
owns the settings/policy inputs and translates violations into platform
errors.

Guarantees per provider call:

* **No environment proxies.** Sessions are built with ``trust_env = False``,
  so ``HTTP_PROXY``/``HTTPS_PROXY``/``ALL_PROXY`` variables can never route
  prompt traffic through a third party.
* **One validated DNS decision.** The hostname is resolved once and every
  address must be loopback/private/link-local unless the host is explicitly
  allowlisted. Suffixes like ``.local`` are hints only: an unresolved or
  publicly-resolving suffix host is refused.
* **Pinned dialing.** The connection dials one of the validated addresses
  directly, so a later DNS rebinding to a public address cannot affect an
  already-approved connection, while the original hostname is preserved for
  the HTTP ``Host`` header and TLS SNI.
* **No redirects.** Any 3xx response aborts the call; a redirect is a new,
  unvalidated destination and provider endpoints must not redirect.
* **Peer re-validation.** After the connection is established, the actual
  peer socket address is checked against the validated set again.
"""

from __future__ import annotations

import functools
import ipaddress
import socket
from urllib.parse import urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.connection import HTTPConnection, HTTPSConnection


class TransportGuardError(Exception):
	"""Base class for transport-level violations raised by this module."""


class AddressPolicyViolation(TransportGuardError):
	"""The hostname or its resolved addresses are outside the allowed policy."""


#: Loopback, private and link-local ranges that the strict policy allows.
#: ``ipaddress.is_private`` also treats IANA documentation ranges
#: (192.0.2.0/24, 198.51.100.0/24, 203.0.113.0/24, 2001:db8::/32) as private;
#: those are not local networks, so the policy enumerates its ranges instead.
_PRIVATE_V4 = (
	ipaddress.ip_network("10.0.0.0/8"),
	ipaddress.ip_network("172.16.0.0/12"),
	ipaddress.ip_network("192.168.0.0/16"),
)
_PRIVATE_V6 = (ipaddress.ip_network("fc00::/7"),)


def is_private_address(address: str) -> bool:
	try:
		ip = ipaddress.ip_address(address)
	except ValueError:
		return False
	if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
		ip = ip.ipv4_mapped
	if ip.is_loopback or ip.is_link_local or ip.is_unspecified:
		return True
	if isinstance(ip, ipaddress.IPv4Address):
		return any(ip in network for network in _PRIVATE_V4)
	return any(ip in network for network in _PRIVATE_V6)


@functools.lru_cache(maxsize=512)
def resolve_host(hostname: str) -> tuple[str, ...]:
	"""Resolve a hostname once, deduplicated, in address-family order."""
	try:
		infos = socket.getaddrinfo(hostname, None)
	except (socket.gaierror, UnicodeError):
		return ()
	return tuple({info[4][0] for info in infos})


def clear_host_cache() -> None:
	"""Drop cached resolutions when provider or policy configuration changes."""
	resolve_host.cache_clear()


def hostname_policy_allows(hostname: str, allowed_hosts: set[str] | None = None) -> bool:
	"""True when `hostname` may be dialed under the local-only policy.

	An explicitly allowlisted host is permitted regardless of its addresses;
	anything else must resolve and every resolved address must be private.
	"""
	hostname = (hostname or "").strip().lower()
	if not hostname:
		return False
	if is_private_address(hostname):
		return True  # literal IP address
	if allowed_hosts and hostname in {host.lower() for host in allowed_hosts}:
		return True
	resolved = resolve_host(hostname)
	return bool(resolved) and all(is_private_address(address) for address in resolved)


def validated_addresses(hostname: str, allowed_hosts: set[str] | None = None) -> tuple[str, ...]:
	"""Resolved addresses that satisfy the policy, or a policy violation."""
	hostname = (hostname or "").strip().lower()
	if not hostname:
		raise AddressPolicyViolation("Provider URL has no hostname.")
	if is_private_address(hostname):
		return (hostname,)
	if allowed_hosts and hostname in {host.lower() for host in allowed_hosts}:
		resolved = resolve_host(hostname)
		if not resolved:
			raise AddressPolicyViolation(f"Allowed host {hostname} does not resolve.")
		return resolved
	resolved = resolve_host(hostname)
	if not resolved:
		raise AddressPolicyViolation(f"Host {hostname} does not resolve to a local address.")
	if not all(is_private_address(address) for address in resolved):
		raise AddressPolicyViolation(f"Host {hostname} resolves to non-local addresses; refusing to connect.")
	return resolved


def is_local_url(url: str, allowed_hosts: set[str] | None = None) -> bool:
	"""True when `url` points inside the strict local-only policy."""
	if not url:
		return False
	hostname = (urlparse(url).hostname or "").lower()
	if not hostname:
		return False
	return hostname_policy_allows(hostname, allowed_hosts)


class PinnedHTTPConnection(HTTPConnection):
	"""HTTPConnection that dials a pre-validated address, keeping Host/SNI."""

	def __init__(self, host, port=None, pinned_ip=None, **kwargs):
		super().__init__(host, port, **kwargs)
		self._pinned_ip = pinned_ip
		self._original_host = host

	def _new_conn(self):
		# urllib3 2.x dials `self._dns_host`; 1.26 dials `self.host`. Swap in
		# the pinned address for the dial only, so TLS SNI and Host stay right.
		dns_attribute = "_dns_host" if hasattr(self, "_dns_host") else "host"
		original = getattr(self, dns_attribute)
		setattr(self, dns_attribute, self._pinned_ip or original)
		try:
			return super()._new_conn()
		finally:
			setattr(self, dns_attribute, original)


class PinnedHTTPSConnection(PinnedHTTPConnection, HTTPSConnection):
	"""Pinned dialing with TLS."""


def _verify_peer(response, pinned_addresses: tuple[str, ...]) -> None:
	"""Re-check the established socket against the validated address set."""
	try:
		connection = getattr(response.raw, "_connection", None)
		sock = getattr(connection, "sock", None)
		if sock is None:
			return  # test doubles / already-closed raw stream; pinning is the primary control
		peer = sock.getpeername()[0]
	except (AttributeError, OSError):
		return
	if peer not in pinned_addresses:
		raise TransportGuardError(
			f"Connection established to unexpected address {peer}; refusing the response."
		)


class PinnedAddressAdapter(HTTPAdapter):
	"""Adapter whose pool dials only the validated addresses and refuses redirects."""

	def __init__(self, hostname: str, pinned_addresses: tuple[str, ...], scheme: str = "http", **kwargs):
		super().__init__(**kwargs)
		self._hostname = hostname
		self._pinned_addresses = pinned_addresses
		connection_class = PinnedHTTPSConnection if scheme == "https" else PinnedHTTPConnection
		# The pool instantiates `ConnectionCls(host, port, **kwargs)`; the
		# pinned IP is carried in by the partial.
		self._connection_class = functools.partial(connection_class, pinned_ip=self._pinned_addresses[0])

	def get_connection(self, url, proxies=None):
		pool = super().get_connection(url, proxies)
		pool.ConnectionCls = self._connection_class
		return pool

	def get_connection_with_tls_context(self, request, verify, proxies=None, cert=None):
		# requests 2.34 builds pools through this path instead of
		# `get_connection`; both must pin the connection class.
		pool = super().get_connection_with_tls_context(request, verify, proxies=proxies, cert=cert)
		pool.ConnectionCls = self._connection_class
		return pool

	def send(self, request, stream=False, timeout=None, verify=True, cert=None, proxies=None):
		response = super().send(
			request, stream=stream, timeout=timeout, verify=verify, cert=cert, proxies=proxies
		)
		if response.is_redirect:
			response.close()
			raise TransportGuardError("Provider endpoints must not redirect; the call was refused.")
		_verify_peer(response, self._pinned_addresses)
		return response


class GuardedSession(requests.Session):
	"""Session whose transport is pinned to one validated provider host.

	The guarded adapters are selected by hostname (not URL-prefix mount), so
	URLs with explicit ports are covered, and a request for any other host is
	a policy violation rather than a plain-text escape hatch.
	"""

	def __init__(self, hostname: str, pinned_addresses: tuple[str, ...], **kwargs):
		super().__init__(**kwargs)
		self.trust_env = False
		self.max_redirects = 0
		self._guarded_hostname = hostname
		self._guarded_adapters = {
			scheme: PinnedAddressAdapter(hostname, pinned_addresses, scheme=scheme)
			for scheme in ("http", "https")
		}

	def get_adapter(self, url):
		parsed = urlparse(url)
		hostname = (parsed.hostname or "").lower()
		if hostname != self._guarded_hostname:
			raise AddressPolicyViolation(
				f"Refusing to contact {hostname or url}; the session is pinned to {self._guarded_hostname}."
			)
		adapter = self._guarded_adapters.get(parsed.scheme)
		if adapter is None:
			raise AddressPolicyViolation(f"Unsupported provider URL scheme {parsed.scheme}.")
		return adapter


def secure_provider_session(hostname: str, allowed_hosts: set[str] | None = None) -> requests.Session:
	"""Build a guarded requests.Session for one validated provider host.

	Raises :class:`AddressPolicyViolation` when the host cannot be validated.
	"""
	addresses = validated_addresses(hostname, allowed_hosts)
	return GuardedSession(hostname, addresses)
