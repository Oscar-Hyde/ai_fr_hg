# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Resource policies, quotas and capability checks.

Limits are resolved per user by combining `AI Resource Policy` records that
match the user directly or one of their roles, with the platform-wide fallback
from `AI Platform Settings`. The most specific, highest priority policy wins.
"""

import frappe
from frappe import _
from frappe.utils import add_to_date, cint, now_datetime, today

from ai_fr_hg.ai.exceptions import QuotaExceededError


def get_effective_policy(user: str | None = None) -> frappe._dict:
	"""Resolve the policy that applies to `user`.

	A policy naming the user explicitly always beats a role policy; within each
	tier the lowest `priority` value wins.
	"""
	user = user or frappe.session.user
	roles = set(frappe.get_roles(user))

	policies = frappe.get_all(
		"AI Resource Policy",
		filters={"enabled": 1},
		fields=[
			"name",
			"user",
			"role",
			"priority",
			"max_requests_per_hour",
			"max_tokens_per_day",
			"max_documents_per_day",
			"max_concurrent_requests",
			"allow_tools",
			"allow_document_upload",
			"allow_pipeline_execution",
			"allow_model_management",
			"allow_learning",
		],
		order_by="priority asc, creation asc",
	)

	matched = None
	for policy in policies:
		if policy.user and policy.user == user:
			matched = policy
			break
	if not matched:
		for policy in policies:
			if policy.role and policy.role in roles:
				matched = policy
				break

	settings = frappe.get_cached_doc("AI Platform Settings")
	if not matched:
		return frappe._dict(
			{
				"name": None,
				"max_requests_per_hour": cint(settings.max_requests_per_user_per_hour),
				"max_tokens_per_day": cint(settings.max_tokens_per_user_per_day),
				"max_documents_per_day": 0,
				"max_concurrent_requests": 0,
				"allow_tools": 1,
				"allow_document_upload": 1,
				"allow_pipeline_execution": 1,
				"allow_model_management": 0,
				"allow_learning": 1,
				"allowed_models": [],
			}
		)

	matched = frappe._dict(matched)
	matched.allowed_models = frappe.get_all(
		"AI Policy Model", filters={"parent": matched.name}, pluck="model"
	)
	return matched


def _committed_usage(user: str) -> tuple[int, int]:
	"""Requests in the last hour and tokens today, as recorded in the database.

	This is the authoritative record of *finished* work. Everything still in
	flight lives in the reservation ledger (see :mod:`ai_fr_hg.ai.limits`).
	"""
	since = add_to_date(now_datetime(), hours=-1)
	requests = frappe.db.count("AI Execution Log", {"user": user, "creation": [">", since]})
	tokens = (
		frappe.db.sql(
			"""
			select coalesce(sum(total_tokens), 0)
			from `tabAI Execution Log`
			where user = %s and date(creation) = %s
			""",
			(user, today()),
		)[0][0]
		or 0
	)
	return cint(requests), cint(tokens)


def _check_allowed_model(policy, model_doc) -> None:
	if model_doc and policy.allowed_models and model_doc.name not in policy.allowed_models:
		frappe.throw(
			_("Your resource policy does not permit the model {0}.").format(model_doc.name),
			exc=QuotaExceededError,
			title=_("Model Not Permitted"),
		)


def check_quota(model_doc=None, user: str | None = None) -> None:
	"""Raise when the user has already exhausted their allowance.

	This is the read-only view of the same limits :func:`reserve_request_quota`
	enforces atomically. It stays available for callers that only need to know
	whether a user is currently over quota (admin screens, pre-flight checks);
	the execution engine always reserves instead, because a check that does not
	claim anything cannot stop two concurrent requests from both passing it.
	"""
	user = user or frappe.session.user
	if user == "Administrator":
		return

	policy = get_effective_policy(user)
	_check_allowed_model(policy, model_doc)

	request_limit = cint(policy.max_requests_per_hour)
	token_limit = cint(policy.max_tokens_per_day)
	if not request_limit and not token_limit:
		return

	used_requests, used_tokens = _committed_usage(user)
	if request_limit and used_requests >= request_limit:
		frappe.throw(
			_("Hourly request limit reached ({0} requests). Try again later.").format(request_limit),
			exc=QuotaExceededError,
			title=_("Quota Exceeded"),
		)
	if token_limit and used_tokens >= token_limit:
		frappe.throw(
			_("Daily token limit reached ({0} tokens).").format(token_limit),
			exc=QuotaExceededError,
			title=_("Quota Exceeded"),
		)


def reserve_request_quota(
	model_doc=None,
	user: str | None = None,
	estimated_tokens: int = 0,
	ttl: int = 300,
):
	"""GOV-03: atomically claim one request and its worst-case token budget.

	Returns a :class:`ai_fr_hg.ai.limits.Reservation`. The caller **must**
	release it — the engine does so in a ``finally`` block after the execution
	log and usage snapshot are written, so committed usage is visible before
	the in-flight claim disappears.

	`estimated_tokens` should be the call's ``max_tokens`` ceiling rather than
	a guess at the real answer length. Reserving the worst case is what makes
	the daily token limit an actual limit instead of an average.
	"""
	from ai_fr_hg.ai.limits import Reservation, reserve_quota

	user = user or frappe.session.user
	if user == "Administrator":
		return Reservation(enforced=False)

	policy = get_effective_policy(user)
	_check_allowed_model(policy, model_doc)

	request_limit = cint(policy.max_requests_per_hour)
	token_limit = cint(policy.max_tokens_per_day)
	if not request_limit and not token_limit:
		return Reservation(enforced=False)

	used_requests, used_tokens = _committed_usage(user)
	return reserve_quota(
		user=user,
		request_limit=request_limit,
		token_limit=token_limit,
		committed_requests=used_requests,
		committed_tokens=used_tokens,
		estimated_tokens=estimated_tokens,
		ttl=ttl,
	)


def user_concurrency_limits(user: str | None = None) -> list[tuple[str, int]]:
	"""GOV-01: the caller's own concurrency bound, held for the whole request."""
	user = user or frappe.session.user
	if user == "Administrator":
		return []
	policy = get_effective_policy(user)
	limit = cint(policy.max_concurrent_requests)
	return [(f"user:{user}", limit)] if limit else []


