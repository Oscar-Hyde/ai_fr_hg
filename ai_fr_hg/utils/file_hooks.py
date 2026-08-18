# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""File upload integration.

When a file is attached to an ``AI Document`` the platform starts processing it
automatically, so a user can drag a PDF onto a record and get a searchable,
indexed document with no further action.

This module also funnels every ``File`` insert through the canonical folder
service so attachments are never forced into a flat location (§4, §6).
"""

from contextlib import contextmanager
from contextvars import ContextVar

import frappe
from frappe import _
from frappe.utils import cint

from ai_fr_hg.ai.organization import organization_name_key


_AI_DOCUMENT_INGESTION_SUPPRESSED = ContextVar("ai_document_file_ingestion_suppressed", default=False)


@contextmanager
def suppress_ai_document_ingestion():
	"""Suppress attachment ingestion only for an in-process canonical mutation."""
	token = _AI_DOCUMENT_INGESTION_SUPPRESSED.set(True)
	try:
		yield
	finally:
		_AI_DOCUMENT_INGESTION_SUPPRESSED.reset(token)


def before_file_insert(doc, method: str | None = None) -> None:
	"""Lock the canonical parent before a File changes subtree membership."""
	desired = getattr(doc.flags, "folder", None) or getattr(doc, "folder", None)
	from ai_fr_hg.ai.folders import (
		_assert_folder_exists,
		_check_write_access,
		_lock_folder_rows,
		_normalize_folder_path,
		get_default_folder,
	)

	if not desired and not getattr(doc, "is_folder", 0):
		desired = get_default_folder(
			user=frappe.session.user,
			doctype=getattr(doc, "attached_to_doctype", None),
			docname=getattr(doc, "attached_to_name", None),
		)
	if not desired:
		return
	desired = _normalize_folder_path(desired)
	_assert_folder_exists(desired)
	_check_write_access(desired, user=frappe.session.user)
	_lock_folder_rows(desired)
	doc.folder = desired


def before_file_save(doc, method: str | None = None) -> None:
	"""Serialize direct File moves with recursive tree operations."""
	if doc.is_new() or not doc.has_value_changed("folder"):
		return
	old = doc.get_doc_before_save()
	old_folder = getattr(old, "folder", None) or "Home"
	new_folder = getattr(doc, "folder", None) or "Home"
	# ``Home`` is the canonical tree root; do not allow direct File saves to
	# create a parallel empty-folder location for AI-managed Files.
	if not doc.folder:
		doc.folder = new_folder
	if old_folder == new_folder:
		return
	from ai_fr_hg.ai.folders import _assert_folder_exists, _check_write_access, _lock_folder_rows

	_assert_folder_exists(new_folder)
	_check_write_access(new_folder, user=frappe.session.user)
	_lock_folder_rows(old_folder, new_folder)


def on_file_upload(doc, method: str | None = None) -> None:
	"""Canonical folder assignment + ingestion kickoff.

	Folder selection is validated server-side. If the client supplied a
	custom ``folder`` (via the folder selector in the attachment dialog or
	bulk upload), we honor it after permission checks. Otherwise we resolve
	the sensible default via :func:`ai_fr_hg.ai.folders.get_default_folder`.

	We never silently default elsewhere on unauthorized access — we fail typed
	(Master §22, File & Folder §4.2).
	"""
	# ------------------------------------------------------------------
	# 1. Canonicalize folder assignment for every File (not just AI Document)
	# ------------------------------------------------------------------
	if not getattr(doc, "is_folder", 0):
		from ai_fr_hg.ai.folders import assign_file_to_folder, get_default_folder

		desired = getattr(doc.flags, "folder", None) or getattr(doc, "folder", None)
		if not desired:
			desired = get_default_folder(
				user=frappe.session.user,
				doctype=getattr(doc, "attached_to_doctype", None),
				docname=getattr(doc, "attached_to_name", None),
			)
		result = assign_file_to_folder(
			doc.name,
			desired,
			attached_to_doctype=getattr(doc, "attached_to_doctype", None),
			attached_to_name=getattr(doc, "attached_to_name", None),
			attached_to_field=getattr(doc, "attached_to_field", None),
			user=frappe.session.user,
		)
		doc.folder = result["folder"]

	# ------------------------------------------------------------------
	# 2. AI Document ingestion pathway (preserve folder as provenance)
	# ------------------------------------------------------------------
	# Tree copies attach a new, independent File identity and finalize the
	# copied document explicitly in the same transaction. Do not turn that
	# reset Draft into an automatic processing run from this generic hook.
	# This capability is process-local and cannot be forged in a File payload.
	if _AI_DOCUMENT_INGESTION_SUPPRESSED.get():
		return
	if doc.attached_to_doctype != "AI Document" or not doc.attached_to_name:
		return

	document = doc.attached_to_name
	if not frappe.db.exists("AI Document", document):
		return
	permission_doc = frappe.get_doc("AI Document", document)
	if not frappe.has_permission(
		"AI Document",
		"write",
		doc=permission_doc,
		user=frappe.session.user,
	):
		frappe.throw(_("You do not have permission to attach a source to this AI Document."), frappe.PermissionError)
	# ``assign_file_to_folder`` already acquired parent-folder and File locks.
	# Lock the owning AI Document only afterwards so uploads serialize with
	# document save/delete without reversing the canonical lock order.
	current = frappe.db.get_value(
		"AI Document",
		document,
		["source_file", "source_file_record", "status", "folder", "organization_revision"],
		as_dict=True,
		for_update=True,
	)
	if not current:
		frappe.throw(_("The AI Document was deleted while its attachment was being saved."), frappe.DoesNotExistError)
	# Other attachments must not replace an established canonical source, even
	# when Frappe deduplicates their bytes to the same URL.
	if current.source_file_record and current.source_file_record != doc.name:
		return
	if current.source_file and current.source_file != doc.file_url:
		return

	from ai_fr_hg.ai.document_tree import resolve_document_name

	folder_path = getattr(doc, "folder", None) or current.folder or "Home"
	organization_name = resolve_document_name(
		folder_path,
		doc.file_name,
		copy_on_collision=True,
		exclude=document,
	)
	auto_process = bool(frappe.db.get_single_value("AI Platform Settings", "auto_process_documents"))
	updates = {
		"source_file": doc.file_url,
		"source_file_record": doc.name,
		"source_type": "File",
		"folder": folder_path,
		"source_folder": folder_path,
		"organization_name": organization_name,
		"organization_name_key": organization_name_key(organization_name),
		"organization_revision": cint(current.organization_revision) + 1,
	}
	if auto_process and current.status in ("Draft", "Queued"):
		updates["status"] = "Queued"
	frappe.db.set_value("AI Document", document, updates, update_modified=True)

	if auto_process and current.status in ("Draft", "Queued"):
		from ai_fr_hg.ai.ingestion import enqueue_processing

		enqueue_processing(document)


def on_file_update(doc, method: str | None = None) -> None:
	"""Track File moves and atomically synchronize stable document provenance."""
	if getattr(doc, "is_folder", 0) or not doc.has_value_changed("folder"):
		return
	old = doc.get_doc_before_save().get("folder") if doc.get_doc_before_save() else None
	new = doc.folder
	if old == new:
		return

	from ai_fr_hg.ai.folders import _update_document_folder_provenance, track_folder_operation

	track_folder_operation("update_file_folder", doc.name, new, frappe.session.user, details={"old": old})
	# Do not allow a File move to commit while its linked AI Document points to
	# the old parent. Raising here rolls the complete Frappe save back.
	_update_document_folder_provenance(doc.name, new)


def on_file_delete(doc, method: str | None = None) -> None:
	"""Clean up folder settings when a folder is deleted and audit file deletes."""
	from ai_fr_hg.ai.folders import _lock_folder_rows

	_lock_folder_rows(getattr(doc, "folder", None))
	from ai_fr_hg.ai.folders import track_folder_operation

	if getattr(doc, "is_folder", 0):
		# Remove orphaned AI Folder Settings and favorites in the same transaction.
		if frappe.db.exists("AI Folder Settings", {"folder": doc.name}):
			frappe.db.delete("AI Folder Settings", {"folder": doc.name})
		frappe.db.delete("AI Folder Favorite", {"folder": doc.name})
		track_folder_operation("delete_folder_hook", doc.name, None, frappe.session.user)
	else:
		track_folder_operation("delete_file_hook", doc.name, doc.folder, frappe.session.user)
