# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Row-level permission rules layered on top of Frappe's role permissions."""

import frappe

MANAGER_ROLES = {"System Manager", "AI Manager"}
LEARNING_REVIEW_ROLES = MANAGER_ROLES | {"AI Auditor"}


def has_app_permission() -> bool:
	"""Whether the current user should see the AI Platform on the apps screen."""
	roles = set(frappe.get_roles())
	return bool(roles.intersection(MANAGER_ROLES | {"AI User", "AI Auditor"}))


def _is_manager(user: str | None = None) -> bool:
	user = user or frappe.session.user
	return user == "Administrator" or bool(set(frappe.get_roles(user)).intersection(MANAGER_ROLES))


def get_accessible_knowledge_base_list(user: str | None = None) -> list[str]:
	"""Knowledge bases readable by `user`, honouring per-base role restrictions."""
	from ai_fr_hg.ai.knowledge import get_accessible_knowledge_bases

	return get_accessible_knowledge_bases(user)


def get_document_query_conditions(user: str | None = None) -> str:
	"""Restrict AI Document lists to knowledge bases the user may read."""
	user = user or frappe.session.user
	if _is_manager(user):
		return ""

	accessible = get_accessible_knowledge_base_list(user)
	if not accessible:
		return "1 = 0"

	rendered = ", ".join(frappe.db.escape(kb) for kb in accessible)
	return f"`tabAI Document`.`knowledge_base` in ({rendered})"


def has_document_permission(doc, ptype: str | None = None, user: str | None = None) -> bool:
	"""Row-level check matching :func:`get_document_query_conditions`."""
	user = user or frappe.session.user
	if _is_manager(user):
		return True
	return doc.knowledge_base in get_accessible_knowledge_base_list(user)


def has_knowledge_base_permission(doc, ptype: str | None = None, user: str | None = None) -> bool:
	"""Private knowledge bases are visible only to their listed roles."""
	user = user or frappe.session.user
	if _is_manager(user):
		return True
	if doc.is_public:
		# Writing to a shared base still needs an explicit grant.
		if ptype in ("write", "create", "delete"):
			return _has_write_grant(doc, user)
		return True

	allowed = [row.role for row in doc.get("restrict_to_roles") or []]
	if not set(frappe.get_roles(user)).intersection(allowed):
		return False
	if ptype in ("write", "create", "delete"):
		return _has_write_grant(doc, user)
	return True


def _has_write_grant(doc, user: str) -> bool:
	roles = set(frappe.get_roles(user))
	for row in doc.get("restrict_to_roles") or []:
		if row.role in roles and row.can_write:
			return True
	return False


def _can_review_learning(user: str) -> bool:
	return user == "Administrator" or bool(set(frappe.get_roles(user)).intersection(LEARNING_REVIEW_ROLES))


def get_candidate_query_conditions(user: str | None = None) -> str:
	"""Learners see their own candidates; reviewers see the complete queue."""
	user = user or frappe.session.user
	if _can_review_learning(user):
		return ""
	return f"`tabAI Knowledge Candidate`.`user` = {frappe.db.escape(user)}"


def has_candidate_permission(doc, ptype: str | None = None, user: str | None = None) -> bool:
	user = user or frappe.session.user
	return _can_review_learning(user) or doc.user == user


def _learning_scope_query(table: str, user: str) -> str:
	if _can_review_learning(user):
		return ""

	roles = set(frappe.get_roles(user))
	escaped_roles = ", ".join(frappe.db.escape(role) for role in roles) or "''"
	escaped_user = frappe.db.escape(user)
	return f"""(
		`{table}`.`scope` = 'Global'
		or (`{table}`.`scope` = 'User' and `{table}`.`scope_value` = {escaped_user})
		or (`{table}`.`scope` = 'Role' and `{table}`.`scope_value` in ({escaped_roles}))
		or (`{table}`.`scope` = 'Agent' and (
			not exists (
				select 1 from `tabAI Agent Role` ar
				where ar.parent = `{table}`.`scope_value` and ar.parenttype = 'AI Agent'
			)
			or exists (
				select 1 from `tabAI Agent Role` ar
				where ar.parent = `{table}`.`scope_value`
					and ar.parenttype = 'AI Agent' and ar.role in ({escaped_roles})
			)
		))
	)"""


def get_memory_query_conditions(user: str | None = None) -> str:
	return _learning_scope_query("tabAI Memory", user or frappe.session.user)


def get_skill_query_conditions(user: str | None = None) -> str:
	return _learning_scope_query("tabAI Skill", user or frappe.session.user)


def _learning_scope_applies(doc, user: str) -> bool:
	if _can_review_learning(user):
		return True
	scope = doc.scope or "Global"
	value = doc.scope_value
	roles = set(frappe.get_roles(user))
	if scope == "Global":
		return True
	if scope == "User":
		return bool(value) and value == user
	if scope == "Role":
		return bool(value) and value in roles
	if scope == "Agent" and value:
		allowed = set(frappe.get_all("AI Agent Role", filters={"parent": value}, pluck="role"))
		return not allowed or bool(roles.intersection(allowed))
	return False


def has_memory_permission(doc, ptype: str | None = None, user: str | None = None) -> bool:
	return _learning_scope_applies(doc, user or frappe.session.user)


def has_skill_permission(doc, ptype: str | None = None, user: str | None = None) -> bool:
	return _learning_scope_applies(doc, user or frappe.session.user)
