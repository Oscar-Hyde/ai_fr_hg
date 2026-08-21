# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Canonical conversation service.

Owns history windows (CHAT-01), atomic sequence allocation (CHAT-02),
configuration persistence (CHAT-03/04), conversation actions (CHAT-05),
feedback persistence (CHAT-06 via learning), and turn cancellation (CHAT-07).

The public API facade in ``api/chat.py`` must stay thin: validation and
transport only. Authorization is enforced here, not only in the wrapper.
"""

from __future__ import annotations

import json

import frappe
from frappe import _
from frappe.utils import cint, now_datetime

from ai_fr_hg.ai.conversation_utils import (
	ACTIVE_TURN_STATUSES,
	CANCEL_TTL_SECONDS,
	HISTORY_LIMIT,
	HISTORY_STATUSES,
	cancel_cache_key,
	window_latest_messages,
)
from ai_fr_hg.ai.exceptions import TurnCancelledError
from ai_fr_hg.ai.providers.base import ChatMessage
from ai_fr_hg.utils import api_validation

MESSAGE_LIST_FIELDS = [
	"name",
	"role",
	"content",
	"reasoning",
	"citations",
	"learned_context",
	"sequence",
	"creation",
	"model",
	"tool",
	"tool_arguments",
	"tool_result",
	"total_tokens",
	"duration_ms",
	"feedback",
	"feedback_reason",
	"feedback_comment",
	"status",
	"error_message",
	"turn_id",
]


def require_conversation(conversation: str, ptype: str = "read"):
	"""Load a conversation and enforce the caller's DocType permission."""
	if not conversation:
		frappe.throw(_("Conversation is required."), frappe.ValidationError)
	doc = frappe.get_doc("AI Conversation", conversation)
	doc.check_permission(ptype)
	return doc


def request_cancel(turn_id: str) -> None:
	"""Mark a turn cancelled in cache so in-flight workers observe it."""
	if not turn_id:
		return
	frappe.cache.set_value(cancel_cache_key(turn_id), 1, expires_in_sec=CANCEL_TTL_SECONDS)


def is_turn_cancelled(turn_id: str | None) -> bool:
	if not turn_id:
		return False
	return bool(frappe.cache.get_value(cancel_cache_key(turn_id)))


def raise_if_cancelled(turn_id: str | None, partial: str = "") -> None:
	if is_turn_cancelled(turn_id):
		raise TurnCancelledError(partial=partial)


def allocate_sequence(conversation: str) -> int:
	"""Reserve the next sequence under a conversation row lock (CHAT-02).

	Both statements below are **locking** reads, and the second one is the
	whole point of this docstring.

	InnoDB's default isolation level is REPEATABLE READ, under which a plain
	``SELECT`` is served from the transaction's consistent snapshot. That
	snapshot is established at the transaction's *first* read — for a Frappe
	worker, somewhere inside ``frappe.connect()``, long before this function
	runs. So an ordinary ``select max(sequence)`` here would return the value
	as of transaction start, not as of lock acquisition.

	The effect was subtle and real: the ``for update`` on the conversation row
	correctly serialized competing writers, then each one read a *stale* max
	and handed out a sequence another writer had already committed. A real
	100-worker bench run produced ``[1, 1, 2, ... 99]``.

	``for update`` on the aggregate makes it a current read, so it observes
	every transaction that committed before this one acquired the lock.
	"""
	frappe.db.sql(
		"select name from `tabAI Conversation` where name = %s for update",
		(conversation,),
	)
	last = frappe.db.sql(
		"select coalesce(max(sequence), 0) from `tabAI Message` where conversation = %s for update",
		(conversation,),
	)[0][0]
	return cint(last) + 1


