# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Distributed runtime admission control.

This module is the single authority for the question *"may this call start
right now?"*. It closes three audit findings that all share the same root
cause — limits were stored on documents but never consulted:

============  ===============================================================
GOV-01        `AI Resource Policy.max_concurrent_requests`,
              `AI Provider.max_concurrent_requests` and
              `AI Model.max_concurrent_requests` are enforced with TTL leases.
GOV-02        `AI Provider.rate_limit_per_minute` is enforced with a
              distributed sliding window.
GOV-03        Quotas are *reserved* before the call and reconciled after it,
              so concurrent requests cannot all pass a check-then-use test.
============  ===============================================================

Why Redis and not the database
------------------------------
Frappe v17 already runs Redis for cache, queue, and socket.io; every bench and
every worker shares it. Admission decisions happen on the hot path of every
model call, must be atomic across processes, and must expire on their own when
a worker dies. A database row would need a lock per call, would leave orphaned
rows behind a killed worker, and would put write traffic on the same
transaction the caller may later roll back. ``frappe.cache()`` is the native
Redis handle, and every decision below is one server-side Lua script, so the
check and the claim can never interleave.

Degradation contract (ADR-009)
------------------------------
If the Redis backend is unreachable the platform is already degraded — Frappe
sessions, queues and realtime all depend on it. Admission control then logs
once and **allows** the call, recording ``degraded`` in the returned decision
rather than pretending an unenforced limit was enforced. Quota reservation
falls back to the committed-usage check, which is the pre-GOV-03 behaviour and
is never weaker than no check at all.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field

import frappe
from frappe import _
from frappe.utils import cint

from ai_fr_hg.ai.exceptions import (
	ConcurrencyLimitError,
	QuotaExceededError,
	RateLimitExceededError,
)

#: Lease lifetime floor/ceiling in seconds. The floor keeps a fast call from
#: releasing a slot before a slow peer has observed it; the ceiling stops a
#: crashed worker from holding a slot for the rest of the day.
MIN_LEASE_TTL = 30
MAX_LEASE_TTL = 3600

#: Sliding rate-limit window.
RATE_WINDOW_MS = 60_000

#: Extra seconds a quota reservation outlives the call it guards.
RESERVATION_GRACE_SECONDS = 60

_DEGRADED_LOG_KEY = "ai_fr_hg:limits:degraded_logged"


class LimitBackendUnavailable(Exception):
	"""The Redis backend could not answer; see the degradation contract above."""


# ---------------------------------------------------------------------------
# Lua primitives — each is a single atomic check-and-claim
# ---------------------------------------------------------------------------

#: Bounded semaphore. Members are lease tokens scored by their expiry, so an
#: abandoned lease is reaped by the next caller instead of needing a sweeper.
LEASE_LUA = """
local key = KEYS[1]
local now = tonumber(ARGV[1])
local ttl = tonumber(ARGV[2])
local limit = tonumber(ARGV[3])
local token = ARGV[4]
redis.call('ZREMRANGEBYSCORE', key, '-inf', now)
local held = redis.call('ZCARD', key)
if held >= limit then
  return {0, held}
end
redis.call('ZADD', key, now + ttl, token)
redis.call('EXPIRE', key, math.ceil(ttl) + 60)
return {1, held + 1}
"""

#: Sliding-window rate limiter. Returns 0 when admitted, otherwise the number
#: of milliseconds until the oldest entry leaves the window.
RATE_LUA = """
local key = KEYS[1]
local now = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local limit = tonumber(ARGV[3])
local token = ARGV[4]
redis.call('ZREMRANGEBYSCORE', key, '-inf', now - window)
if redis.call('ZCARD', key) >= limit then
  local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
  if oldest[2] == nil then
    return 1
  end
  return math.ceil(tonumber(oldest[2]) + window - now)
end
redis.call('ZADD', key, now, token)
redis.call('PEXPIRE', key, window + 1000)
return 0
"""

