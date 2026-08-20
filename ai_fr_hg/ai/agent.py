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

from ai_fr_hg.ai.deadline import allows as budget_allows
from ai_fr_hg.ai.deadline import expired as budget_expired
from ai_fr_hg.ai.engine import resolve_model, run_chat
from ai_fr_hg.ai.exceptions import (
	DeadlineExceededError,
	ProviderError,
	ProviderOfflineError,
	ProviderTimeoutError,
)
from ai_fr_hg.ai.knowledge import build_context, retrieve
from ai_fr_hg.ai.logging import write_audit_log
from ai_fr_hg.ai.providers.base import ChatMessage
from ai_fr_hg.ai.settings import should_stream_completion
from ai_fr_hg.utils.db import safe_set_value

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

LANGUAGE_INSTRUCTIONS = (
	"CONTEXT marks each file with language=... A file may mix English, Arabic and Hebrew. "
	"If asked what language a file is in, list every language in that label. "
	"Do not drop Arabic or Hebrew just because English is also present. "
	"Reply in the same language the user wrote in unless they ask otherwise."
)

USER_LANGUAGE_INSTRUCTIONS = (
	"Reply in the same language the user wrote in, unless they ask for a different one. "
	"The user may write in English, Arabic, Hebrew, or a mix of those."
)

#: Conversation history window sent to the model, in messages.
HISTORY_LIMIT = 20

#: Rough cost of one more model round trip. Used to decide whether the turn can
#: afford another tool iteration, or should settle for the answer it has.
ITERATION_COST_SECONDS = 10.0

#: Time budget for retrieval. Retrieval is a nice-to-have: if the embedding
#: round trip is slow we would rather answer without context than not at all.
RETRIEVAL_BUDGET_SECONDS = 20.0

TIMED_OUT_ANSWER = (
	"I ran out of time while answering that. The local model is still loading or is "
	"responding slowly.\n\n"
	"This usually settles after the model's first run. If it keeps happening, try a "
	"smaller model, raise **Request Timeout**, or set **Max Turn Duration** to 0 in "
	"AI Platform Settings so the turn is not cut off."
)

PROVIDER_TIMEOUT_ANSWER = (
	"The AI model did not respond within the allowed time and I could not finish an "
	"answer.\n\n"
	"Local models are slowest on their first run, so try again first. If it keeps "
	"happening, pick a smaller model or raise **Request Timeout** on the provider "
	"and in AI Platform Settings."
)

PROVIDER_OFFLINE_ANSWER = (
	"The AI runtime is unreachable, so I could not answer.\n\n"
	"Check that the model server is running and that the provider's **Base URL** in "
	"AI Providers is correct, then try again."
)

PROVIDER_OOM_ANSWER = (
	"The local model could not start because this machine does not have enough memory.\n\n"
	"Ollama asked for more RAM than is free. In the Assistant model selector pick a "
	"smaller model (phi3:mini or qwen2.5:0.5b), or run `ollama stop` on the large model "
	"and try again."
)

PROVIDER_ERROR_ANSWER = (
	"The local model could not complete that request.\n\n"
	"Check that Ollama is running (`ollama list`) and that the selected model is installed. "
	"If the model is too large for this machine, switch to a smaller one."
)


def answer_for_provider_error(exc: Exception) -> str:
	"""Pick a saved explanation for a provider failure that is not a timeout."""
	text = str(exc).lower()
	if any(token in text for token in ("system memory", "not enough memory", "out of memory")):
		return PROVIDER_OOM_ANSWER
	return PROVIDER_ERROR_ANSWER


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


def build_system_prompt(
	agent_doc, context: str = "", override: str | None = None, memory: str = "", skills: str = ""
) -> str:
	"""Compose the system prompt from agent settings, grounding rules and context.

	`memory` and `skills` are the Learning Loop's contribution: approved,
	persistent knowledge and procedures that were taught and should shape how
	the agent answers. They are appended as grounded blocks so the model
	applies them when relevant without overriding the persona.
	"""
	settings = frappe.get_cached_doc("AI Platform Settings")

	base = override or agent_doc.system_prompt or settings.default_system_prompt or DEFAULT_SYSTEM_PROMPT
	parts = [base.strip(), USER_LANGUAGE_INSTRUCTIONS]

	if context:
		if agent_doc.strict_grounding:
			parts.append(GROUNDING_INSTRUCTIONS)
		if agent_doc.citation_mode and agent_doc.citation_mode != "None":
			parts.append(CITATION_INSTRUCTIONS)
		if "language=" in context:
			parts.append(LANGUAGE_INSTRUCTIONS)
		parts.append(f"CONTEXT:\n{context}")
	elif agent_doc.strict_grounding and agent_doc.use_knowledge:
		parts.append("No relevant context was retrieved. Tell the user you do not have that information.")

	if memory:
		parts.append(memory)
	if skills:
		parts.append(skills)

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


