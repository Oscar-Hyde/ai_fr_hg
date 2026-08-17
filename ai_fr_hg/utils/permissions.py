# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Row-level permission rules for AI_FR_HG records.

Frappe role permissions answer *which DocTypes* a user may use.  These hooks
answer *which rows* within the shared operational DocTypes they may see or
change.  Keep both the list-query and direct-document paths here so opening a
known document name can never bypass the conditions used by ``get_list``.
"""

from __future__ import annotations

import frappe

_MANAGER_ROLES = {"System Manager", "AI Manager"}
_READ_PERMISSION_TYPES = {"read", "select", "report", "export", "print", "email"}


def has_app_permission() -> bool:
	"""Whether the current user should see AI_FR_HG on Frappe's apps screen."""
	return bool(_roles(frappe.session.user).intersection(_MANAGER_ROLES | {"AI User", "AI Auditor"}))


def _roles(user: str) -> set[str]:
	return set(frappe.get_roles(user))


def _is_manager(user: str) -> bool:
	return user == "Administrator" or bool(_roles(user).intersection(_MANAGER_ROLES))


def _is_auditor(user: str) -> bool:
	return "AI Auditor" in _roles(user)


def _is_read(permission_type: str | None) -> bool:
	return (permission_type or "read").lower() in _READ_PERMISSION_TYPES


def _escape(value: str) -> str:
	return frappe.db.escape(value)


def _role_sql(user: str) -> str:
	roles = sorted(_roles(user))
	return ", ".join(_escape(role) for role in roles) or "''"


def _owned_condition(doctype: str, field: str, user: str, *, auditors: bool = False) -> str:
	if _is_manager(user) or (auditors and _is_auditor(user)):
		return ""
	return f"`tab{doctype}`.`{field}` = {_escape(user)}"


# ---------------------------------------------------------------------------
# List-query conditions
# ---------------------------------------------------------------------------


def conversation_query(user: str) -> str:
	return _owned_condition("AI Conversation", "user", user)


def message_query(user: str) -> str:
	if _is_manager(user):
		return ""
	return (
		"`tabAI Message`.`conversation` in "
		f"(select name from `tabAI Conversation` where `user` = {_escape(user)})"
	)


def knowledge_base_query(user: str) -> str:
	if _is_manager(user):
		return ""
	return (
		"(`tabAI Knowledge Base`.`is_public` = 1 or exists ("
		"select 1 from `tabAI Knowledge Base Role` kb_role "
		"where kb_role.parent = `tabAI Knowledge Base`.name "
		"and kb_role.parenttype = 'AI Knowledge Base' "
		f"and kb_role.role in ({_role_sql(user)})))"
	)


def document_query(user: str) -> str:
	if _is_manager(user):
		return ""
	return (
		"`tabAI Document`.`knowledge_base` in ("
		"select kb.name from `tabAI Knowledge Base` kb "
		"where kb.is_public = 1 or exists ("
		"select 1 from `tabAI Knowledge Base Role` kb_role "
		"where kb_role.parent = kb.name and kb_role.parenttype = 'AI Knowledge Base' "
		f"and kb_role.role in ({_role_sql(user)})))"
	)


def chunk_query(user: str) -> str:
	if _is_manager(user):
		return ""
	return (
		"`tabAI Document Chunk`.`knowledge_base` in ("
		"select kb.name from `tabAI Knowledge Base` kb "
		"where kb.is_public = 1 or exists ("
		"select 1 from `tabAI Knowledge Base Role` kb_role "
		"where kb_role.parent = kb.name and kb_role.parenttype = 'AI Knowledge Base' "
		f"and kb_role.role in ({_role_sql(user)})))"
	)


def agent_query(user: str) -> str:
	if _is_manager(user):
		return ""
	return (
		"(not exists (select 1 from `tabAI Agent Role` agent_role "
		"where agent_role.parent = `tabAI Agent`.name and agent_role.parenttype = 'AI Agent') "
		"or exists (select 1 from `tabAI Agent Role` agent_role "
		"where agent_role.parent = `tabAI Agent`.name and agent_role.parenttype = 'AI Agent' "
		f"and agent_role.role in ({_role_sql(user)})))"
	)


def candidate_query(user: str) -> str:
	if _is_manager(user) or _is_auditor(user):
		return ""
	return _owned_condition("AI Knowledge Candidate", "user", user)


def _learning_scope_query(doctype: str, user: str) -> str:
	if _is_manager(user) or _is_auditor(user):
		return ""

	table = f"tab{doctype}"
	roles = _role_sql(user)
	return f"""(
		`{table}`.`scope` = 'Global'
		or (`{table}`.`scope` = 'User' and `{table}`.`scope_value` = {_escape(user)})
		or (`{table}`.`scope` = 'Role' and `{table}`.`scope_value` in ({roles}))
		or (`{table}`.`scope` = 'Agent' and (
			not exists (
				select 1 from `tabAI Agent Role` agent_role
				where agent_role.parent = `{table}`.`scope_value`
					and agent_role.parenttype = 'AI Agent'
			)
			or exists (
				select 1 from `tabAI Agent Role` agent_role
				where agent_role.parent = `{table}`.`scope_value`
					and agent_role.parenttype = 'AI Agent'
					and agent_role.role in ({roles})
			)
		))
	)"""