def save_message(conversation: str, role: str, content: str, **kwargs):
	"""Append a message with a locked, unique sequence (CHAT-02)."""
	sequence = kwargs.pop("sequence", None)
	if sequence is None:
		sequence = allocate_sequence(conversation)

	payload = {
		"doctype": "AI Message",
		"conversation": conversation,
		"role": role,
		"content": content,
		"sequence": cint(sequence),
		"status": kwargs.pop("status", None) or "Completed",
		"user": kwargs.pop("user", None) or frappe.session.user,
	}
	payload.update({key: value for key, value in kwargs.items() if value is not None})
	message = frappe.get_doc(payload)
	message.flags.ignore_permissions = True
	message.flags.sequence_allocated = True
	message.insert(ignore_permissions=True)
	return message


def get_conversation_history(conversation: str, limit: int = HISTORY_LIMIT) -> list[ChatMessage]:
	"""Load the latest eligible turns as chat messages (CHAT-01)."""
	limit = max(1, min(cint(limit) or HISTORY_LIMIT, 200))
	fetch = limit + 16
	rows = frappe.get_all(
		"AI Message",
		filters={"conversation": conversation, "status": ["in", list(HISTORY_STATUSES)]},
		fields=["role", "content", "tool_call_id", "tool_arguments", "tool_result", "tool", "sequence"],
		order_by="sequence desc, creation desc",
		limit=fetch,
	)
	window = window_latest_messages([dict(row) for row in rows], limit)

	messages: list[ChatMessage] = []
	for row in window:
		role = (row.get("role") or "user").lower()
		if role == "system":
			continue
		if role == "tool":
			messages.append(
				ChatMessage(
					role="tool",
					content=row.get("tool_result") or "",
					name=row.get("tool"),
					tool_call_id=row.get("tool_call_id"),
				)
			)
		else:
			messages.append(ChatMessage(role=role, content=row.get("content") or ""))
	return messages


def history_was_truncated(conversation: str, limit: int = HISTORY_LIMIT) -> bool:
	count = cint(
		frappe.db.count(
			"AI Message", {"conversation": conversation, "status": ["in", list(HISTORY_STATUSES)]}
		)
	)
	return count > max(1, min(cint(limit) or HISTORY_LIMIT, 200))


def _parse_json_field(value, empty):
	if not value:
		return empty
	if not isinstance(value, str):
		return value
	try:
		return json.loads(value)
	except ValueError:
		return empty


def _decorate_messages(rows) -> list[dict]:
	messages = []
	for row in rows:
		item = dict(row)
		item["citations"] = _parse_json_field(item.get("citations"), [])
		item["learned_context"] = _parse_json_field(item.get("learned_context"), {})
		messages.append(item)
	return messages


def get_conversation_payload(conversation: str, *, limit: int | None = None, offset: int = 0) -> dict:
	"""Conversation configuration plus a bounded message page (CHAT-03/05)."""
	doc = require_conversation(conversation, "read")
	page, start = api_validation.pagination(
		limit, offset, default_limit=100, hard_limit=api_validation.MAX_MESSAGE_PAGE
	)
	rows = frappe.get_all(
		"AI Message",
		filters={"conversation": conversation},
		fields=MESSAGE_LIST_FIELDS,
		order_by="sequence asc, creation asc",
		limit=page,
		start=start,
	)
	total = cint(frappe.db.count("AI Message", {"conversation": conversation}))
	return {
		"conversation": doc.as_dict(),
		"messages": _decorate_messages(rows),
		"limit": page,
		"offset": start,
		"total": total,
		"has_more": start + len(rows) < total,
	}


def list_conversations(*, limit: int = 50, offset: int = 0, include_archived: bool = False) -> dict:
	"""Permission-aware conversation list with v17 limit/offset pagination."""
	page, start = api_validation.pagination(
		limit, offset, default_limit=50, hard_limit=api_validation.MAX_CONVERSATION_PAGE
	)
	filters: dict = {}
	if not include_archived:
		filters["status"] = "Active"
	rows = frappe.get_list(
		"AI Conversation",
		filters=filters,
		fields=["name", "title", "agent", "model", "message_count", "last_message_on", "pinned", "status"],
		order_by="pinned desc, last_message_on desc, creation desc",
		limit=page,
		offset=start,
	)
	return {"conversations": rows, "limit": page, "offset": start, "has_more": len(rows) == page}