#: Quota reservation ledger. Members encode ``token|tokens`` so in-flight
#: request *and* token totals can be summed without a second round trip, and
#: the whole decision stays inside one atomic script.
RESERVE_LUA = """
local key = KEYS[1]
local now = tonumber(ARGV[1])
local ttl = tonumber(ARGV[2])
local request_limit = tonumber(ARGV[3])
local token_limit = tonumber(ARGV[4])
local committed_requests = tonumber(ARGV[5])
local committed_tokens = tonumber(ARGV[6])
local estimate = tonumber(ARGV[7])
local token = ARGV[8]
redis.call('ZREMRANGEBYSCORE', key, '-inf', now)
local members = redis.call('ZRANGE', key, 0, -1)
local inflight_requests = 0
local inflight_tokens = 0
for _, member in ipairs(members) do
  local separator = string.find(member, '|', 1, true)
  if separator then
    inflight_tokens = inflight_tokens + (tonumber(string.sub(member, separator + 1)) or 0)
  end
  inflight_requests = inflight_requests + 1
end
if request_limit > 0 and (committed_requests + inflight_requests + 1) > request_limit then
  return {-1, inflight_requests, inflight_tokens}
end
if token_limit > 0 and (committed_tokens + inflight_tokens + estimate) > token_limit then
  return {-2, inflight_requests, inflight_tokens}
end
redis.call('ZADD', key, now + ttl, token .. '|' .. estimate)
redis.call('EXPIRE', key, math.ceil(ttl) + 60)
return {1, inflight_requests + 1, inflight_tokens + estimate}
"""


def _cache():
	return frappe.cache()


def _key(*parts: str) -> str:
	"""Site-scoped Redis key. ``make_key`` adds the site prefix Frappe uses."""
	raw = "ai_fr_hg:limits:" + ":".join(str(part).replace(" ", "_") for part in parts if part)
	try:
		key = _cache().make_key(raw)
	except Exception as exc:  # pragma: no cover - only when Redis is absent
		raise LimitBackendUnavailable(str(exc)) from exc
	return key.decode() if isinstance(key, bytes) else str(key)


def _eval(script: str, key: str, *args):
	try:
		return _cache().eval(script, 1, key, *args)
	except LimitBackendUnavailable:
		raise
	except Exception as exc:
		raise LimitBackendUnavailable(str(exc)) from exc


def _note_degraded(reason: str) -> None:
	"""Log a Redis outage once per hour instead of once per request."""
	try:
		cache = _cache()
		if cache.get_value(_DEGRADED_LOG_KEY):
			return
		cache.set_value(_DEGRADED_LOG_KEY, 1, expires_in_sec=3600)
	except Exception:
		return
	try:
		frappe.log_error(
			title="AI admission control degraded",
			message=(
				"Redis-backed concurrency, rate limiting and quota reservation are "
				f"unavailable and were bypassed: {reason}"
			),
		)
	except Exception:
		pass


def _clamp_ttl(ttl: int) -> int:
	return max(MIN_LEASE_TTL, min(cint(ttl) or MIN_LEASE_TTL, MAX_LEASE_TTL))


# ---------------------------------------------------------------------------
# GOV-01 — concurrency leases
# ---------------------------------------------------------------------------


@dataclass
class Lease:
	"""One held slot of one bounded semaphore."""

	key: str
	token: str
	scope: str
	limit: int
	held: int = 0


@dataclass
class LeaseSet:
	"""All slots one call holds. Release is idempotent and never raises."""

	leases: list[Lease] = field(default_factory=list)
	degraded: bool = False

	def release(self) -> None:
		for lease in self.leases:
			try:
				_cache().zrem(lease.key, lease.token)
			except Exception:
				# The TTL is the backstop: an unreleased lease expires on its
				# own, which is exactly the behaviour a killed worker needs.
				pass
		self.leases = []

	def __enter__(self) -> LeaseSet:
		return self

	def __exit__(self, *exc_info) -> None:
		self.release()

	def as_diagnostics(self) -> dict:
		return {
			"degraded": self.degraded,
			"scopes": [
				{"scope": lease.scope, "limit": lease.limit, "held": lease.held} for lease in self.leases
			],
		}


