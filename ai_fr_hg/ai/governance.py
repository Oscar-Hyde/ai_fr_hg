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


def check_quota(model_doc=None, user: str | None = None) -> None:
	"""Raise when the user has exhausted their request or token allowance."""
	user = user or frappe.session.user
	if user == "Administrator":
		return

	policy = get_effective_policy(user)

	if model_doc and policy.allowed_models and model_doc.name not in policy.allowed_models:
		frappe.throw(
			_("Your resource policy does not permit the model {0}.").format(model_doc.name),
			exc=QuotaExceededError,
			title=_("Model Not Permitted"),
		)

	if limit := cint(policy.max_requests_per_hour):
		since = add_to_date(now_datetime(), hours=-1)
		used = frappe.db.count("AI Execution Log", {"user": user, "creation": [">", since]})
		if used >= limit:
			frappe.throw(
				_("Hourly request limit reached ({0} requests). Try again later.").format(limit),
				exc=QuotaExceededError,
				title=_("Quota Exceeded"),
			)

	if limit := cint(policy.max_tokens_per_day):
		used = (
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
		if cint(used) >= limit:
			frappe.throw(
				_("Daily token limit reached ({0} tokens).").format(limit),
				exc=QuotaExceededError,
				title=_("Quota Exceeded"),
			)


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