def rename_conversation(conversation: str, title: str) -> dict:
	title = api_validation.bounded_text(title, label=_("Title"), max_length=140, required=True)
	doc = require_conversation(conversation, "write")
	doc.db_set("title", title[:140])
	return {"status": "renamed", "title": title, "conversation": conversation}


def pin_conversation(conversation: str, pinned: int | bool = True) -> dict:
	doc = require_conversation(conversation, "write")
	value = 1 if pinned and cint(pinned) else 0
	doc.db_set("pinned", value)
	return {"status": "pinned" if value else "unpinned", "pinned": value, "conversation": conversation}


def archive_conversation(conversation: str) -> dict:
	doc = require_conversation(conversation, "write")
	doc.db_set("status", "Archived")
	return {"status": "archived", "conversation": conversation}


def restore_conversation(conversation: str) -> dict:
	doc = require_conversation(conversation, "write")
	if doc.status != "Archived":
		return {"status": "active", "conversation": conversation}
	doc.db_set("status", "Active")
	return {"status": "restored", "conversation": conversation}


def delete_conversation(conversation: str) -> dict:
	require_conversation(conversation, "delete")
	frappe.db.delete("AI Message", {"conversation": conversation})
	frappe.delete_doc("AI Conversation", conversation)
	return {"status": "deleted", "conversation": conversation}


def export_conversation(conversation: str) -> dict:
	doc = require_conversation(conversation, "read")
	messages = frappe.get_all(
		"AI Message",
		filters={"conversation": conversation},
		fields=["role", "content", "sequence", "creation", "citations", "status", "turn_id"],
		order_by="sequence asc",
		limit=10_000,
	)
	return {
		"conversation": {
			"name": doc.name,
			"title": doc.title,
			"agent": doc.agent,
			"model": doc.model,
			"status": doc.status,
			"pinned": cint(doc.pinned),
			"context_document": doc.context_document,
			"summary": doc.summary,
		},
		"messages": messages,
	}


def get_messages(conversation: str, *, limit: int = 50, offset: int = 0) -> dict:
	doc = require_conversation(conversation, "read")
	page, start = api_validation.pagination(
		limit, offset, default_limit=50, hard_limit=api_validation.MAX_MESSAGE_PAGE
	)
	rows = frappe.get_all(
		"AI Message",
		filters={"conversation": conversation},
		fields=MESSAGE_LIST_FIELDS,
		order_by="sequence asc, creation asc",
		limit=page,
		start=start,
	)
	return {
		"conversation": doc.name,
		"messages": _decorate_messages(rows),
		"limit": page,
		"offset": start,
		"has_more": len(rows) == page,
	}


def sync_turn_configuration(
	conversation: str,
	*,
	agent: str | None = None,
	model: str | None = None,
	knowledge_bases: list[str] | None = None,
) -> None:
	"""Persist the selectors used for this turn so reload restores them (CHAT-03)."""
	doc = require_conversation(conversation, "write")
	changed = False
	if agent and doc.agent != agent:
		doc.agent = agent
		changed = True
	if model is not None and doc.model != model:
		doc.model = model or None
		changed = True
	if knowledge_bases is not None:
		current = [row.knowledge_base for row in doc.get("knowledge_bases") or [] if row.knowledge_base]
		if current != list(knowledge_bases):
			doc.set("knowledge_bases", [])
			for kb in knowledge_bases:
				if kb:
					doc.append("knowledge_bases", {"knowledge_base": kb})
			changed = True
	if changed:
		doc.flags.ignore_permissions = True
		doc.save(ignore_permissions=True)


