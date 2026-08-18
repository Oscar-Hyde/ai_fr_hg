# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""File upload integration.

When a file is attached to an ``AI Document`` the platform starts processing it
automatically, so a user can drag a PDF onto a record and get a searchable,
indexed document with no further action.

This module also funnels every ``File`` insert through the canonical folder
service so attachments are never forced into a flat location (§4, §6).
"""

import frappe

from ai_fr_hg.ai.exceptions import FolderError


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
	try:
		from ai_fr_hg.ai.folders import (
			_assert_folder_exists,
			_check_write_access,
			_normalize_folder_path,
			get_default_folder,
		)

		# Skip folders themselves
		if getattr(doc, "is_folder", 0):
			pass
		else:
			# Determine desired folder
			desired = None
			if getattr(doc.flags, "folder", None):
				desired = doc.flags.folder
			elif getattr(doc, "folder", None):
				desired = doc.folder

			if not desired:
				desired = get_default_folder(
					user=doc.owner or frappe.session.user,
					doctype=getattr(doc, "attached_to_doctype", None),
					docname=getattr(doc, "attached_to_name", None),
				)

			desired = _normalize_folder_path(desired)
			_assert_folder_exists(desired)
			_check_write_access(desired, user=doc.owner or frappe.session.user)

			if doc.folder != desired:
				frappe.db.set_value("File", doc.name, "folder", desired, update_modified=False)
				doc.folder = desired

	except FolderError as exc:
		frappe.log_error(title="File folder assignment failed", message=str(exc))
		if "Folder" in type(exc).__name__ or "Permission" in type(exc).__name__:
			raise
	except Exception:
		frappe.log_error(title="File folder assignment unexpected error", message=frappe.get_traceback())

	# ------------------------------------------------------------------
	# 2. AI Document ingestion pathway (preserve folder as provenance)
	# ------------------------------------------------------------------
	if doc.attached_to_doctype != "AI Document" or not doc.attached_to_name:
		return
	if not frappe.db.get_single_value("AI Platform Settings", "auto_process_documents"):
		return

	document = doc.attached_to_name
	if not frappe.db.exists("AI Document", document):
		return

	current = frappe.db.get_value("AI Document", document, ["source_file", "status", "folder", "source_folder"], as_dict=True)
	if current.source_file or current.status not in ("Draft", "Queued"):
		return

	folder_path = getattr(doc, "folder", None)
	if folder_path and frappe.db.exists("File", folder_path):
		frappe.db.set_value(
			"AI Document",
			document,
			{"source_file": doc.file_url, "source_type": "File", "status": "Queued", "folder": folder_path, "source_folder": folder_path},
			update_modified=False,
		)
	else:
		frappe.db.set_value(
			"AI Document",
			document,
			{"source_file": doc.file_url, "source_type": "File", "status": "Queued"},
			update_modified=False,
		)

	from ai_fr_hg.ai.ingestion import enqueue_processing

	enqueue_processing(document)

def on_file_update(doc, method: str | None = None) -> None:
	"""Track file moves/renames for audit provenance (§8, §23)."""
	try:
		if getattr(doc, "is_folder", 0):
			return
		# If folder changed, record audit and update AI Document provenance
		if doc.has_value_changed("folder"):
			old = doc.get_doc_before_save().get("folder") if doc.get_doc_before_save() else None
			new = doc.folder
			if old != new:
				from ai_fr_hg.ai.folders import track_folder_operation, _update_document_folder_provenance

				track_folder_operation("update_file_folder", doc.name, new, frappe.session.user, details={"old": old})
				try:
					_update_document_folder_provenance(doc.name, new)
				except Exception:
					frappe.log_error(title="Folder provenance sync failed on file update", message=frappe.get_traceback())
	except Exception:
		frappe.log_error(title="File update hook failed", message=frappe.get_traceback())


def on_file_delete(doc, method: str | None = None) -> None:
	"""Clean up folder settings when a folder is deleted and audit file deletes."""
	try:
		from ai_fr_hg.ai.folders import track_folder_operation

		if getattr(doc, "is_folder", 0):
			# Remove orphaned AI Folder Settings
			if frappe.db.exists("AI Folder Settings", {"folder": doc.name}):
				frappe.db.delete("AI Folder Settings", {"folder": doc.name})
			# Favorites cleanup is not strictly required but keeps UI clean
			frappe.db.delete("AI Folder Favorite", {"folder": doc.name})
			track_folder_operation("delete_folder_hook", doc.name, None, frappe.session.user)
		else:
			track_folder_operation("delete_file_hook", doc.name, doc.folder, frappe.session.user)
	except Exception:
		frappe.log_error(title="File delete hook failed", message=frappe.get_traceback())
