# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Agent runtime: retrieval-augmented chat with tool calling.

`run_agent_turn` is the single entry point used by the chat interface, the API
and the automation layer. It assembles the prompt from the agent definition,
retrieved knowledge and conversation history, then loops over tool calls until
the model produces a final answer or the iteration budget is spent.
"""

import json
import time

import frappe
from frappe import _
from frappe.utils import cint, flt, now_datetime

from ai_fr_hg.ai.engine import resolve_model, run_chat
from ai_fr_hg.ai.knowledge import build_context, retrieve
from ai_fr_hg.ai.logging import write_audit_log
from ai_fr_hg.ai.providers.base import ChatMessage

DEFAULT_SYSTEM_PROMPT = (
	"You are a helpful enterprise AI assistant running entirely on local infrastructure. "
	"Answer accurately and concisely. If you are unsure, say so plainly rather than guessing."
)

GROUNDING_INSTRUCTIONS = (
	"Answer using ONLY the information in the CONTEXT block below. "
	"If the context does not contain the answer, say that you do not have that information. "
	"Never invent facts, figures or sources."
)

CITATION_INSTRUCTIONS = (
	"Cite the context passages you rely on using their bracketed numbers, for example [1] or [2]. "
	"Place each citation immediately after the statement it supports."
)

#: Conversation history window sent to the model, in messages.
HISTORY_LIMIT = 20


def get_agent(agent: str | None = None):
	"""Resolve an agent by name, or fall back to the configured default."""
	if agent:
		doc = frappe.get_cached_doc("AI Agent", agent)
		if not doc.enabled:
			frappe.throw(_("AI Agent {0} is disabled.").format(agent))
		check_agent_access(doc)
		return doc

	settings = frappe.get_cached_doc("AI Platform Settings")
	if settings.default_agent:
		doc = frappe.get_cached_doc("AI Agent", settings.default_agent)
		if doc.enabled:
			check_agent_access(doc)
			return doc

	candidates = frappe.get_all(
		"AI Agent",
		filters={"enabled": 1},
		fields=["name"],
		order_by="is_default desc, creation asc",
		limit=1,
	)
	if not candidates:
		frappe.throw(
			_("No AI Agent is configured. Create one in the AI Administration workspace."),
			title=_("No Agent"),
		)
	doc = frappe.get_cached_doc("AI Agent", candidates[0].name)
	check_agent_access(doc)
	return doc


def check_agent_access(agent_doc) -> None:
	"""Raise when the session user is not permitted to use this agent."""
	allowed = [row.role for row in agent_doc.get("allowed_roles") or []]
	if not allowed:
		return
	if frappe.session.user == "Administrator":
		return
	if not set(frappe.get_roles()).intersection(allowed):
		frappe.throw(
			_("You are not permitted to use the agent {0}.").format(agent_doc.name),
			frappe.PermissionError,
		)


def build_system_prompt(agent_doc, context: str = "", override: str | None = None) -> str:
	"""Compose the system prompt from agent settings, grounding rules and context."""
	settings = frappe.get_cached_doc("AI Platform Settings")

	base = override or agent_doc.system_prompt or settings.default_system_prompt or DEFAULT_SYSTEM_PROMPT
	parts = [base.strip()]

	if context:
		if agent_doc.strict_grounding:
			parts.append(GROUNDING_INSTRUCTIONS)
		if agent_doc.citation_mode and agent_doc.citation_mode != "None":
			parts.append(CITATION_INSTRUCTIONS)
		parts.append(f"CONTEXT:\n{context}")
	elif agent_doc.strict_grounding and agent_doc.use_knowledge:
		parts.append("No relevant context was retrieved. Tell the user you do not have that information.")

	if agent_doc.response_format == "Markdown":
		parts.append("Format your response using Markdown.")
	elif agent_doc.response_format == "JSON":
		parts.append("Respond with a single valid JSON object and nothing else.")

	return "\n\n".join(part for part in parts if part)


def get_agent_knowledge_bases(agent_doc, conversation_doc=None) -> list[str]:
	"""Knowledge bases in play for this turn, conversation overriding the agent."""
	if conversation_doc and conversation_doc.get("knowledge_bases"):
		return [row.knowledge_base for row in conversation_doc.knowledge_bases]
	return [row.knowledge_base for row in agent_doc.get("knowledge_bases") or []]


def get_conversation_history(conversation: str, limit: int = HISTORY_LIMIT) -> list[ChatMessage]:
	"""Load the recent turns of a conversation as chat messages."""
	rows = frappe.get_all(
		"AI Message",
		filters={"conversation": conversation, "status": ["in", ["Completed", "Draft"]]},
		fields=["role", "content", "tool_call_id", "tool_arguments", "tool_result", "tool"],
		order_by="sequence asc, creation asc",
		limit_page_length=limit,
	)

	messages = []
	for row in rows:
		role = (row.role or "user").lower()
		if role == "system":
			continue  # the system prompt is rebuilt fresh each turn
		if role == "tool":
			messages.append(
				ChatMessage(
					role="tool",
					content=row.tool_result or "",
					name=row.tool,
					tool_call_id=row.tool_call_id,
				)
			)
		else:
			messages.append(ChatMessage(role=role, content=row.content or ""))
	return messages


def run_agent_turn(
	prompt: str,
	agent: str | None = None,
	conversation: str | None = None,
	knowledge_bases: list[str] | None = None,
	model: str | None = None,
	include_history: bool = True,
	save_messages: bool = True,
	extra_context: str | None = None,
) -> dict:
	"""Execute one full agent turn and return the answer with its provenance."""
	from ai_fr_hg.ai.tools import execute_tool, get_agent_tool_schemas

	started = time.monotonic()
	agent_doc = get_agent(agent)
	conversation_doc = frappe.get_doc("AI Conversation", conversation) if conversation else None

	model_name = model or (conversation_doc.model if conversation_doc else None) or agent_doc.model
	model_doc = resolve_model(model_name, "Chat")

	# 1. Retrieve supporting knowledge.
	retrieved = []
	context = extra_context or ""
	if agent_doc.use_knowledge:
		targets = knowledge_bases or get_agent_knowledge_bases(agent_doc, conversation_doc)
		try:
			retrieved = retrieve(
				prompt,
				knowledge_bases=targets or None,
				top_k=cint(agent_doc.top_k) or None,
			)
			retrieved_context = build_context(retrieved)
			context = f"{context}\n\n{retrieved_context}".strip() if context else retrieved_context
		except Exception as exc:
			frappe.log_error(title="AI retrieval failed", message=str(exc))

	# 2. Assemble the message list.
	override = conversation_doc.system_prompt_override if conversation_doc else None
	messages = [ChatMessage(role="system", content=build_system_prompt(agent_doc, context, override))]
	if include_history and conversation:
		messages.extend(get_conversation_history(conversation))
	messages.append(ChatMessage(role="user", content=prompt))

	# 3. Persist the user's message.
	user_message = None
	if save_messages and conversation:
		user_message = save_message(
			conversation, role="User", content=prompt, agent=agent_doc.name, model=model_doc.name
		)

	# 4. Run the model, resolving tool calls iteratively.
	tools = get_agent_tool_schemas(agent_doc) if agent_doc.use_tools else None
	max_iterations = cint(agent_doc.max_tool_iterations) or 4
	options = {"temperature": flt(agent_doc.temperature), "max_tokens": cint(agent_doc.max_tokens)}

	tool_invocations: list[dict] = []
	result = None

	for iteration in range(max_iterations + 1):
		result = run_chat(
			messages,
			model=model_doc.name,
			options=options,
			tools=tools if iteration < max_iterations else None,
			operation="Chat",
			conversation=conversation,
		)

		if not result.tool_calls:
			break

		messages.append(
			ChatMessage(
				role="assistant",
				content=result.content or "",
				tool_calls=[
					{
						"id": call["id"],
						"type": "function",
						"function": {
							"name": call["name"],
							"arguments": json.dumps(call["arguments"]),
						},
					}
					for call in result.tool_calls
				],
			)
		)

		for call in result.tool_calls:
			outcome = execute_tool(
				call["name"],
				call["arguments"],
				conversation=conversation,
				agent=agent_doc.name,
			)
			tool_invocations.append({"tool": call["name"], "arguments": call["arguments"], **outcome})
			messages.append(
				ChatMessage(
					role="tool",
					content=json.dumps(outcome.get("result"), default=str)[:8000],
					name=call["name"],
					tool_call_id=call["id"],
				)
			)
			if save_messages and conversation:
				save_message(
					conversation,
					role="Tool",
					content="",
					tool=call["name"] if frappe.db.exists("AI Tool", call["name"]) else None,
					tool_call_id=call["id"],
					tool_arguments=frappe.as_json(call["arguments"]),
					tool_result=frappe.as_json(outcome.get("result")),
					agent=agent_doc.name,
				)

	citations = [r.as_dict() for r in retrieved]
	answer = result.content if result else ""

	# 5. Persist the assistant's reply.
	assistant_message = None
	if save_messages and conversation:
		assistant_message = save_message(
			conversation,
			role="Assistant",
			content=answer,
			reasoning=result.reasoning if result else None,
			model=model_doc.name,
			agent=agent_doc.name,
			citations=frappe.as_json(citations) if citations else None,
			context_used=context or None,
			prompt_tokens=result.prompt_tokens if result else 0,
			completion_tokens=result.completion_tokens if result else 0,
			total_tokens=result.total_tokens if result else 0,
			duration_ms=result.duration_ms if result else 0,
		)
		update_conversation_stats(conversation, result)

	update_agent_stats(agent_doc.name, result)

	return {
		"answer": answer,
		"reasoning": result.reasoning if result else "",
		"conversation": conversation,
		"agent": agent_doc.name,
		"model": model_doc.name,
		"citations": citations,
		"tool_invocations": tool_invocations,
		"user_message": user_message.name if user_message else None,
		"message": assistant_message.name if assistant_message else None,
		"prompt_tokens": result.prompt_tokens if result else 0,
		"completion_tokens": result.completion_tokens if result else 0,
		"total_tokens": result.total_tokens if result else 0,
		"duration_ms": int((time.monotonic() - started) * 1000),
	}


def save_message(conversation: str, role: str, content: str, **kwargs):
	"""Append a message to a conversation with the next sequence number."""
	sequence = (
		frappe.db.sql(
			"select coalesce(max(sequence), 0) from `tabAI Message` where conversation = %s",
			(conversation,),
		)[0][0]
		or 0
	)

	message = frappe.new_doc("AI Message")
	message.update(
		{
			"conversation": conversation,
			"role": role,
			"content": content,
			"sequence": cint(sequence) + 1,
			"status": "Completed",
			"user": frappe.session.user,
			**{k: v for k, v in kwargs.items() if v is not None},
		}
	)
	message.flags.ignore_permissions = True
	message.insert(ignore_permissions=True)
	return message


def update_conversation_stats(conversation: str, result) -> None:
	"""Refresh the counters and title on the conversation record."""
	values = {
		"message_count": frappe.db.count("AI Message", {"conversation": conversation}),
		"last_message_on": now_datetime(),
	}

	if result:
		current = cint(frappe.db.get_value("AI Conversation", conversation, "total_tokens"))
		values["total_tokens"] = current + cint(result.total_tokens)

	# Title the conversation after its first question, so the list is scannable.
	if not frappe.db.get_value("AI Conversation", conversation, "title"):
		first = frappe.get_all(
			"AI Message",
			filters={"conversation": conversation, "role": "User"},
			fields=["content"],
			order_by="sequence asc",
			limit=1,
		)
		if first and first[0].content:
			values["title"] = first[0].content.strip().split("\n")[0][:120]

	frappe.db.set_value("AI Conversation", conversation, values, update_modified=False)


def update_agent_stats(agent: str, result) -> None:
	row = frappe.db.get_value("AI Agent", agent, ["message_count", "total_tokens"], as_dict=True)
	if not row:
		return
	frappe.db.set_value(
		"AI Agent",
		agent,
		{
			"message_count": cint(row.message_count) + 1,
			"total_tokens": cint(row.total_tokens) + (cint(result.total_tokens) if result else 0),
			"last_used_on": now_datetime(),
		},
		update_modified=False,
	)


def create_conversation(
	agent: str | None = None, title: str | None = None, knowledge_bases: list[str] | None = None
):
	"""Start a new conversation bound to an agent."""
	agent_doc = get_agent(agent)

	conversation = frappe.new_doc("AI Conversation")
	conversation.update(
		{
			"title": title,
			"agent": agent_doc.name,
			"user": frappe.session.user,
			"status": "Active",
			"model": agent_doc.model,
		}
	)
	for kb in knowledge_bases or []:
		conversation.append("knowledge_bases", {"knowledge_base": kb})
	conversation.insert()

	frappe.db.set_value(
		"AI Agent",
		agent_doc.name,
		"conversation_count",
		cint(frappe.db.count("AI Conversation", {"agent": agent_doc.name})),
		update_modified=False,
	)

	if agent_doc.greeting:
		save_message(conversation.name, role="Assistant", content=agent_doc.greeting, agent=agent_doc.name)

	write_audit_log(
		action="Conversation Created",
		category="Access",
		reference_doctype="AI Conversation",
		reference_name=conversation.name,
	)
	return conversation