def update_conversation_config(
	conversation: str,
	*,
	agent: str | None = None,
	model: str | None = None,
	knowledge_bases: list[str] | None = None,
	context_document: str | None = None,
) -> dict:
	doc = require_conversation(conversation, "write")
	if agent:
		doc.agent = agent
	if model is not None:
		doc.model = model or None
	if knowledge_bases is not None:
		doc.set("knowledge_bases", [])
		for kb in knowledge_bases:
			if kb:
				doc.append("knowledge_bases", {"knowledge_base": kb})
	if context_document is not None:
		if context_document:
			if not frappe.db.exists("AI Document", context_document):
				frappe.throw(_("AI Document {0} does not exist.").format(context_document))
			frappe.has_permission("AI Document", "read", doc=context_document, throw=True)
		doc.context_document = context_document or None
	doc.flags.ignore_permissions = True
	doc.save(ignore_permissions=True)
	return {
		"conversation": doc.name,
		"agent": doc.agent,
		"model": doc.model,
		"context_document": doc.context_document,
	}


def cancel_turn(conversation: str, turn_id: str | None = None) -> dict:
	"""Cooperative cancellation keyed by ``turn_id`` (CHAT-07)."""
	require_conversation(conversation, "write")
	resolved_turn = (turn_id or "").strip() or None
	filters: dict = {"conversation": conversation, "status": ["in", list(ACTIVE_TURN_STATUSES)]}
	if resolved_turn:
		request_cancel(resolved_turn)
		filters["turn_id"] = resolved_turn

	rows = frappe.get_all(
		"AI Message",
		filters=filters,
		fields=["name", "turn_id"],
		order_by="sequence desc",
		limit=20,
	)
	if not resolved_turn and rows:
		resolved_turn = rows[0].turn_id
		if resolved_turn:
			request_cancel(resolved_turn)
			rows = frappe.get_all(
				"AI Message",
				filters={
					"conversation": conversation,
					"turn_id": resolved_turn,
					"status": ["in", list(ACTIVE_TURN_STATUSES)],
				},
				fields=["name", "turn_id"],
				limit=20,
			)

	updated = []
	for row in rows:
		frappe.db.set_value(
			"AI Message",
			row.name,
			{"status": "Cancelled", "error_message": _("Cancelled by user")},
		)
		updated.append(row.name)

	frappe.publish_realtime(
		"ai_turn_cancelled",
		{"conversation": conversation, "turn_id": resolved_turn, "messages": updated},
		doctype="AI Conversation",
		docname=conversation,
	)
	return {
		"status": "cancelled" if (updated or resolved_turn) else "no_active_turn",
		"turn_id": resolved_turn,
		"messages": updated,
	}


def get_turn_status(conversation: str, turn_id: str) -> dict:
	"""Reconnect payload for one turn (CHAT-07)."""
	require_conversation(conversation, "read")
	turn_id = api_validation.bounded_text(turn_id, label=_("Turn"), max_length=64, required=True)
	rows = frappe.get_all(
		"AI Message",
		filters={"conversation": conversation, "turn_id": turn_id},
		fields=MESSAGE_LIST_FIELDS,
		order_by="sequence asc",
		limit=50,
	)
	messages = _decorate_messages(rows)
	statuses = {row.get("status") for row in messages}
	if "Cancelled" in statuses or is_turn_cancelled(turn_id):
		state = "Cancelled"
	elif statuses & ACTIVE_TURN_STATUSES:
		state = "Streaming"
	elif "Failed" in statuses:
		state = "Failed"
	elif "Completed" in statuses:
		state = "Completed"
	else:
		state = "Unknown"
	assistant = next((row for row in reversed(messages) if row.get("role") == "Assistant"), None)
	return {
		"conversation": conversation,
		"turn_id": turn_id,
		"status": state,
		"cancelled": state == "Cancelled",
		"content": (assistant or {}).get("content") or "",
		"message": (assistant or {}).get("name"),
		"messages": messages,
	}


def focused_documents(conversation_doc, documents: list[str] | None) -> list[str] | None:
	"""Merge ``context_document`` into the authorized document scope (CHAT-04)."""
	names = list(documents or [])
	focused = conversation_doc.get("context_document") if conversation_doc else None
	if focused and focused not in names:
		if frappe.db.exists("AI Document", focused) and frappe.has_permission(
			"AI Document", "read", doc=focused
		):
			names.append(focused)
	return names or None