def runtime_concurrency_limits(model_doc=None, provider: str | None = None) -> list[tuple[str, int]]:
	"""GOV-01: provider and model bounds, held for one attempt at a time.

	These are per-attempt because failover moves the call to a different
	runtime: the slot that matters is the one on the host actually being
	dialled.
	"""
	specs: list[tuple[str, int]] = []
	if model_doc is not None:
		if limit := cint(getattr(model_doc, "max_concurrent_requests", 0)):
			specs.append((f"model:{model_doc.name}", limit))
		provider = provider or getattr(model_doc, "provider", None)
	if provider:
		provider_limit = cint(frappe.db.get_value("AI Provider", provider, "max_concurrent_requests") or 0)
		if provider_limit:
			specs.append((f"provider:{provider}", provider_limit))
	return specs


def concurrency_limits(model_doc=None, user: str | None = None) -> list[tuple[str, int]]:
	"""Every ``(scope, limit)`` pair that bounds one runtime call.

	Resolved here rather than in the engine so the three places a concurrency
	limit can be configured — resource policy, provider, model — have exactly
	one reader.
	"""
	return user_concurrency_limits(user) + runtime_concurrency_limits(model_doc)


def provider_rate_limit(provider: str | None) -> int:
	"""GOV-02: the configured requests-per-minute ceiling for a provider."""
	if not provider:
		return 0
	return cint(frappe.db.get_value("AI Provider", provider, "rate_limit_per_minute") or 0)


def check_capability(capability: str, user: str | None = None) -> None:
	"""Raise when the policy denies a capability such as tool execution."""
	user = user or frappe.session.user
	if user == "Administrator":
		return

	policy = get_effective_policy(user)
	field = {
		"tools": "allow_tools",
		"document_upload": "allow_document_upload",
		"pipeline": "allow_pipeline_execution",
		"model_management": "allow_model_management",
		"learning": "allow_learning",
	}.get(capability)

	# Only enforce when the policy record actually carries the flag. This keeps
	# a freshly-added capability (e.g. allow_learning) from being denied for
	# role policies that predate its column.
	if field and policy.get(field) is not None and not cint(policy.get(field)):
		frappe.throw(
			_("Your resource policy does not permit this action ({0}).").format(capability),
			exc=QuotaExceededError,
			title=_("Not Permitted"),
		)


def check_document_quota(user: str | None = None) -> None:
	"""Raise when the daily document ingestion allowance is exhausted."""
	user = user or frappe.session.user
	if user == "Administrator":
		return

	policy = get_effective_policy(user)
	if limit := cint(policy.max_documents_per_day):
		used = frappe.db.count("AI Document", {"owner": user, "creation": [">=", today()]})
		if used >= limit:
			frappe.throw(
				_("Daily document limit reached ({0} documents).").format(limit),
				exc=QuotaExceededError,
				title=_("Quota Exceeded"),
			)


def record_usage(model: str, tokens: int, user: str | None = None) -> None:
	"""Accumulate a daily usage snapshot per user and model."""
	user = user or frappe.session.user
	snapshot_date = today()

	name = frappe.db.get_value(
		"AI Usage Snapshot", {"snapshot_date": snapshot_date, "user": user, "model": model}, "name"
	)
	if name:
		frappe.db.sql(
			"""
			update `tabAI Usage Snapshot`
			set request_count = request_count + 1, total_tokens = total_tokens + %s
			where name = %s
			""",
			(cint(tokens), name),
		)
		return

	try:
		doc = frappe.new_doc("AI Usage Snapshot")
		doc.update(
			{
				"snapshot_date": snapshot_date,
				"user": user,
				"model": model,
				"request_count": 1,
				"total_tokens": cint(tokens),
			}
		)
		doc.flags.ignore_permissions = True
		doc.insert(ignore_permissions=True)
	except frappe.DuplicateEntryError:
		pass