def acquire_leases(specs: list[tuple[str, int]], *, ttl: int = 300) -> LeaseSet:
	"""Acquire every bounded slot in `specs`, or none of them.

	`specs` is a list of ``(scope, limit)`` pairs such as
	``[("user:alice@example.com", 3), ("provider:Local Ollama", 8)]``. A limit
	of ``0`` or less means "unlimited" and is skipped rather than stored, so an
	unconfigured field costs nothing at runtime.

	Specs are sorted before acquisition. Two concurrent calls that need the
	same pair of scopes therefore always take them in the same order, which is
	what prevents a lease deadlock between a user limit and a model limit.

	Raises :class:`ConcurrencyLimitError` naming the exhausted scope. Every
	slot already taken by this call is released first — a partially admitted
	call would leak a slot for the whole TTL.
	"""
	held = LeaseSet()
	ttl = _clamp_ttl(ttl)
	now = time.time()

	for scope, limit in sorted((s, cint(l)) for s, l in specs or []):
		if limit <= 0:
			continue
		token = uuid.uuid4().hex
		try:
			key = _key("lease", scope)
			granted, count = _eval(LEASE_LUA, key, now, ttl, limit, token)
		except LimitBackendUnavailable as exc:
			_note_degraded(str(exc))
			held.degraded = True
			continue

		if not cint(granted):
			held.release()
			raise ConcurrencyLimitError(
				_("{0} is at its concurrent request limit ({1}). Try again shortly.").format(
					_describe_scope(scope), limit
				)
			)
		held.leases.append(Lease(key=key, token=token, scope=scope, limit=limit, held=cint(count)))

	return held


def _describe_scope(scope: str) -> str:
	prefix, _, value = str(scope).partition(":")
	labels = {"user": _("Your account"), "provider": _("Provider {0}"), "model": _("Model {0}")}
	label = labels.get(prefix)
	if not label:
		return scope
	return label.format(value) if "{0}" in str(label) else str(label)


# ---------------------------------------------------------------------------
# GOV-02 — provider rate limiting
# ---------------------------------------------------------------------------


def check_rate_limit(scope: str, limit_per_minute: int) -> dict:
	"""Admit one call against a distributed sliding window.

	Returns a diagnostics dict on success. Raises
	:class:`RateLimitExceededError` carrying the retry delay otherwise. A
	limit of ``0`` disables the window.
	"""
	limit = cint(limit_per_minute)
	if limit <= 0:
		return {"enforced": False}

	now_ms = int(time.time() * 1000)
	try:
		retry_after_ms = cint(
			_eval(RATE_LUA, _key("rate", scope), now_ms, RATE_WINDOW_MS, limit, uuid.uuid4().hex)
		)
	except LimitBackendUnavailable as exc:
		_note_degraded(str(exc))
		return {"enforced": False, "degraded": True}

	if retry_after_ms > 0:
		raise RateLimitExceededError(
			_("{0} exceeded its rate limit of {1} requests per minute. Retry in {2}s.").format(
				_describe_scope(scope), limit, max(1, round(retry_after_ms / 1000))
			),
			retry_after_ms=retry_after_ms,
		)
	return {"enforced": True, "limit": limit}


# ---------------------------------------------------------------------------
# GOV-03 — quota reservation
# ---------------------------------------------------------------------------