def memory_query(user: str) -> str:
	return _learning_scope_query("AI Memory", user)


def skill_query(user: str) -> str:
	return _learning_scope_query("AI Skill", user)


def task_query(user: str) -> str:
	return _owned_condition("AI Task", "owner", user, auditors=True)


def pipeline_run_query(user: str) -> str:
	return _owned_condition("AI Pipeline Run", "triggered_by", user, auditors=True)


def execution_log_query(user: str) -> str:
	return _owned_condition("AI Execution Log", "user", user, auditors=True)


def search_query(user: str) -> str:
	return _owned_condition("AI Search Query", "user", user, auditors=True)


def tool_invocation_query(user: str) -> str:
	return _owned_condition("AI Tool Invocation", "user", user, auditors=True)


# ---------------------------------------------------------------------------
# Direct-document permission
# ---------------------------------------------------------------------------


def _knowledge_base_access(knowledge_base: str | None, user: str, *, write: bool = False) -> bool:
	if not knowledge_base:
		return False
	if _is_manager(user):
		return True

	kb = frappe.db.get_value("AI Knowledge Base", knowledge_base, ["is_public"], as_dict=True)
	if not kb:
		return False

	roles = _roles(user)
	grants = frappe.get_all(
		"AI Knowledge Base Role",
		filters={"parent": knowledge_base, "parenttype": "AI Knowledge Base"},
		fields=["role", "can_write"],
	)
	matching = [grant for grant in grants if grant.role in roles]
	if write:
		return any(bool(grant.can_write) for grant in matching)
	return bool(kb.is_public or matching)


def _owned_document_access(doc, user: str, field: str, permission_type: str, *, auditors=False) -> bool:
	if _is_manager(user):
		return True
	if auditors and _is_auditor(user):
		return _is_read(permission_type)
	return doc.get(field) == user


def _learning_scope_applies(doc, user: str) -> bool:
	if _is_manager(user) or _is_auditor(user):
		return True
	scope = doc.scope or "Global"
	value = doc.scope_value
	roles = _roles(user)
	if scope == "Global":
		return True
	if scope == "User":
		return bool(value) and value == user
	if scope == "Role":
		return bool(value) and value in roles
	if scope == "Agent" and value:
		allowed = set(
			frappe.get_all(
				"AI Agent Role",
				filters={"parent": value, "parenttype": "AI Agent"},
				pluck="role",
			)
		)
		return not allowed or bool(allowed.intersection(roles))
	return False


def has_document_permission(
	doc,
	ptype: str | None = None,
	user: str | None = None,
	permission_type: str | None = None,
) -> bool:
	"""Return row-level access for every shared AI record registered in hooks."""
	user = user or frappe.session.user
	permission_type = (permission_type or ptype or "read").lower()

	if _is_manager(user):
		return True

	if doc.doctype == "AI Conversation":
		return doc.user == user or doc.owner == user
	if doc.doctype == "AI Message":
		conversation = frappe.db.get_value(
			"AI Conversation", doc.conversation, ["user", "owner"], as_dict=True
		)
		return bool(conversation and user in {conversation.user, conversation.owner})
	if doc.doctype == "AI Knowledge Base":
		# can_write controls document ingestion into a base, not modification of
		# the base's security/configuration record itself.
		return _knowledge_base_access(doc.name, user, write=False) if _is_read(permission_type) else False
	if doc.doctype == "AI Document":
		return _knowledge_base_access(doc.knowledge_base, user, write=not _is_read(permission_type))
	if doc.doctype == "AI Document Chunk":
		return _is_read(permission_type) and _knowledge_base_access(doc.knowledge_base, user)
	if doc.doctype == "AI Agent":
		if not _is_read(permission_type):
			return False
		allowed = {row.role for row in (doc.get("allowed_roles") or [])}
		return not allowed or bool(allowed.intersection(_roles(user)))
	if doc.doctype == "AI Knowledge Candidate":
		if _is_auditor(user):
			return _is_read(permission_type)
		return doc.user == user
	if doc.doctype in {"AI Memory", "AI Skill"}:
		return _is_read(permission_type) and _learning_scope_applies(doc, user)
	if doc.doctype == "AI Task":
		return _owned_document_access(doc, user, "owner", permission_type, auditors=True)
	if doc.doctype == "AI Pipeline Run":
		return _owned_document_access(doc, user, "triggered_by", permission_type, auditors=True)
	if doc.doctype in {"AI Execution Log", "AI Search Query", "AI Tool Invocation"}:
		return _owned_document_access(doc, user, "user", permission_type, auditors=True)

	return False
