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
		model: DF.Link | None
		prompt_tokens: DF.Int
		reasoning: DF.LongText | None
		role: DF.Literal["System", "User", "Assistant", "Tool"]
		sequence: DF.Int
		status: DF.Literal["Draft", "Pending", "Streaming", "Completed", "Failed"]
		tool: DF.Link | None
		tool_arguments: DF.Code | None
		tool_call_id: DF.Data | None
		tool_result: DF.Code | None
		total_tokens: DF.Int
		user: DF.Link | None
	# end: auto-generated types

	def before_insert(self):
		if not self.sequence:
			last = frappe.db.sql(
				"select coalesce(max(sequence), 0) from `tabAI Message` where conversation = %s",
				(self.conversation,),
			)[0][0]
			self.sequence = cint(last) + 1

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
	if roles.intersection({"System Manager", "AI Manager"}):
		return ""
	return f"""`tabAI Message`.`conversation` in (
		select name from `tabAI Conversation`
		where `user` = {frappe.db.escape(user)} or `owner` = {frappe.db.escape(user)}
	)"""
