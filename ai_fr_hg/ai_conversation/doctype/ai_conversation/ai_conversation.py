# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class AIConversation(Document):
	_DOCTYPE_NAME = "AI Conversation"

	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		from ai_fr_hg.ai_conversation.doctype.ai_agent_knowledge_base.ai_agent_knowledge_base import (
			AIAgentKnowledgeBase,
		)

		agent: DF.Link | None
		context_document: DF.Link | None
		knowledge_bases: DF.Table[AIAgentKnowledgeBase]
		last_message_on: DF.Datetime | None
		message_count: DF.Int
		model: DF.Link | None
		naming_series: DF.Literal["AICONV-.YYYY.-"]
		pinned: DF.Check
		status: DF.Literal["Active", "Archived", "Failed"]
		summary: DF.LongText | None
		system_prompt_override: DF.Code | None
		title: DF.Data | None
		total_tokens: DF.Int
		user: DF.Link | None
	# end: auto-generated types

	def before_insert(self):
		if not self.user:
			self.user = frappe.session.user

	def on_trash(self):
		frappe.db.delete("AI Message", {"conversation": self.name})

	@frappe.whitelist()
	def send(self, message: str) -> dict:
		"""Send a message in this conversation."""
		from ai_fr_hg.ai.agent import run_agent_turn

		return run_agent_turn(message, agent=self.agent, conversation=self.name)

	@frappe.whitelist()
	def generate_summary(self) -> dict:
		"""Summarise this conversation."""
		from ai_fr_hg.api.chat import summarize_conversation

		return summarize_conversation(self.name)


def get_permission_query_conditions(user: str | None = None) -> str:
	"""Users see only their own conversations unless they manage the platform."""
	user = user or frappe.session.user
	roles = set(frappe.get_roles(user))
	if roles.intersection({"System Manager", "AI Manager"}):
		return ""
	return f"""(`tabAI Conversation`.`user` = {frappe.db.escape(user)}
		or `tabAI Conversation`.`owner` = {frappe.db.escape(user)})"""


def has_permission(doc, ptype: str | None = None, user: str | None = None) -> bool:
	"""Row-level check matching the query condition above."""
	user = user or frappe.session.user
	if set(frappe.get_roles(user)).intersection({"System Manager", "AI Manager"}):
		return True
	return doc.user == user or doc.owner == user
