# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Row-level permission rules layered on top of Frappe's role permissions."""

import frappe

MANAGER_ROLES = {"System Manager", "AI Manager"}


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
