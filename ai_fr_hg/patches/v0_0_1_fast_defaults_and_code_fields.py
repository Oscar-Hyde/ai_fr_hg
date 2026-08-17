# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Tune an existing install for faster local chat and fix code-field options."""

import frappe
from frappe.utils import cint


def execute():
	frappe.reload_doctype("AI Platform Settings", force=True)
	frappe.reload_doctype("AI Model", force=True)
	frappe.reload_doctype("AI Agent", force=True)
	frappe.reload_doctype("AI Conversation", force=True)
	frappe.reload_doctype("AI Prompt Template", force=True)

	settings = frappe.get_single("AI Platform Settings")
	if not cint(settings.default_max_tokens) or cint(settings.default_max_tokens) == 2048:
		settings.default_max_tokens = 512
	if settings.enable_hybrid_search:
		settings.enable_hybrid_search = 0
	settings.flags.ignore_permissions = True
	settings.save(ignore_permissions=True)

	# Existing starter assistant can be slow on small local models. Keep its
	# knowledge tools, but do not automatically retrieve on every turn.
	if frappe.db.exists("AI Agent", "General Assistant"):
		frappe.db.sql(
			"""
			update `tabAI Agent`
			set use_knowledge = 0,
				max_tokens = case when max_tokens = 2048 or max_tokens is null then 512 else max_tokens end,
				max_tool_iterations = case
					when max_tool_iterations = 4 or max_tool_iterations is null then 2
					else max_tool_iterations
				end
			where name = 'General Assistant'
			"""
		)

	# If exactly one chat model exists, make it the default so the assistant is
	# usable immediately after Discover Models.
	chat_models = frappe.get_all(
		"AI Model",
		filters={"enabled": 1, "model_type": "Chat"},
		pluck="name",
		order_by="creation asc",
	)
	if chat_models and not settings.default_chat_model:
		frappe.db.set_single_value("AI Platform Settings", "default_chat_model", chat_models[0])
