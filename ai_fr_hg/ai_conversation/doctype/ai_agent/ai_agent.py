# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, flt


class AIAgent(Document):
	_DOCTYPE_NAME = "AI Agent"

	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		from ai_fr_hg.ai_conversation.doctype.ai_agent_knowledge_base.ai_agent_knowledge_base import (
			AIAgentKnowledgeBase,
		)
		from ai_fr_hg.ai_conversation.doctype.ai_agent_role.ai_agent_role import AIAgentRole
		from ai_fr_hg.ai_conversation.doctype.ai_agent_tool.ai_agent_tool import AIAgentTool

		agent_name: DF.Data
		allowed_roles: DF.Table[AIAgentRole]
		citation_mode: DF.Literal["None", "Inline", "Footnote"]
		conversation_count: DF.Int
		description: DF.SmallText | None
		enabled: DF.Check
		fallback_answer: DF.SmallText | None
		greeting: DF.SmallText | None
		is_default: DF.Check
		knowledge_bases: DF.Table[AIAgentKnowledgeBase]
		last_used_on: DF.Datetime | None
		max_tokens: DF.Int
		max_tool_iterations: DF.Int
		message_count: DF.Int
		model: DF.Link | None
		response_format: DF.Literal["Text", "Markdown", "JSON"]
		strict_grounding: DF.Check
		system_prompt: DF.Code | None
		temperature: DF.Float
		tools: DF.Table[AIAgentTool]
		top_k: DF.Int
		total_tokens: DF.Float
		use_knowledge: DF.Check
		use_tools: DF.Check
	# end: auto-generated types

	def validate(self):
		self.validate_model()
		self.validate_default()
		self.validate_knowledge()
		self.validate_generation()

	def validate_model(self):
		if not self.model:
			return
		model_type = frappe.db.get_value("AI Model", self.model, "model_type")
		if model_type not in ("Chat", "Vision"):
			frappe.throw(_("{0} is a {1} model and cannot drive an agent.").format(self.model, model_type))

	def validate_default(self):
		if not self.is_default:
			return
		frappe.db.sql(
			"update `tabAI Agent` set is_default = 0 where is_default = 1 and name != %s",
			(self.name,),
		)

	def validate_knowledge(self):
		if self.use_knowledge and not self.knowledge_bases:
			frappe.msgprint(
				_(
					"Knowledge retrieval is on but no knowledge base is selected, so every accessible base will be searched."
				),
				indicator="blue",
				alert=True,
			)
		if self.strict_grounding and not self.use_knowledge:
			frappe.throw(_("Answer Only From Knowledge requires Use Knowledge Retrieval to be enabled."))

		seen = set()
		for row in self.knowledge_bases:
			if row.knowledge_base in seen:
				frappe.throw(_("Knowledge base {0} is listed more than once.").format(row.knowledge_base))
			seen.add(row.knowledge_base)

	def validate_generation(self):
		if not 0 <= flt(self.temperature) <= 2:
			frappe.throw(_("Temperature must be between 0 and 2."))
		if cint(self.max_tool_iterations) > 10:
			frappe.throw(_("Max Tool Iterations cannot exceed 10."))
		if self.use_tools and not self.tools:
			frappe.throw(_("Enable Tool Calling is on but no tools are selected."))

	@frappe.whitelist()
	def start_conversation(self) -> str:
		"""Open a new conversation with this agent."""
		from ai_fr_hg.ai.agent import create_conversation

		return create_conversation(agent=self.name).name

	@frappe.whitelist()
	def test_agent(self, prompt: str = "Introduce yourself in one sentence.") -> dict:
		"""Run a single turn against this agent without saving a conversation."""
		from ai_fr_hg.ai.agent import run_agent_turn

		return run_agent_turn(prompt, agent=self.name, save_messages=False, include_history=False)
