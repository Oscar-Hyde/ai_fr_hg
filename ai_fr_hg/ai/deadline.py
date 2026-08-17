# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Per-request time budgets.

A single chat turn fans out into several model calls, and each of those carries
its own retry and failover bounds. Multiplied together those bounds are far
larger than the deadline any reverse proxy will hold a connection open for::

    (max_tool_iterations + 1) x (max_retries + 1) x providers x request_timeout

With the shipped defaults that is well over ten minutes, so a slow local model
produces a bare ``504 Gateway Time-out`` from the proxy: no error in the UI, no
saved answer, and no clue in the logs about which layer was still working.

A :class:`Deadline` makes the budget explicit and, crucially, *shared*. The
entry point declares how long the whole operation may take; every layer beneath
it asks how much time is left and declines to start work it cannot finish. The
turn then ends with a real, saved, explainable result instead of a dead socket.

The budget is opt-in. When no deadline is active every helper here reports
"unlimited", so background jobs - which have no proxy in front of them and may
legitimately run for minutes - keep their existing behaviour untouched.
"""

import time
from contextlib import contextmanager

import frappe

#: Time held back from the budget so a turn can still persist its messages,
#: close its execution log and build a response after the last model call.
DEFAULT_RESERVE_SECONDS = 2.0

#: A model call shorter than this is not worth starting; the round trip alone
#: would consume it.
MIN_CALL_SECONDS = 1.0

#: Key under which the active deadline is stored on ``frappe.flags``.
FLAG = "ai_turn_deadline"


class Deadline:
	"""A monotonic time budget for one logical operation.

	The clock is injectable so the behaviour can be tested deterministically
	rather than by sleeping.
	"""

	__slots__ = ("_clock", "budget", "started")

	def __init__(self, budget_seconds: float, clock=time.monotonic):
		self._clock = clock
		self.budget = max(float(budget_seconds or 0), 0.0)
		self.started = clock()

	@property
	def elapsed(self) -> float:
		return self._clock() - self.started

	def remaining(self) -> float:
		return max(self.budget - self.elapsed, 0.0)

	@property
	def expired(self) -> bool:
		return self.remaining() <= 0

	def allows(self, seconds: float, reserve: float = DEFAULT_RESERVE_SECONDS) -> bool:
		"""Is there room to spend `seconds` and still keep the reserve?"""
		return self.remaining() - reserve >= seconds

	def clamp(self, timeout: float, reserve: float = DEFAULT_RESERVE_SECONDS) -> float:
		"""Shrink `timeout` so the call cannot outlive the budget.

		Returns ``0.0`` when there is not enough time left to bother starting,
		which callers treat as "give up now, with a clear error".
		"""
		available = self.remaining() - reserve
		if available < MIN_CALL_SECONDS:
			return 0.0
		return min(float(timeout), available)

	def __repr__(self) -> str:
		return f"<Deadline budget={self.budget:.1f}s remaining={self.remaining():.1f}s>"


def get_deadline() -> Deadline | None:
	"""The deadline governing the current operation, if one is active."""
	return frappe.flags.get(FLAG)


@contextmanager
def turn_budget(seconds: float):
	"""Run a block under a time budget.

	A falsy or non-positive `seconds` disables budgeting entirely, and nested
	budgets never *extend* an outer one - the tighter deadline always wins.
	"""
	if not seconds or float(seconds) <= 0:
		yield None
		return

	previous = get_deadline()
	deadline = Deadline(seconds)

	# An inner block must not be able to buy itself more time than the
	# operation that contains it has left.
	if previous and previous.remaining() < deadline.remaining():
		yield previous
		return

	frappe.flags[FLAG] = deadline
	try:
		yield deadline
	finally:
		if previous is None:
			frappe.flags.pop(FLAG, None)
		else:
			frappe.flags[FLAG] = previous


def remaining_seconds() -> float | None:
	"""Seconds left in the active budget, or ``None`` when unbudgeted."""
	deadline = get_deadline()
	return deadline.remaining() if deadline else None


def expired() -> bool:
	"""Has the active budget run out? False when there is no budget."""
	deadline = get_deadline()
	return bool(deadline and deadline.expired)


def allows(seconds: float, reserve: float = DEFAULT_RESERVE_SECONDS) -> bool:
	"""Is there room for `seconds` more work? True when there is no budget."""
	deadline = get_deadline()
	return deadline.allows(seconds, reserve=reserve) if deadline else True


def clamp_timeout(timeout: float, reserve: float = DEFAULT_RESERVE_SECONDS) -> float | None:
	"""Clamp a per-call timeout to the active budget.

	Returns ``None`` when unbudgeted (use the caller's own timeout) and
	``0.0`` when the budget is spent.
	"""
	deadline = get_deadline()
	return deadline.clamp(timeout, reserve=reserve) if deadline else None
