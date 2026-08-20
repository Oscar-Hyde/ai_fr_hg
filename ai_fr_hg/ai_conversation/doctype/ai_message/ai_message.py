# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint


class AIMessage(Document):
	_DOCTYPE_NAME = "AI Message"

	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		agent: DF.Link | None
		citations: DF.Code | None
		completion_tokens: DF.Int
		content: DF.LongText | None
		context_used: DF.LongText | None
		conversation: DF.Link
		duration_ms: DF.Int
		error_message: DF.SmallText | None
		execution_log: DF.Link | None
		feedback: DF.Literal["", "Positive", "Negative"]
		feedback_comment: DF.SmallText | None
		feedback_reason: DF.Literal["", "Correction", "Missing Information", "Incorrect Information"]
		learned_context: DF.Code | None
		model: DF.Link | None
		prompt_tokens: DF.Int
		reasoning: DF.LongText | None
		role: DF.Literal["System", "User", "Assistant", "Tool"]
		sequence: DF.Int
		status: DF.Literal["Draft", "Pending", "Streaming", "Completed", "Failed", "Cancelled"]
		turn_id: DF.Data | None
		tool: DF.Link | None
		tool_arguments: DF.Code | None
		tool_call_id: DF.Data | None
		tool_result: DF.Code | None
		total_tokens: DF.Int
		user: DF.Link | None
	# end: auto-generated types

	def before_insert(self):
		if self.sequence and getattr(self.flags, "sequence_allocated", False):
			return
		if not self.sequence:
			from ai_fr_hg.ai.conversation import allocate_sequence

			self.sequence = allocate_sequence(self.conversation)

	def after_insert(self):
		frappe.publish_realtime(
			"ai_message",
			{
				"conversation": self.conversation,
				"message": self.name,
				"role": self.role,
				"content": self.content,
			},
			doctype="AI Conversation",
			docname=self.conversation,
		)


def get_permission_query_conditions(user: str | None = None) -> str:
	"""Messages inherit their conversation's visibility."""
	user = user or frappe.session.user
	roles = set(frappe.get_roles(user))
	if user == "Administrator" or roles.intersection({"System Manager", "AI Manager"}):
		return ""
	return f"""`tabAI Message`.`conversation` in (
		select name from `tabAI Conversation`
		where `user` = {frappe.db.escape(user)} or `owner` = {frappe.db.escape(user)}
	)"""


def has_permission(doc, ptype: str | None = None, user: str | None = None) -> bool:
	"""Apply conversation ownership to direct message reads and writes too."""
	user = user or frappe.session.user
	roles = set(frappe.get_roles(user))
	if user == "Administrator" or roles.intersection({"System Manager", "AI Manager"}):
		return True
	if not doc.conversation:
		return False
	conversation = frappe.db.get_value(
		"AI Conversation",
		doc.conversation,
		["user", "owner"],
		as_dict=True,
	)
	return bool(conversation and user in {conversation.user, conversation.owner})
