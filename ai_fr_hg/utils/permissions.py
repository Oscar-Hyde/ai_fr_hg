# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Row-level permission rules for AI_FR_HG records.

Frappe role permissions answer *which DocTypes* a user may use.  These hooks
answer *which rows* within the shared operational DocTypes they may see or
change.  Keep both the list-query and direct-document paths here so opening a
known document name can never bypass the conditions used by ``get_list``.
"""

from __future__ import annotations

from contextlib import contextmanager

import frappe

_MANAGER_ROLES = {"System Manager", "AI Manager"}
_READ_PERMISSION_TYPES = {"read", "select", "report", "export", "print", "email"}


def has_app_permission() -> bool:
	"""Whether the current user should see AI_FR_HG on Frappe's apps screen."""
	try:
		return bool(_roles(frappe.session.user).intersection(_MANAGER_ROLES | {"AI User", "AI Auditor"}))
	except Exception:
		return False


def _roles(user: str) -> set[str]:
	try:
		return set(frappe.get_roles(user))
	except Exception:
		return set()


def _is_manager(user: str) -> bool:
	return user == "Administrator" or bool(_roles(user).intersection(_MANAGER_ROLES))


def _is_auditor(user: str) -> bool:
	return "AI Auditor" in _roles(user)


def _is_read(permission_type: str | None) -> bool:
	return (permission_type or "read").lower() in _READ_PERMISSION_TYPES


def _escape(value: str) -> str:
	try:
		return frappe.db.escape(value)
	except Exception:
		escaped = str(value).replace("'", "''")
		return f"'{escaped}'"


def _role_sql(user: str) -> str:
	roles = sorted(_roles(user))
	return ", ".join(_escape(role) for role in roles) or "''"


def _owned_condition(doctype: str, field: str, user: str, *, auditors: bool = False) -> str:
	if _is_manager(user) or (auditors and _is_auditor(user)):
		return ""
	return f"`tab{doctype}`.`{field}` = {_escape(user)}"


def _safe_condition(fn):
	"""Wrap permission queries so Desk never 500s on return."""

	def wrapper(user: str) -> str:
		try:
			return fn(user)
		except Exception:
			try:
				frappe.log_error(
					title=f"AI permission query failed: {fn.__name__}", message=frappe.get_traceback()
				)
			except Exception:
				pass
			return "1=0"

	return wrapper


def _safe_doc_permission(fn):
	"""Wrap document permission check so Desk never 500s."""

	def wrapper(doc, ptype=None, user=None, permission_type=None):
		try:
			return fn(doc, ptype=ptype, user=user, permission_type=permission_type)
		except Exception:
			try:
				frappe.log_error(title="AI document permission failed", message=frappe.get_traceback())
			except Exception:
				pass
			return False

	return wrapper


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


def glossary_query(user: str) -> str:
	"""Glossaries are readable with the same KB grant as documents.

	A glossary with no knowledge base is a global termbase: readable by any
	AI role, writable only by managers.
	"""
	if _is_manager(user):
		return ""
	return (
		"(`tabAI Translation Glossary`.`knowledge_base` is null "
		"or `tabAI Translation Glossary`.`knowledge_base` = '' "
		"or `tabAI Translation Glossary`.`knowledge_base` in ("
		"select kb.name from `tabAI Knowledge Base` kb "
		"where kb.is_public = 1 or exists ("
		"select 1 from `tabAI Knowledge Base Role` kb_role "
		"where kb_role.parent = kb.name and kb_role.parenttype = 'AI Knowledge Base' "
		f"and kb_role.role in ({_role_sql(user)}))))"
	)


