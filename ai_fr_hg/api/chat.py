# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Whitelisted chat and conversation endpoints."""

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
) -> dict:
	"""Send a message and return the assistant's reply with its citations.

	`documents` lists `AI Document` records the user has just uploaded. The
	turn waits for them to finish indexing (bounded by the turn budget) and
	then grounds retrieval on those files alone, so asking "summarise the file
	I just uploaded" answers from the new upload instead of missing it.
	"""
	from ai_fr_hg.ai.agent import create_conversation, run_agent_turn
	from ai_fr_hg.ai.deadline import turn_budget
	from ai_fr_hg.ai.ingestion import wait_for_indexed

	if not (message or "").strip():
		frappe.throw(_("Message cannot be empty."))

	if isinstance(knowledge_bases, str):
		try:
			knowledge_bases = json.loads(knowledge_bases)
		except ValueError:
			knowledge_bases = [knowledge_bases]

	documents = _coerce_documents(documents)

	if not conversation:
		conversation_doc = create_conversation(agent=agent, knowledge_bases=knowledge_bases)
		conversation = conversation_doc.name
	else:
		frappe.get_doc("AI Conversation", conversation).check_permission("write")

	# Interactive requests sit behind a reverse proxy that will hang up long
	# before the retry/failover bounds are exhausted. Cap the turn so the user
	# gets a saved, explainable answer instead of a 504.
	with turn_budget(_get_turn_budget()):
		if documents:
			wait_for_indexed(documents)
		return run_agent_turn(
			message,
			agent=agent,
			conversation=conversation,
			knowledge_bases=knowledge_bases,
			model=model,
			documents=documents,
		)


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
	configured = frappe.db.get_single_value("AI Platform Settings", "max_turn_seconds")
	# `None` means the column predates this setting (site not yet migrated);
	# fall back to the default rather than silently running unbudgeted.
	return 90 if configured is None else cint(configured)


@frappe.whitelist()
def start_conversation(
	agent: str | None = None,
	title: str | None = None,
	knowledge_bases: str | list | None = None,
) -> dict:
	"""Create a new conversation."""
	from ai_fr_hg.ai.agent import create_conversation

	if isinstance(knowledge_bases, str):
		try:
			knowledge_bases = json.loads(knowledge_bases)
		except ValueError:
			knowledge_bases = [knowledge_bases]

	doc = create_conversation(agent=agent, title=title, knowledge_bases=knowledge_bases)
	return {"conversation": doc.name, "title": doc.title, "agent": doc.agent}


@frappe.whitelist()
def get_conversation(conversation: str) -> dict:
	"""Return a conversation with its full message history."""
	doc = frappe.get_doc("AI Conversation", conversation)
	doc.check_permission("read")

	messages = frappe.get_all(
		"AI Message",
		filters={"conversation": conversation},
		fields=[
			"name",
			"role",
			"content",
			"reasoning",
			"citations",
			"sequence",
			"creation",
			"model",
			"tool",
			"tool_arguments",
			"tool_result",
			"total_tokens",
			"duration_ms",
			"feedback",
			"status",
			"error_message",
		],
		order_by="sequence asc, creation asc",
		limit_page_length=0,
	)
	for message in messages:
		if message.citations:
			try:
				message.citations = json.loads(message.citations)
			except ValueError:
				message.citations = []

	return {
		"conversation": doc.as_dict(),
		"messages": messages,
	}


@frappe.whitelist()
def list_conversations(limit: int = 50, include_archived: bool = False) -> list:
	"""List the current user's conversations, most recent first."""
	filters = {"user": frappe.session.user}
	if not include_archived:
		filters["status"] = "Active"

	return frappe.get_list(
		"AI Conversation",
		filters=filters,
		fields=["name", "title", "agent", "message_count", "last_message_on", "pinned", "status"],
		order_by="pinned desc, last_message_on desc, creation desc",
		limit_page_length=cint(limit) or 50,
	)


@frappe.whitelist()
def delete_conversation(conversation: str) -> dict:
	"""Delete a conversation and every message in it."""
	doc = frappe.get_doc("AI Conversation", conversation)
	doc.check_permission("delete")

	frappe.db.delete("AI Message", {"conversation": conversation})
	frappe.delete_doc("AI Conversation", conversation)
	return {"status": "deleted", "conversation": conversation}


@frappe.whitelist()
def archive_conversation(conversation: str) -> dict:
	"""Archive a conversation without deleting it."""
	doc = frappe.get_doc("AI Conversation", conversation)
	doc.check_permission("write")
	doc.db_set("status", "Archived")
	return {"status": "archived", "conversation": conversation}


@frappe.whitelist()
def rename_conversation(conversation: str, title: str) -> dict:
	"""Rename a conversation."""
	doc = frappe.get_doc("AI Conversation", conversation)
	doc.check_permission("write")
	doc.db_set("title", title[:140])
	return {"status": "renamed", "title": title}


@frappe.whitelist()
def submit_feedback(message: str, feedback: str) -> dict:
	"""Record thumbs up/down on an assistant message.

	A ``Negative`` rating feeds the Learning Loop: the answer is captured as a
	knowledge candidate for review, so a human can turn a recurring mistake
	into a learned correction.
	"""
	if feedback not in ("Positive", "Negative", ""):
		frappe.throw(_("Feedback must be Positive or Negative."))

	doc = frappe.get_doc("AI Message", message)
	doc.check_permission("write")
	doc.db_set("feedback", feedback)

	result = {"status": "recorded", "feedback": feedback}
	if feedback:
		try:
			from ai_fr_hg.ai.learning import observe_feedback

			result.update(observe_feedback(message, feedback))
		except Exception:
			# Feedback recording must never break the user's action.
			frappe.log_error(title="AI feedback loop failed", message=frappe.get_traceback())
	return result


@frappe.whitelist()
def summarize_conversation(conversation: str) -> dict:
	"""Generate and store a summary of a conversation."""
	from ai_fr_hg.ai.intelligence import summarize

	doc = frappe.get_doc("AI Conversation", conversation)
	doc.check_permission("write")

	messages = frappe.get_all(
		"AI Message",
		filters={"conversation": conversation, "role": ["in", ["User", "Assistant"]]},
		fields=["role", "content"],
		order_by="sequence asc",
		limit_page_length=0,
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
	)

	return {
		"agents": agents,
		"models": models,
		"knowledge_bases": frappe.get_all(
			"AI Knowledge Base",
			filters={"name": ["in", accessible or [""]], "enabled": 1},
			fields=["name", "knowledge_base_name", "document_count", "chunk_count"],
			order_by="knowledge_base_name asc",
		),
		"settings": {
			"streaming_enabled": frappe.db.get_single_value("AI Platform Settings", "streaming_enabled"),
			"platform_enabled": frappe.db.get_single_value("AI Platform Settings", "platform_enabled"),
			"default_chat_model": frappe.db.get_single_value("AI Platform Settings", "default_chat_model"),
		},
		"user": frappe.session.user,
	}