def get_agent_knowledge_base_weights(agent_doc, conversation_doc=None) -> dict[str, float]:
	"""Per-KB fusion weights from the agent child table (default 1.0)."""
	weights: dict[str, float] = {}
	for row in agent_doc.get("knowledge_bases") or []:
		if not row.knowledge_base:
			continue
		weight = flt(row.weight)
		weights[row.knowledge_base] = 1.0 if row.weight in (None, "") else weight
	# Conversation KB overrides change the target set but inherit agent weights.
	if conversation_doc and conversation_doc.get("knowledge_bases"):
		return {
			row.knowledge_base: weights.get(row.knowledge_base, 1.0)
			for row in conversation_doc.knowledge_bases
			if row.knowledge_base
		}
	return weights


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
	documents: list[str] | None = None,
	folder: str | None = None,
	on_token=None,
) -> dict:
	"""Execute one full agent turn and return the answer with its provenance.

	`documents` scopes retrieval to a specific set of `AI Document` records -
	used when a caller has just uploaded files and wants the model to answer
	from those files alone, so the reply is grounded in the new upload rather
	than the whole knowledge base.
	"""
	from ai_fr_hg.ai.tools import execute_tool, get_agent_tool_schemas

	started = time.monotonic()
	agent_doc = get_agent(agent)
	conversation_doc = frappe.get_doc("AI Conversation", conversation) if conversation else None

	model_name = model or (conversation_doc.model if conversation_doc else None) or agent_doc.model
	model_doc = resolve_model(model_name, "Chat")

	# 1. Retrieve supporting knowledge.
	retrieved = []
	context = extra_context or ""
	# Attached files are this turn's source of truth even when the agent does
	# not auto-retrieve from its knowledge bases (the seeded General Assistant
	# keeps use_knowledge off so empty-site small talk stays cheap).
	if agent_doc.use_knowledge or documents:
		targets = knowledge_bases or get_agent_knowledge_bases(agent_doc, conversation_doc)
		# A configured agent with no attached knowledge bases still returns fast
		# instead of paying an access/query round-trip on every chat.
		# Retrieval is supporting evidence, not the answer. Skip it when the
		# budget is already too tight to also pay for the generation that
		# follows, rather than spending the whole turn on context.
		if (targets or documents) and budget_allows(RETRIEVAL_BUDGET_SECONDS + ITERATION_COST_SECONDS):
			try:
				retrieved = retrieve(
					prompt,
					knowledge_bases=targets or None,
					top_k=cint(agent_doc.top_k) or None,
					documents=documents,
				)
				retrieved_context = build_context(retrieved)
				context = f"{context}\n\n{retrieved_context}".strip() if context else retrieved_context
			except Exception as exc:
				frappe.log_error(title="AI retrieval failed", message=str(exc))

	# Attached files must still reach the prompt when retrieval found nothing
	# (no keyword overlap, embeddings below threshold, or a swallowed error).
	if documents and not (context or "").strip():
		from ai_fr_hg.ai.ingestion import excerpts_for_documents

		context = excerpts_for_documents(documents)

	# 2. Assemble the message list.
	override = conversation_doc.system_prompt_override if conversation_doc else None

	# Learning Loop: recall approved knowledge/skills and let them shape the
	# turn. This is additive and best-effort - a memory failure must never
	# break an otherwise healthy chat turn.
	memory_block = skills_block = ""
	learned_context = {"memories": [], "skills": []}
	try:
		from ai_fr_hg.ai.learning import prepare_memory_context

		prepared = prepare_memory_context(prompt, agent=agent_doc.name)
		memory_block = prepared["memory_block"]
		skills_block = prepared["skill_block"]
		learned_context = {
			"memories": prepared["memories"],
			"skills": prepared["skills"],
		}
	except Exception:
		frappe.log_error(title="AI memory recall failed", message=frappe.get_traceback())

	messages = [
		ChatMessage(
			role="system",
			content=build_system_prompt(agent_doc, context, override, memory_block, skills_block),
		)
	]
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
	timed_out = False
	timeout_kind = "budget"
	provider_answer = ""

	for iteration in range(max_iterations + 1):
		# Offering tools invites another round trip to interpret their output.
		# Once the budget can no longer fund that, ask for a final answer
		# instead - a grounded reply now beats a perfect one after the proxy
		# has already hung up.
		offer_tools = (
			tools if iteration < max_iterations and budget_allows(ITERATION_COST_SECONDS * 2) else None
		)

		try:
			result = run_chat(
				messages,
				model=model_doc.name,
				options=options,
				tools=offer_tools,
				operation="Chat",
				conversation=conversation,
				on_token=on_token
				if should_stream_completion(
					requested=bool(on_token),
					enabled=True,
					offer_tools=offer_tools,
				)
				else None,
			)
		except DeadlineExceededError:
			# The whole turn ran out of its shared time budget.
			timed_out = True
			timeout_kind = "budget"
			break
		except ProviderTimeoutError:
			# The provider specifically failed to answer in time (as opposed to
			# the overall turn budget). Surface a helpful message rather than a
			# bare 417 so the thread stays coherent.
			timed_out = True
			timeout_kind = "timeout"
			break
		except ProviderOfflineError:
			timed_out = True
			timeout_kind = "offline"
			break
		except ProviderError as exc:
			# HTTP 500 from the runtime (model too large, weights missing, …)
			# must become a saved answer, not a bare 417 in the browser.
			timed_out = True
			timeout_kind = "provider"
			provider_answer = answer_for_provider_error(exc)
			break

		if not result.tool_calls:
			break

		# Tool results are only useful if we can afford the follow-up call
		# that turns them into prose.
		if budget_expired() or not budget_allows(ITERATION_COST_SECONDS):
			timed_out = not (result.content or "").strip()
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
							# Kept as a mapping: each provider adapter renders
							# it in its own wire format (Ollama needs an object,
							# OpenAI-compatible runtimes need a JSON string).
							"arguments": call["arguments"] or {},
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
	answer = (result.content if result else "") or ""

	# A blown budget (or an unresponsive runtime) is a real outcome, not a
	# dropped connection. Persist an explanation so the conversation stays
	# coherent and the user learns what to change, instead of the proxy or the
	# API returning a bare 504/417.
	if timed_out and not answer.strip():
		answer = {
			"budget": TIMED_OUT_ANSWER,
			"timeout": PROVIDER_TIMEOUT_ANSWER,
			"offline": PROVIDER_OFFLINE_ANSWER,
			"provider": provider_answer or PROVIDER_ERROR_ANSWER,
		}[timeout_kind]

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
			learned_context=frappe.as_json(learned_context)
			if learned_context["memories"] or learned_context["skills"]
			else None,
			prompt_tokens=result.prompt_tokens if result else 0,
			completion_tokens=result.completion_tokens if result else 0,
			total_tokens=result.total_tokens if result else 0,
			duration_ms=result.duration_ms if result else 0,
			status="Failed" if timed_out else "Completed",
			error_message={
				"budget": "Turn exceeded its time budget.",
				"timeout": "The model did not respond in time.",
				"offline": "The AI runtime is unreachable.",
				"provider": "The model runtime rejected the request.",
			}.get(timeout_kind)
			if timed_out
			else None,
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
		"timed_out": timed_out,
		"user_message": user_message.name if user_message else None,
		"message": assistant_message.name if assistant_message else None,
		"prompt_tokens": result.prompt_tokens if result else 0,
		"completion_tokens": result.completion_tokens if result else 0,
		"total_tokens": result.total_tokens if result else 0,
		"duration_ms": int((time.monotonic() - started) * 1000),
		"_streamed": bool(result and getattr(result, "raw", None) and result.raw.get("streamed")),
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
		# Avoid read-modify-write races when two messages land concurrently.
		frappe.db.sql(
			"""
			update `tabAI Conversation`
			set total_tokens = coalesce(total_tokens, 0) + %s,
				last_message_on = %s
			where name = %s
			""",
			(cint(result.total_tokens), values["last_message_on"], conversation),
		)

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

	safe_set_value("AI Conversation", conversation, values, update_modified=False)


def update_agent_stats(agent: str, result) -> None:
	frappe.db.sql(
		"""
		update `tabAI Agent`
		set message_count = coalesce(message_count, 0) + 1,
			total_tokens = coalesce(total_tokens, 0) + %s,
			last_used_on = %s
		where name = %s
		""",
		(cint(result.total_tokens) if result else 0, now_datetime(), agent),
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

	frappe.db.sql(
		"update `tabAI Agent` set conversation_count = conversation_count + 1 where name = %s",
		(agent_doc.name,),
	)

	if agent_doc.greeting:
		save_message(conversation.name, role="Assistant", content=agent_doc.greeting, agent=agent_doc.name)

	write_audit_log(
		action="Conversation Created",
		category="Access",
		details={"agent": agent_doc.name, "knowledge_bases": knowledge_bases or []},
		reference_doctype="AI Conversation",
		reference_name=conversation.name,
		raise_on_error=True,
	)
	return conversation