def translation_query(user: str) -> str:
	if _is_manager(user):
		return ""
	# A translation is readable by whoever can read the knowledge base it
	# belongs to, and always by the person who requested it.
	return (
		"(`tabAI Translation`.`owner` = {user} or `tabAI Translation`.`requested_by` = {user} "
		"or `tabAI Translation`.`knowledge_base` in ("
		"select kb.name from `tabAI Knowledge Base` kb "
		"where kb.is_public = 1 or exists ("
		"select 1 from `tabAI Knowledge Base Role` kb_role "
		"where kb_role.parent = kb.name and kb_role.parenttype = 'AI Knowledge Base' "
		"and kb_role.role in ({roles}))))"
	).format(user=_escape(user), roles=_role_sql(user))


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


def pattern_entity_query(user: str) -> str:
	# Pattern entities carry the same denormalized knowledge base as chunks,
	# so they ride the document's row-level access without a join.
	if _is_manager(user):
		return ""
	return (
		"`tabAI Pattern Entity`.`knowledge_base` in ("
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
	try:
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
	except Exception:
		return "1=0"


def memory_query(user: str) -> str:
	return _learning_scope_query("AI Memory", user)


def skill_query(user: str) -> str:
	return _learning_scope_query("AI Skill", user)


def task_query(user: str) -> str:
	if _is_manager(user) or _is_auditor(user):
		return ""
	return (
		f"(`tabAI Task`.`owner` = {_escape(user)} or `tabAI Task`.`requested_by` = {_escape(user)})"
	)


def automation_event_query(user: str) -> str:
	return _owned_condition("AI Automation Event", "requested_by", user, auditors=True)


def pipeline_run_query(user: str) -> str:
	return _owned_condition("AI Pipeline Run", "triggered_by", user, auditors=True)


def execution_log_query(user: str) -> str:
	return _owned_condition("AI Execution Log", "user", user, auditors=True)


def search_query(user: str) -> str:
	return _owned_condition("AI Search Query", "user", user, auditors=True)


def tool_invocation_query(user: str) -> str:
	return _owned_condition("AI Tool Invocation", "user", user, auditors=True)


def folder_settings_query(user: str) -> str:
	if _is_manager(user):
		return ""
	# List visibility must match direct-document access (has_document_permission
	# opens the linked File). Compose the same permission query conditions
	# Frappe itself applies when listing Files, and require the settings row's
	# folder to pass them - so an AI User can never list metadata for a folder
	# they could not open directly.
	try:
		conditions = _file_list_permission_conditions(user)
	except Exception:
		return "1=0"
	if not conditions:
		return ""
	return f"`tabAI Folder Settings`.`folder` in (select `name` from `tabFile` where {conditions})"


def _file_list_permission_conditions(user: str) -> str:
	"""The permission query conditions Frappe applies when listing ``File``."""
	parts: list[str] = []
	hooks = frappe.get_hooks("permission_query_conditions", {})
	for method in hooks.get("File", []) + hooks.get("*", []):
		# Security-reviewed dynamic dispatch: this is the exact mechanism
		# Frappe's own DatabaseQuery uses to resolve registered permission
		# hooks. Only hook methods registered by installed apps can be reached.
		condition = frappe.call(frappe.get_attr(method), user, doctype="File")  # nosemgrep
		if condition:
			parts.append(str(condition))
	return " and ".join(f"({part})" for part in parts)


def folder_favorite_query(user: str) -> str:
	if _is_manager(user):
		return ""
	return _owned_condition("AI Folder Favorite", "user", user)


# Wrap all list-query conditions so Desk return never throws 500
conversation_query = _safe_condition(conversation_query)
message_query = _safe_condition(message_query)
knowledge_base_query = _safe_condition(knowledge_base_query)
document_query = _safe_condition(document_query)
translation_query = _safe_condition(translation_query)
glossary_query = _safe_condition(glossary_query)
chunk_query = _safe_condition(chunk_query)
agent_query = _safe_condition(agent_query)
candidate_query = _safe_condition(candidate_query)
memory_query = _safe_condition(memory_query)
skill_query = _safe_condition(skill_query)
task_query = _safe_condition(task_query)
automation_event_query = _safe_condition(automation_event_query)
pipeline_run_query = _safe_condition(pipeline_run_query)
execution_log_query = _safe_condition(execution_log_query)
search_query = _safe_condition(search_query)
tool_invocation_query = _safe_condition(tool_invocation_query)
folder_settings_query = _safe_condition(folder_settings_query)
folder_favorite_query = _safe_condition(folder_favorite_query)

# ---------------------------------------------------------------------------
# Direct-document permission
# ---------------------------------------------------------------------------


@contextmanager
def scoped_knowledge_base_permission_cache():
	"""Deduplicate KB grant queries inside one bounded list/tree operation.

	The cache is deliberately opt-in and scoped rather than process- or
	request-global, so a role mutation followed by a permission check in the same
	request cannot observe stale authorization state.
	"""
	local = frappe.local
	attribute = "ai_fr_hg_knowledge_base_permission_cache"
	missing = object()
	previous = getattr(local, attribute, missing)
	setattr(local, attribute, {})
	try:
		yield
	finally:
		if previous is missing:
			delattr(local, attribute)
		else:
			setattr(local, attribute, previous)


def _knowledge_base_access(knowledge_base: str | None, user: str, *, write: bool = False) -> bool:
	if not knowledge_base:
		return False
	if _is_manager(user):
		return True

	cache = getattr(frappe.local, "ai_fr_hg_knowledge_base_permission_cache", None)
	cache_key = (user, knowledge_base, bool(write))
	if cache is not None and cache_key in cache:
		return cache[cache_key]

	kb = frappe.db.get_value("AI Knowledge Base", knowledge_base, ["is_public"], as_dict=True)
	if not kb:
		result = False
	else:
		roles = _roles(user)
		grants = frappe.get_all(
			"AI Knowledge Base Role",
			filters={"parent": knowledge_base, "parenttype": "AI Knowledge Base"},
			fields=["role", "can_write"],
		)
		matching = [grant for grant in grants if grant.role in roles]
		result = any(bool(grant.can_write) for grant in matching) if write else bool(kb.is_public or matching)
	if cache is not None:
		cache[cache_key] = result
	return result


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
	if doc.doctype == "AI Translation":
		if doc.get("owner") == user or doc.get("requested_by") == user:
			return True
		return _knowledge_base_access(doc.knowledge_base, user, write=not _is_read(permission_type))
	if doc.doctype == "AI Document Chunk":
		return _is_read(permission_type) and _knowledge_base_access(doc.knowledge_base, user)
	if doc.doctype == "AI Pattern Entity":
		# Machine-written analysis rows: readable exactly like their document's
		# knowledge base, mutable only by the scan service and managers.
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
		if _owned_document_access(doc, user, "owner", permission_type, auditors=True):
			return True
		return _owned_document_access(doc, user, "requested_by", permission_type, auditors=True)
	if doc.doctype == "AI Automation Event":
		return _owned_document_access(doc, user, "requested_by", permission_type, auditors=True)
	if doc.doctype == "AI Pipeline Run":
		return _owned_document_access(doc, user, "triggered_by", permission_type, auditors=True)
	if doc.doctype in {"AI Execution Log", "AI Search Query", "AI Tool Invocation"}:
		return _owned_document_access(doc, user, "user", permission_type, auditors=True)
	if doc.doctype == "AI Folder Favorite":
		if _is_auditor(user) and _is_read(permission_type):
			return True
		return doc.get("user") == user
	if doc.doctype == "AI Folder Settings":
		if _is_read(permission_type):
			# Read allowed if user can read the underlying folder
			try:
				folder_doc = frappe.get_doc("File", doc.folder)
				return frappe.has_permission("File", "read", doc=folder_doc, user=user)
			except Exception:
				return False
		# Write requires write on underlying folder
		try:
			folder_doc = frappe.get_doc("File", doc.folder)
			return frappe.has_permission("File", "write", doc=folder_doc, user=user)
		except Exception:
			return False

	return False


# Wrapped last: the decorator needs the function above to already exist, so
# Desk never sees an ImportError from this module.
has_document_permission = _safe_doc_permission(has_document_permission)
