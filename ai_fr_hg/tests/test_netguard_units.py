# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Connection-level network guard tests against a real loopback server.

SEC-04 requires the provider transport to ignore environment proxies, make
one validated DNS decision, dial only the validated address (DNS rebinding
can no longer move an approved connection), refuse redirects, and re-validate
the established peer socket. These tests run a real HTTP server on
``127.0.0.1`` and mock only the resolver, so the sockets and the adapter
behaviour are genuinely exercised - no model runtime or database is involved.
"""

import http.server
import os
import shutil
import socket
import ssl
import subprocess
import tempfile
import threading
import unittest
import warnings
from typing import ClassVar
from unittest.mock import patch

from ai_fr_hg.utils import netguard


class _RecordingHandler(http.server.BaseHTTPRequestHandler):
	requests: ClassVar[list[dict]] = []

	def _handle(self):
		body = ""
		length = int(self.headers.get("Content-Length") or 0)
		if length:
			body = self.rfile.read(length).decode("utf-8")
		_RecordingHandler.requests.append(
			{
				"path": self.path,
				"host": self.headers.get("Host"),
				"body": body,
			}
		)
		if self.path == "/redirect":
			self.send_response(302)
			self.send_header("Location", "http://192.0.2.10/elsewhere")
			self.end_headers()
			return
		self.send_response(200)
		self.send_header("Content-Type", "application/json")
		self.end_headers()
		self.wfile.write(b'{"ok": true}')

	def do_GET(self):
		self._handle()

	def do_POST(self):
		self._handle()

	def log_message(self, *args):  # silence test output
		return


class NetguardTestCase(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		cls.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _RecordingHandler)
		cls.port = cls.server.server_address[1]
		cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
		cls.thread.start()

	@classmethod
	def tearDownClass(cls):
		cls.server.shutdown()
		cls.server.server_close()

	def setUp(self):
		_RecordingHandler.requests = []
		netguard.clear_host_cache()

	def resolve_local(self, hostname):
		return ("127.0.0.1",)

	def resolve_public(self, hostname):
		return ("203.0.113.5",)

	def resolve_mixed(self, hostname):
		return ("127.0.0.1", "203.0.113.5")

	def resolve_none(self, hostname):
		return ()

	def url(self, path="/"):
		return f"http://provider.test:{self.port}{path}"


class TestResolutionPolicy(NetguardTestCase):
	def test_private_addresses_are_recognised(self):
		for address in ("127.0.0.1", "::1", "10.0.0.5", "172.16.4.3", "192.168.1.50", "169.254.1.2"):
			self.assertTrue(netguard.is_private_address(address), address)

	def test_public_addresses_are_rejected(self):
		for address in ("8.8.8.8", "203.0.113.5", "2001:db8::1"):
			self.assertFalse(netguard.is_private_address(address), address)

	def test_all_resolved_private_is_allowed(self):
		with patch("ai_fr_hg.utils.netguard.resolve_host", side_effect=self.resolve_local):
			self.assertTrue(netguard.hostname_policy_allows("provider.test"))
			self.assertTrue(netguard.is_local_url(self.url()))

	def test_mixed_resolution_is_rejected(self):
		with patch("ai_fr_hg.utils.netguard.resolve_host", side_effect=self.resolve_mixed):
			self.assertFalse(netguard.hostname_policy_allows("provider.test"))
			with self.assertRaises(netguard.AddressPolicyViolation):
				netguard.validated_addresses("provider.test")

	def test_unresolved_host_is_rejected(self):
		with patch("ai_fr_hg.utils.netguard.resolve_host", side_effect=self.resolve_none):
			self.assertFalse(netguard.hostname_policy_allows("provider.test"))
			with self.assertRaises(netguard.AddressPolicyViolation):
				netguard.validated_addresses("provider.test")

	def test_allowlisted_host_may_resolve_publicly_but_must_resolve(self):
		with patch("ai_fr_hg.utils.netguard.resolve_host", side_effect=self.resolve_public):
			self.assertFalse(netguard.hostname_policy_allows("provider.test"))
			self.assertTrue(netguard.hostname_policy_allows("provider.test", {"provider.test"}))
			self.assertEqual(
				netguard.validated_addresses("provider.test", {"provider.test"}), ("203.0.113.5",)
			)
		with patch("ai_fr_hg.utils.netguard.resolve_host", side_effect=self.resolve_none):
			with self.assertRaises(netguard.AddressPolicyViolation):
				netguard.validated_addresses("provider.test", {"provider.test"})

	def test_suffix_hints_are_not_unconditional_trust(self):
		# A `.local`/`.internal` name that resolves publicly is still refused.
		with patch("ai_fr_hg.utils.netguard.resolve_host", side_effect=self.resolve_public):
			self.assertFalse(netguard.hostname_policy_allows("runtime.internal"))
			self.assertFalse(netguard.is_local_url("http://runtime.internal:11434"))

	def test_literal_private_ip_is_allowed_without_resolution(self):
		self.assertTrue(netguard.hostname_policy_allows("127.0.0.1"))
		self.assertTrue(netguard.is_local_url("http://[::1]:8000"))
		self.assertFalse(netguard.hostname_policy_allows("8.8.8.8"))


class TestPinnedTransport(NetguardTestCase):
	def session(self):
		return netguard.secure_provider_session("provider.test")

	def test_request_dials_the_validated_address_and_keeps_the_host_header(self):
		with patch("ai_fr_hg.utils.netguard.resolve_host", side_effect=self.resolve_local):
			response = self.session().get(self.url())
		self.assertEqual(response.status_code, 200)
		self.assertTrue(_RecordingHandler.requests)
		host = _RecordingHandler.requests[0]["host"]
		self.assertTrue(host.startswith("provider.test"), host)

	def test_dns_rebinding_cannot_move_an_approved_connection(self):
		with patch("ai_fr_hg.utils.netguard.resolve_host", side_effect=self.resolve_local):
			session = self.session()
			self.assertEqual(session.get(self.url()).status_code, 200)

		# The resolver now points at a public address; the session's adapter
		# still dials the originally validated address, and the established
		# peer is re-validated before the response is trusted.
		with patch("ai_fr_hg.utils.netguard.resolve_host", side_effect=self.resolve_public):
			session.get_adapter(self.url()).poolmanager.clear()
			response = session.get(self.url())
		self.assertEqual(response.status_code, 200)
		self.assertGreaterEqual(len(_RecordingHandler.requests), 2)

	def test_environment_proxies_are_ignored(self):
		previous = {key: os.environ.get(key) for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY")}
		os.environ["HTTP_PROXY"] = "http://127.0.0.1:1"
		os.environ["ALL_PROXY"] = "http://127.0.0.1:1"
		try:
			with patch("ai_fr_hg.utils.netguard.resolve_host", side_effect=self.resolve_local):
				response = self.session().get(self.url())
			self.assertEqual(response.status_code, 200, "trust_env=False must bypass proxy environment")
		finally:
			for key, value in previous.items():
				if value is None:
					os.environ.pop(key, None)
				else:
					os.environ[key] = value

	def test_redirects_are_refused(self):
		with patch("ai_fr_hg.utils.netguard.resolve_host", side_effect=self.resolve_local):
			with self.assertRaises(netguard.TransportGuardError):
				self.session().get(self.url("/redirect"))

	def test_peer_mismatch_is_refused(self):
		class _FakeSock:
			def getpeername(self):
				return ("203.0.113.9", 80)

		class _FakeRaw:
			_connection = type("Conn", (), {"sock": _FakeSock()})()

		response = type("Response", (), {"raw": _FakeRaw()})()
		with self.assertRaises(netguard.TransportGuardError):
			netguard._verify_peer(response, ("127.0.0.1",))

	def test_peer_inside_the_validated_set_is_accepted(self):
		class _FakeSock:
			def getpeername(self):
				return ("127.0.0.1", 12345)

		class _FakeRaw:
			_connection = type("Conn", (), {"sock": _FakeSock()})()

		response = type("Response", (), {"raw": _FakeRaw()})()
		netguard._verify_peer(response, ("127.0.0.1",))

	def test_ipv6_addresses_are_validated_like_ipv4(self):
		with patch("ai_fr_hg.utils.netguard.resolve_host", return_value=("::1",)):
			self.assertTrue(netguard.hostname_policy_allows("provider.test"))
		with patch("ai_fr_hg.utils.netguard.resolve_host", return_value=("2001:db8::1",)):
			self.assertFalse(netguard.hostname_policy_allows("provider.test"))

	def test_connection_failure_is_a_normal_connection_error(self):
		import requests

		with patch("ai_fr_hg.utils.netguard.resolve_host", return_value=("127.0.0.1",)):
			session = self.session()
		# Nothing listens on this port; the dial fails with a ConnectionError,
		# not a policy error.
		dead_port = None
		probe = socket.socket()
		try:
			probe.bind(("127.0.0.1", 0))
			dead_port = probe.getsockname()[1]
		finally:
			probe.close()
		with self.assertRaises(requests.exceptions.ConnectionError):
			session.get(f"http://provider.test:{dead_port}/", timeout=2)

	def test_tls_pinned_dial_preserves_sni_and_host(self):
		"""Real TLS loopback: pinned dial keeps the original hostname for SNI."""
		if not shutil.which("openssl"):
			self.skipTest("openssl is required to mint a loopback test certificate")
		with tempfile.TemporaryDirectory() as directory:
			cert = os.path.join(directory, "cert.pem")
			key = os.path.join(directory, "key.pem")
			subprocess.run(
				[
					"openssl",
					"req",
					"-x509",
					"-newkey",
					"rsa:2048",
					"-keyout",
					key,
					"-out",
					cert,
					"-days",
					"2",
					"-nodes",
					"-subj",
					"/CN=provider.test",
				],
				check=True,
				capture_output=True,
			)
			server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _RecordingHandler)
			context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
			context.load_cert_chain(cert, key)
			server.socket = context.wrap_socket(server.socket, server_side=True)
			port = server.server_address[1]
			thread = threading.Thread(target=server.serve_forever, daemon=True)
			thread.start()
			try:
				with patch("ai_fr_hg.utils.netguard.resolve_host", return_value=("127.0.0.1",)):
					session = self.session()
					with warnings.catch_warnings():
						warnings.simplefilter("ignore")
						response = session.get(f"https://provider.test:{port}/", verify=False)
				self.assertEqual(response.status_code, 200)
				self.assertTrue(_RecordingHandler.requests)
				self.assertTrue(_RecordingHandler.requests[-1]["host"].startswith("provider.test"))
			finally:
				server.shutdown()
				server.server_close()


if __name__ == "__main__":
	unittest.main()