@dataclass
class Reservation:
	"""A held share of a user's request and token allowance.

	The reservation is released only *after* the execution log and usage
	snapshot are written. During that short overlap the usage is counted twice
	— once in the ledger and once in the database. Over-counting briefly is the
	safe direction for a quota; under-counting is how a limit gets exceeded.
	"""

	key: str = ""
	member: str = ""
	estimate: int = 0
	enforced: bool = False
	degraded: bool = False
	released: bool = False

	def release(self) -> None:
		if self.released or not self.key or not self.member:
			self.released = True
			return
		self.released = True
		try:
			_cache().zrem(self.key, self.member)
		except Exception:
			pass  # the reservation TTL is the backstop

	def __enter__(self) -> Reservation:
		return self

	def __exit__(self, *exc_info) -> None:
		self.release()


def reserve_quota(
	*,
	user: str,
	request_limit: int,
	token_limit: int,
	committed_requests: int,
	committed_tokens: int,
	estimated_tokens: int,
	ttl: int = 300,
) -> Reservation:
	"""Atomically claim one request and `estimated_tokens` of allowance.

	`committed_requests`/`committed_tokens` come from the database (the
	authoritative record of finished work). The ledger adds everything still
	in flight. Both are compared against the limit inside one Lua script, so
	N concurrent requests can no longer all observe the same pre-call total.
	"""
	request_limit = cint(request_limit)
	token_limit = cint(token_limit)
	if request_limit <= 0 and token_limit <= 0:
		return Reservation(enforced=False)

	estimate = max(0, cint(estimated_tokens))
	member_token = uuid.uuid4().hex
	member = f"{member_token}|{estimate}"
	ttl = _clamp_ttl(ttl) + RESERVATION_GRACE_SECONDS

	try:
		key = _key("quota", user)
		outcome = _eval(
			RESERVE_LUA,
			key,
			time.time(),
			ttl,
			request_limit,
			token_limit,
			max(0, cint(committed_requests)),
			max(0, cint(committed_tokens)),
			estimate,
			member_token,
		)
	except LimitBackendUnavailable as exc:
		_note_degraded(str(exc))
		# Fall back to the committed-usage comparison. This is the pre-GOV-03
		# behaviour: it still refuses a user who is already over the limit.
		if request_limit > 0 and cint(committed_requests) >= request_limit:
			raise QuotaExceededError(
				_("Hourly request limit reached ({0} requests). Try again later.").format(request_limit)
			) from exc
		if token_limit > 0 and cint(committed_tokens) >= token_limit:
			raise QuotaExceededError(
				_("Daily token limit reached ({0} tokens).").format(token_limit)
			) from exc
		return Reservation(enforced=False, degraded=True)

	status = cint(outcome[0]) if isinstance(outcome, (list, tuple)) else cint(outcome)
	if status == -1:
		raise QuotaExceededError(
			_("Hourly request limit reached ({0} requests). Try again later.").format(request_limit)
		)
	if status == -2:
		raise QuotaExceededError(_("Daily token limit reached ({0} tokens).").format(token_limit))

	return Reservation(key=key, member=member, estimate=estimate, enforced=True)


# ---------------------------------------------------------------------------
# Introspection for operations dashboards and tests
# ---------------------------------------------------------------------------


def current_usage(scope: str, kind: str = "lease") -> int:
	"""Live count for a lease or rate scope; ``-1`` when Redis is unavailable."""
	try:
		key = _key("lease" if kind == "lease" else "rate", scope)
		cache = _cache()
		now = time.time() if kind == "lease" else time.time() * 1000
		window = 0 if kind == "lease" else RATE_WINDOW_MS
		cache.zremrangebyscore(key, "-inf", now - window)
		return cint(cache.zcard(key))
	except Exception:
		return -1


def reset_scope(scope: str, kind: str = "lease") -> None:
	"""Drop every entry for a scope. Test and operator recovery helper only."""
	try:
		prefix = {"lease": "lease", "rate": "rate", "quota": "quota"}.get(kind, "lease")
		# `_key` has already applied Frappe's site prefix, so this must be the
		# raw Redis DEL rather than `delete_value`, which would prefix twice.
		_cache().delete(_key(prefix, scope))
	except Exception:
		pass
