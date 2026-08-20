# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Whitelisted chat and conversation endpoints.

Thin transport/RPC facade. Conversation authorization, history, sequencing,
cancellation, and configuration live in ``ai.conversation`` / ``ai.agent``.
"""

import json

import frappe
from frappe import _
from frappe.utils import cint


@frappe.whitelist()
def send_message(
	message: str,
	conversation: str | None = None,
	agent: str | None = None,
	knowledge_bases: str | list | None = None,
	model: str | None = None,
	documents: str | list | None = None,
	stream: bool | int | str = False,
	turn_id: str | None = None,
) -> dict:
	"""Send a message and return the assistant's reply with its citations.

	`documents` lists `AI Document` records the user has just uploaded. The
	turn prepares them immediately (inline extraction if needed, a short wait
	only when nothing readable exists) and grounds the answer on those files,
	so asking "summarise the file I just uploaded" uses the new upload.
	"""
	from ai_fr_hg.ai.agent import create_conversation, run_agent_turn
	from ai_fr_hg.ai.deadline import turn_budget
	from ai_fr_hg.ai.engine import publish_chat_token
	from ai_fr_hg.ai.ingestion import prepare_documents_for_turn
	from ai_fr_hg.ai.settings import should_stream_completion
	from ai_fr_hg.utils import api_validation

	message = api_validation.bounded_text(
		message, label=_("Message"), max_length=api_validation.MAX_CHAT_MESSAGE_CHARS, required=True
	)
	knowledge_bases = api_validation.bounded_list(
		knowledge_bases, label=_("Knowledge bases"), max_items=api_validation.MAX_KNOWLEDGE_BASES_PER_REQUEST
	)
	documents = api_validation.bounded_list(
		documents, label=_("Documents"), max_items=api_validation.MAX_DOCUMENTS_PER_TURN
	)
	documents = _coerce_documents(documents)
	turn_id = api_validation.bounded_text(turn_id, label=_("Turn"), max_length=64) if turn_id else None

	if not conversation:
		conversation_doc = create_conversation(agent=agent, knowledge_bases=knowledge_bases or None)
		conversation = conversation_doc.name
	else:
		from ai_fr_hg.ai.conversation import require_conversation

		require_conversation(conversation, "write")

	turn_id = (turn_id or "").strip() or frappe.generate_hash(length=12)
	want_stream = should_stream_completion(
		requested=bool(cint(stream)),
		enabled=bool(cint(frappe.db.get_single_value("AI Platform Settings", "streaming_enabled"))),
		offer_tools=None,
	)

	def on_token(delta: str) -> None:
		publish_chat_token(conversation, turn_id, delta)

	with turn_budget(_get_turn_budget()):
		extra_context = None
		if documents:
			documents, extra_context = prepare_documents_for_turn(documents)
		result = run_agent_turn(
			message,
			agent=agent,
			conversation=conversation,
			knowledge_bases=knowledge_bases or None,
			model=model,
			documents=documents or None,
			extra_context=extra_context or None,
			on_token=on_token if want_stream else None,
			turn_id=turn_id,
		)
	result["turn_id"] = result.get("turn_id") or turn_id
	result["streamed"] = bool(result.pop("_streamed", False))
	return result


def _coerce_documents(documents) -> list[str]:
	"""Normalise the `documents` argument and check the caller may read them."""
	if isinstance(documents, str):
		try:
			documents = json.loads(documents)
		except ValueError:
			documents = [documents]

	names = [doc for doc in (documents or []) if doc]
	for name in names:
		if not frappe.db.exists("AI Document", name):
			frappe.throw(_("AI Document {0} does not exist.").format(name))
		frappe.has_permission("AI Document", "read", doc=name, throw=True)
	return names


def _get_turn_budget() -> int:
	"""Seconds one interactive turn may take. 0 disables the budget."""
	from ai_fr_hg.ai.settings import coerce_turn_budget

	configured = frappe.db.get_single_value("AI Platform Settings", "max_turn_seconds")
	return coerce_turn_budget(configured)


@frappe.whitelist()
def start_conversation(
	agent: str | None = None,
	title: str | None = None,
	knowledge_bases: str | list | None = None,
) -> dict:
	"""Create a new conversation."""
	from ai_fr_hg.ai.agent import create_conversation
	from ai_fr_hg.utils import api_validation

	knowledge_bases = api_validation.bounded_list(
		knowledge_bases, label=_("Knowledge bases"), max_items=api_validation.MAX_KNOWLEDGE_BASES_PER_REQUEST
	)
	if title:
		title = api_validation.bounded_text(title, label=_("Title"), max_length=140)
	doc = create_conversation(agent=agent, title=title, knowledge_bases=knowledge_bases or None)
	return {"conversation": doc.name, "title": doc.title, "agent": doc.agent}


@frappe.whitelist()
def get_conversation(conversation: str, limit: int = 100, offset: int = 0) -> dict:
	"""Return a conversation with a bounded, paginated message history."""
	from ai_fr_hg.ai.conversation import get_conversation_payload
	from ai_fr_hg.utils import api_validation

	conversation = api_validation.valid_identifier(conversation, label=_("Conversation"), required=True)
	return get_conversation_payload(conversation, limit=limit, offset=offset)


@frappe.whitelist()
def list_conversations(limit: int = 50, offset: int = 0, include_archived: bool = False) -> dict | list:
	"""List conversations the caller may read, most recent first."""
	from ai_fr_hg.ai.conversation import list_conversations as _list

	payload = _list(limit=limit, offset=offset, include_archived=include_archived)
	# Keep the historical list shape for callers that ignore pagination keys.
	return payload


@frappe.whitelist()
def delete_conversation(conversation: str) -> dict:
	from ai_fr_hg.ai.conversation import delete_conversation as _delete
	from ai_fr_hg.utils import api_validation

	conversation = api_validation.valid_identifier(conversation, label=_("Conversation"), required=True)
	return _delete(conversation)


@frappe.whitelist()
def archive_conversation(conversation: str) -> dict:
	from ai_fr_hg.ai.conversation import archive_conversation as _archive
	from ai_fr_hg.utils import api_validation

	conversation = api_validation.valid_identifier(conversation, label=_("Conversation"), required=True)
	return _archive(conversation)


@frappe.whitelist()
def rename_conversation(conversation: str, title: str) -> dict:
	from ai_fr_hg.ai.conversation import rename_conversation as _rename
	from ai_fr_hg.utils import api_validation

	conversation = api_validation.valid_identifier(conversation, label=_("Conversation"), required=True)
	return _rename(conversation, title)


@frappe.whitelist()
def pin_conversation(conversation: str, pinned: int | bool = True) -> dict:
	from ai_fr_hg.ai.conversation import pin_conversation as _pin
	from ai_fr_hg.utils import api_validation

	conversation = api_validation.valid_identifier(conversation, label=_("Conversation"), required=True)
	return _pin(conversation, pinned)


@frappe.whitelist()
def restore_conversation(conversation: str) -> dict:
	from ai_fr_hg.ai.conversation import restore_conversation as _restore
	from ai_fr_hg.utils import api_validation

	conversation = api_validation.valid_identifier(conversation, label=_("Conversation"), required=True)
	return _restore(conversation)


@frappe.whitelist()
def get_messages(conversation: str, limit: int = 50, offset: int = 0) -> dict:
	from ai_fr_hg.ai.conversation import get_messages as _get
	from ai_fr_hg.utils import api_validation

	conversation = api_validation.valid_identifier(conversation, label=_("Conversation"), required=True)
	return _get(conversation, limit=limit, offset=offset)


@frappe.whitelist()
def export_conversation(conversation: str) -> dict:
	from ai_fr_hg.ai.conversation import export_conversation as _export
	from ai_fr_hg.utils import api_validation

	conversation = api_validation.valid_identifier(conversation, label=_("Conversation"), required=True)
	return _export(conversation)


@frappe.whitelist()
def cancel_turn(conversation: str, turn_id: str | None = None) -> dict:
	from ai_fr_hg.ai.conversation import cancel_turn as _cancel
	from ai_fr_hg.utils import api_validation

	conversation = api_validation.valid_identifier(conversation, label=_("Conversation"), required=True)
	if turn_id:
		turn_id = api_validation.bounded_text(turn_id, label=_("Turn"), max_length=64)
	return _cancel(conversation, turn_id)


@frappe.whitelist()
def get_turn_status(conversation: str, turn_id: str) -> dict:
	from ai_fr_hg.ai.conversation import get_turn_status as _status
	from ai_fr_hg.utils import api_validation

	conversation = api_validation.valid_identifier(conversation, label=_("Conversation"), required=True)
	return _status(conversation, turn_id)


@frappe.whitelist()
def update_conversation_config(
	conversation: str,
	agent: str | None = None,
	model: str | None = None,
	knowledge_bases: str | list | None = None,
	context_document: str | None = None,
) -> dict:
	from ai_fr_hg.ai.conversation import update_conversation_config as _update
	from ai_fr_hg.utils import api_validation

	conversation = api_validation.valid_identifier(conversation, label=_("Conversation"), required=True)
	knowledge_bases = (
		api_validation.bounded_list(
			knowledge_bases,
			label=_("Knowledge bases"),
			max_items=api_validation.MAX_KNOWLEDGE_BASES_PER_REQUEST,
		)
		if knowledge_bases is not None
		else None
	)
	return _update(
		conversation,
		agent=agent,
		model=model,
		knowledge_bases=knowledge_bases,
		context_document=context_document,
	)


@frappe.whitelist()
def submit_feedback(
	message: str,
	feedback: str,
	correction: str | None = None,
	reason: str | None = None,
) -> dict:
	"""Record an outcome and feed it through the governed Learning Loop."""
	from ai_fr_hg.ai.learning import record_feedback
	from ai_fr_hg.utils import api_validation

	message = api_validation.valid_identifier(message, label=_("Message"), required=True)
	feedback = api_validation.enum_choice(
		feedback, allowed=("", "Positive", "Negative"), label=_("Feedback"), default=""
	)
	if correction:
		correction = api_validation.bounded_text(correction, label=_("Correction"), max_length=4000)
	if reason:
		reason = api_validation.enum_choice(
			reason,
			allowed=("", "Correction", "Missing Information", "Incorrect Information"),
			label=_("Reason"),
			default="",
		)
	result = record_feedback(message, feedback, correction=correction, reason=reason)
	return {"status": "recorded", **result}


@frappe.whitelist()
def summarize_conversation(conversation: str) -> dict:
	"""Generate and store a summary of a conversation."""
	from ai_fr_hg.ai.conversation import require_conversation
	from ai_fr_hg.ai.intelligence import summarize
	from ai_fr_hg.utils import api_validation

	conversation = api_validation.valid_identifier(conversation, label=_("Conversation"), required=True)
	doc = require_conversation(conversation, "write")

	messages = frappe.get_all(
		"AI Message",
		filters={"conversation": conversation, "role": ["in", ["User", "Assistant"]]},
		fields=["role", "content"],
		order_by="sequence asc",
		limit=500,
	)
	if not messages:
		frappe.throw(_("This conversation has no messages to summarise."))

	transcript = "\n\n".join(f"{m.role}: {m.content}" for m in messages if m.content)
	summary = summarize(
		transcript,
		instructions="Summarise this conversation, listing the questions asked and the conclusions reached.",
		reference_doctype="AI Conversation",
		reference_name=conversation,
	)
	doc.db_set("summary", summary)
	return {"conversation": conversation, "summary": summary}


@frappe.whitelist()
def get_chat_context() -> dict:
	"""Bootstrap payload for the chat interface: agents, models, knowledge bases."""
	from ai_fr_hg.ai.knowledge import get_accessible_knowledge_bases

	roles = set(frappe.get_roles())
	agents = []
	for agent in frappe.get_all(
		"AI Agent",
		filters={"enabled": 1},
		fields=["name", "agent_name", "description", "model", "is_default", "greeting", "use_knowledge"],
		order_by="is_default desc, agent_name asc",
		limit=200,
	):
		allowed = frappe.get_all("AI Agent Role", filters={"parent": agent.name}, pluck="role")
		if allowed and frappe.session.user != "Administrator" and not roles.intersection(allowed):
			continue
		agents.append(agent)

	accessible = get_accessible_knowledge_bases()

	models = frappe.get_all(
		"AI Model",
		filters={"enabled": 1, "model_type": ["in", ["Chat", "Vision"]]},
		fields=["name", "model_label", "model_type", "status", "provider", "is_default"],
		order_by="is_default desc, model_label asc",
		limit=200,
	)

	return {
		"agents": agents,
		"models": models,
		"knowledge_bases": frappe.get_all(
			"AI Knowledge Base",
			filters={"name": ["in", accessible or [""]], "enabled": 1},
			fields=["name", "knowledge_base_name", "document_count", "chunk_count"],
			order_by="knowledge_base_name asc",
			limit=200,
		),
		"settings": {
			"streaming_enabled": frappe.db.get_single_value("AI Platform Settings", "streaming_enabled"),
			"platform_enabled": frappe.db.get_single_value("AI Platform Settings", "platform_enabled"),
			"default_chat_model": frappe.db.get_single_value("AI Platform Settings", "default_chat_model"),
			"max_turn_seconds": cint(frappe.db.get_single_value("AI Platform Settings", "max_turn_seconds")),
		},
		"user": frappe.session.user,
	}
