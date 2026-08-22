# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Installation, seeding and first-run setup.

`after_install` makes the platform usable immediately: roles, a default local
Ollama provider, a starter knowledge base, a general-purpose agent, the
built-in tools and sensible platform defaults.
"""

from pathlib import Path

import frappe
from frappe import _

ROLES = [
	{
		"role_name": "AI Manager",
		"desk_access": 1,
		"description": "Full control of the AI platform: providers, models, agents, policies.",
	},
	{
		"role_name": "AI User",
		"desk_access": 1,
		"description": "Can chat, upload documents and search the knowledge base.",
	},
	{
		"role_name": "AI Auditor",
		"desk_access": 1,
		"description": "Read-only access to logs, audit trails and usage reporting.",
	},
]

BUILTIN_TOOLS = [
	{
		"tool_name": "search_knowledge_base",
		"tool_type": "Builtin",
		"handler": "search_knowledge_base",
		"is_readonly_tool": 1,
		"description": (
			"Search the local knowledge base for passages relevant to a question. "
			"Use this whenever the answer may live in an uploaded document."
		),
		"parameters": [
			{
				"parameter": "query",
				"parameter_type": "String",
				"required": 1,
				"description": "The search query, phrased as a natural language question.",
			},
			{
				"parameter": "knowledge_base",
				"parameter_type": "String",
				"description": "Optional: restrict the search to one knowledge base.",
			},
			{
				"parameter": "limit",
				"parameter_type": "Integer",
				"description": "Maximum number of passages to return. Default 5.",
			},
		],
	},
	{
		"tool_name": "get_document_text",
		"tool_type": "Builtin",
		"handler": "get_document_text",
		"is_readonly_tool": 1,
		"description": (
			"Read the full extracted text of an uploaded AI Document by its name, title, or filename. "
			"Use this to answer questions about or summarise a specific uploaded document."
		),
		"parameters": [
			{
				"parameter": "document",
				"parameter_type": "String",
				"required": 1,
				"description": (
					"The document ID, title, filename, or file path "
					"(for example 'Maintenance_Study_Electronics_Electrical_Equipment.docx' or 'AIDOC-2026-00001')."
				),
			},
			{
				"parameter": "max_characters",
				"parameter_type": "Integer",
				"description": "Maximum characters to return. Default 8000.",
			},
		],
	},
	{
		"tool_name": "translate_content",
		"tool_type": "Builtin",
		"handler": "translate_content",
		"is_readonly_tool": 1,
		"description": (
			"Translate an uploaded document, or a passage of text, between Arabic, English and Hebrew. "
			"Use this whenever the user asks for content in another one of those languages."
		),
		"parameters": [
			{
				"parameter": "target_language",
				"parameter_type": "String",
				"required": 1,
				"description": "The language to translate into: 'ar' (Arabic), 'en' (English) or 'he' (Hebrew).",
			},
			{
				"parameter": "document",
				"parameter_type": "String",
				"description": "The document ID, title or filename to translate. Omit when passing 'text'.",
			},
			{
				"parameter": "text",
				"parameter_type": "String",
				"description": "A passage to translate directly, instead of a stored document.",
			},
			{
				"parameter": "source_language",
				"parameter_type": "String",
				"description": "Optional source language ('ar', 'en' or 'he'). Detected automatically when omitted.",
			},
		],
	},
	{
		"tool_name": "list_documents",
		"tool_type": "Builtin",
		"handler": "list_documents",
		"is_readonly_tool": 1,
		"description": (
			"List records of any Frappe DocType the user may read. "
			"Use this to answer questions about business data."
		),
		"parameters": [
			{
				"parameter": "doctype",
				"parameter_type": "String",
				"required": 1,
				"description": "The DocType to list, for example 'Sales Invoice'.",
			},
			{
				"parameter": "filters",
				"parameter_type": "Object",
				"description": 'Filters as a JSON object, for example {"status": "Open"}.',
			},
			{
				"parameter": "fields",
				"parameter_type": "Array",
				"description": "Fieldnames to return.",
			},
			{
				"parameter": "limit",
				"parameter_type": "Integer",
				"description": "Maximum records to return. Default 20, maximum 100.",
			},
		],
	},
	{
		"tool_name": "count_documents",
		"tool_type": "Builtin",
		"handler": "count_documents",
		"is_readonly_tool": 1,
		"description": "Count records of a DocType matching optional filters.",
		"parameters": [
			{
				"parameter": "doctype",
				"parameter_type": "String",
				"required": 1,
				"description": "The DocType to count.",
			},
			{"parameter": "filters", "parameter_type": "Object", "description": "Filters as a JSON object."},
		],
	},
	{
		"tool_name": "get_document",
		"tool_type": "Builtin",
		"handler": "get_document",
		"is_readonly_tool": 1,
		"description": "Fetch a single Frappe record by DocType and name.",
		"parameters": [
			{
				"parameter": "doctype",
				"parameter_type": "String",
				"required": 1,
				"description": "The DocType of the record.",
			},
			{
				"parameter": "name",
				"parameter_type": "String",
				"required": 1,
				"description": "The record's ID.",
			},
			{
				"parameter": "fields",
				"parameter_type": "Array",
				"description": "Optional list of fields to return.",
			},
		],
	},
	{
		"tool_name": "current_datetime",
		"tool_type": "Builtin",
		"handler": "current_datetime",
		"is_readonly_tool": 1,
		"description": "Get the current date and time on this system. Use this for any 'today' question.",
		"parameters": [],
	},
]

EXTRACTION_SCHEMAS = [
	{
		"schema_name": "Invoice Data",
		"description": "Common fields found on a supplier invoice.",
		"instructions": "Extract invoice details. Use ISO dates. Amounts must be plain numbers.",
		"extraction_fields": [
			{
				"field_name": "invoice_number",
				"label": "Invoice Number",
				"field_type": "String",
				"required": 1,
			},
			{"field_name": "invoice_date", "label": "Invoice Date", "field_type": "Date"},
			{"field_name": "due_date", "label": "Due Date", "field_type": "Date"},
			{"field_name": "supplier_name", "label": "Supplier", "field_type": "String"},
			{"field_name": "total_amount", "label": "Total", "field_type": "Number"},
			{"field_name": "tax_amount", "label": "Tax", "field_type": "Number"},
			{"field_name": "currency", "label": "Currency", "field_type": "String"},
			{"field_name": "line_items", "label": "Line Items", "field_type": "Array"},
		],
	},
	{
		"schema_name": "Contract Summary",
		"description": "Key commercial terms of a contract.",
		"instructions": "Extract the contract's parties, dates and commercial terms.",
		"extraction_fields": [
			{"field_name": "contract_title", "label": "Title", "field_type": "String", "required": 1},
			{"field_name": "parties", "label": "Parties", "field_type": "Array"},
			{"field_name": "effective_date", "label": "Effective Date", "field_type": "Date"},
			{"field_name": "expiry_date", "label": "Expiry Date", "field_type": "Date"},
			{"field_name": "contract_value", "label": "Value", "field_type": "Number"},
			{"field_name": "notice_period_days", "label": "Notice Period (days)", "field_type": "Integer"},
			{"field_name": "auto_renews", "label": "Auto Renews", "field_type": "Boolean"},
			{"field_name": "governing_law", "label": "Governing Law", "field_type": "String"},
		],
	},
]

PROMPT_TEMPLATES = [
	{
		"template_name": "Document Summary",
		"category": "Summarization",
		"description": "Concise executive summary of a document.",
		"system_prompt": "You are a precise analyst. You never invent facts.",
		"user_prompt": (
			"Summarise the document below for an executive audience.\n"
			"Cover the purpose, the key facts and any required actions.\n\n"
			"{{ content }}"
		),
		"variables": [{"variable": "content", "label": "Content", "required": 1}],
	},
	{
		"template_name": "Grounded Answer",
		"category": "RAG",
		"description": "Answer a question strictly from supplied context.",
		"system_prompt": (
			"Answer only from the CONTEXT. If the context does not contain the answer, "
			"say you do not have that information. Cite passages as [1], [2]."
		),
		"user_prompt": "CONTEXT:\n{{ context }}\n\nQUESTION: {{ question }}",
		"variables": [
			{"variable": "context", "label": "Context", "required": 1},
			{"variable": "question", "label": "Question", "required": 1},
		],
	},
]


def before_install() -> None:
	"""Warn about optional dependencies that unlock extra file formats."""
	optional = {
		"pypdf": "PDF",
		"docx": "Word (.docx)",
		"openpyxl": "Excel (.xlsx)",
		"pptx": "PowerPoint (.pptx)",
		"bs4": "HTML",
	}

	missing = []
	for module, label in optional.items():
		try:
			__import__(module)
		except ImportError:
			missing.append(label)

	if missing:
		print(
			"\n  Note: these document formats need optional packages: "
			+ ", ".join(missing)
			+ '\n  Install them with:  bench pip install --editable "./apps/ai_fr_hg[documents]"\n'
		)


def after_install() -> None:
	"""Seed roles, defaults and starter records."""
	ensure_site_file_directories()
	create_roles()
	create_settings()
	create_default_provider()
	create_builtin_tools()
	create_extraction_schemas()
	create_prompt_templates()
	create_default_knowledge_base()
	create_default_agent()
	create_default_policies()
	create_default_folders()
	seed_resource_marketplace()

	frappe.db.commit()  # nosemgrep: frappe-manual-commit

	print("")
	print("  AI Platform installed.")
	print("")
	print("  Next steps:")
	print("    1. Start your local runtime, e.g.  ollama serve")
	print("    2. Pull a chat model,        e.g.  ollama pull llama3.1:8b")
	print("    3. Pull an embedding model,  e.g.  ollama pull nomic-embed-text")
	print("    4. Open /app/ai-control-center and run 'Test All Providers'")
	print("    5. Run 'Discover Models' to register what the runtime has")
	print("")


def ensure_site_file_directories() -> None:
	"""Ensure Frappe's native public/private upload roots exist.

	Frappe's chunked upload endpoint writes temporary files below these roots.
	A site recreated by restore/reinstall can be missing empty directories (they
	are normally excluded from backups), which otherwise turns a native upload
	into a FileNotFoundError before app code receives it.
	"""
	for parts in (("public", "files"), ("private", "files")):
		Path(frappe.get_site_path(*parts)).mkdir(parents=True, exist_ok=True)


def create_default_folders() -> None:
	"""Create a coherent, navigable folder structure (File & Folder §11, operational)."""
	try:
		from ai_fr_hg.ai.folders import create_folder as _create_folder
	except Exception:
		return
	folders = [
		("AI Platform", "Home"),
		("Contracts", "Home/AI Platform"),
		("Knowledge Base", "Home/AI Platform"),
		("Projects", "Home/AI Platform"),
		("Reports", "Home/AI Platform"),
		("Agent Outputs", "Home/AI Platform"),
		("Shared Uploads", "Home"),
		("Attachments", "Home"),
	]
	for name, parent in folders:
		try:
			expected = f"{parent}/{name}"
			if not frappe.db.exists("File", expected):
				if frappe.db.exists("File", parent):
					_create_folder(name, parent_folder=parent, is_private=0)
		except Exception:
			frappe.log_error(title=f"Default folder creation failed: {name}", message=frappe.get_traceback())
	try:
		settings = frappe.get_single("AI Platform Settings")
		if not settings.storage_folder:
			settings.storage_folder = "Home/AI Platform"
			settings.flags.ignore_permissions = True
			settings.save(ignore_permissions=True)
	except Exception:
		frappe.log_error(title="Storage folder default assignment failed", message=frappe.get_traceback())


def seed_resource_marketplace() -> None:
	"""Register the built-in resource marketplace so downloads work out of the box."""
	try:
		from ai_fr_hg.ai.resources.catalog import catalog_tables_ready, refresh_builtin_catalog

		if not catalog_tables_ready():
			frappe.logger("ai_fr_hg").info(
				"Resource marketplace seeding skipped: AI Resources module has not been synced yet. "
				"Run `bench migrate` once more after the app's modules are synced."
			)
			return
		refresh_builtin_catalog(user="Administrator")
	except Exception:
		frappe.log_error(title="Resource marketplace seeding failed", message=frappe.get_traceback())


def create_roles() -> None:
	for role in ROLES:
		if frappe.db.exists("Role", role["role_name"]):
			continue
		doc = frappe.new_doc("Role")
		doc.update(role)
		doc.flags.ignore_permissions = True
		doc.insert(ignore_permissions=True)


def create_settings() -> None:
	settings = frappe.get_single("AI Platform Settings")
	if not settings.default_system_prompt:
		settings.default_system_prompt = (
			"You are a helpful enterprise AI assistant running entirely on local infrastructure. "
			"Answer accurately and concisely. If you are unsure, say so plainly rather than guessing."
		)
	if not settings.redact_patterns:
		# Sensible starting redactions: card numbers and long digit runs.
		settings.redact_patterns = "\n".join(
			[
				r"\b\d{4}[- ]?\d{4}[- ]?\d{4}[- ]?\d{4}\b",
				r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b",
			]
		)
	settings.flags.ignore_permissions = True
	settings.save(ignore_permissions=True)


def create_default_provider() -> None:
	"""Register a local Ollama endpoint so the platform is usable at once."""
	if frappe.db.exists("AI Provider", "Local Ollama"):
		return

	doc = frappe.new_doc("AI Provider")
	doc.update(
		{
			"provider_name": "Local Ollama",
			"provider_type": "Ollama",
			"base_url": "http://localhost:11434",
			"enabled": 1,
			"is_default": 1,
			"priority": 1,
			"request_timeout": 120,
			"max_concurrent_requests": 4,
			"description": "Default local Ollama runtime. Start it with `ollama serve`.",
		}
	)
	doc.flags.ignore_permissions = True
	doc.insert(ignore_permissions=True)


def create_builtin_tools() -> None:
	for tool in BUILTIN_TOOLS:
		if frappe.db.exists("AI Tool", tool["tool_name"]):
			continue
		doc = frappe.new_doc("AI Tool")
		doc.update({k: v for k, v in tool.items() if k != "parameters"})
		for parameter in tool.get("parameters") or []:
			doc.append("parameters", parameter)
		doc.flags.ignore_permissions = True
		doc.insert(ignore_permissions=True)


def create_extraction_schemas() -> None:
	for schema in EXTRACTION_SCHEMAS:
		if frappe.db.exists("AI Extraction Schema", schema["schema_name"]):
			continue
		doc = frappe.new_doc("AI Extraction Schema")
		doc.update({k: v for k, v in schema.items() if k != "extraction_fields"})
		for field in schema["extraction_fields"]:
			doc.append("extraction_fields", field)
		doc.flags.ignore_permissions = True
		doc.insert(ignore_permissions=True)


def create_prompt_templates() -> None:
	for template in PROMPT_TEMPLATES:
		if frappe.db.exists("AI Prompt Template", template["template_name"]):
			continue
		doc = frappe.new_doc("AI Prompt Template")
		doc.update({k: v for k, v in template.items() if k != "variables"})
		for variable in template.get("variables") or []:
			doc.append("variables", variable)
		doc.flags.ignore_permissions = True
		doc.insert(ignore_permissions=True)


def create_default_knowledge_base() -> None:
	if frappe.db.exists("AI Knowledge Base", "General Knowledge"):
		return

	doc = frappe.new_doc("AI Knowledge Base")
	doc.update(
		{
			"knowledge_base_name": "General Knowledge",
			"description": "Default knowledge base for uploaded documents.",
			"enabled": 1,
			"is_public": 1,
			"chunk_size": 1200,
			"chunk_overlap": 150,
			"top_k": 6,
			"similarity_threshold": 0.25,
		}
	)
	doc.flags.ignore_permissions = True
	doc.insert(ignore_permissions=True)


def create_default_agent() -> None:
	if frappe.db.exists("AI Agent", "General Assistant"):
		return

	doc = frappe.new_doc("AI Agent")
	doc.update(
		{
			"agent_name": "General Assistant",
			"description": "General purpose assistant with knowledge retrieval and read-only tools.",
			"enabled": 1,
			"is_default": 1,
			"temperature": 0.2,
			"max_tokens": 512,
			"max_tool_iterations": 2,
			"use_knowledge": 0,
			"top_k": 6,
			"citation_mode": "Inline",
			"response_format": "Markdown",
			"use_tools": 1,
			"greeting": "Hello. Ask me anything about your documents or your data.",
			"system_prompt": (
				"You are an enterprise AI assistant running entirely on local infrastructure.\n"
				"Be accurate, concise and professional.\n"
				"When context passages are provided, ground your answer in them and cite them.\n"
				"When you do not know something, say so rather than guessing.\n"
				"Use the available tools when they would give a more accurate answer."
			),
		}
	)
	# Knowledge retrieval is opt-in per conversation: automatically running it on
	# every small-talk chat adds an embedding round-trip and prompt bloat on a
	# fresh site that often has no documents yet.
	for tool in ("search_knowledge_base", "get_document_text", "current_datetime"):
		if frappe.db.exists("AI Tool", tool):
			doc.append("tools", {"tool": tool, "enabled": 1})

	doc.flags.ignore_permissions = True
	doc.insert(ignore_permissions=True)


def create_default_policies() -> None:
	"""A conservative default policy for the AI User role."""
	if frappe.db.exists("AI Resource Policy", "Standard AI User"):
		return

	doc = frappe.new_doc("AI Resource Policy")
	doc.update(
		{
			"policy_name": "Standard AI User",
			"enabled": 1,
			"role": "AI User",
			"priority": 50,
			"max_requests_per_hour": 200,
			"max_tokens_per_day": 500_000,
			"max_documents_per_day": 100,
			"allow_tools": 1,
			"allow_document_upload": 1,
			"allow_pipeline_execution": 0,
			"allow_model_management": 0,
			"notes": "Default limits applied to everyone holding the AI User role.",
		}
	)
	doc.flags.ignore_permissions = True
	doc.insert(ignore_permissions=True)


def _enable_github_test_annotations() -> None:
	"""Expose unittest failures as check annotations when Actions logs are unavailable."""
	import os
	import traceback
	import unittest

	if not os.environ.get("GITHUB_ACTIONS") or getattr(unittest.TestResult, "_ai_fr_hg_annotations", False):
		return

	def emit(test, err) -> None:
		title = str(test).replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
		message = "".join(traceback.format_exception(*err))[-12000:]
		message = message.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
		print(f"::error title={title}::{message}")

	original_error = unittest.TestResult.addError
	original_failure = unittest.TestResult.addFailure

	def add_error(self, test, err):
		emit(test, err)
		return original_error(self, test, err)

	def add_failure(self, test, err):
		emit(test, err)
		return original_failure(self, test, err)

	unittest.TestResult.addError = add_error
	unittest.TestResult.addFailure = add_failure
	unittest.TestResult._ai_fr_hg_annotations = True


def after_migrate() -> None:
	"""Clear Desk and boot caches after every migrate.

	A stale `bootinfo` or `assets.json` hash is the common cause of
	"Loading failed for .../build_events.bundle.XXX.js" and the subsequent
	"xhr poll error" when the Desk router retries with a mismatched socket
	host. Clearing the cache here makes returning to Desk after `bench
	migrate` or `bench build` never reuse a stale hashed asset.
	"""
	try:
		frappe.clear_cache()
	except Exception:
		pass
	try:
		ensure_site_file_directories()
	except Exception:
		pass
	try:
		# Re-ensure the module fix so Desk workspaces that were cached with
		# the old module name render on the first Desk return after migrate.
		_fix_learning_doctype_modules()
	except Exception:
		pass

	# CHAT-02: re-assert the message-sequence indexes on every migrate.
	#
	# Neither of the two previous owners fires on an already-installed site:
	# `AI Message.on_doctype_update` runs from `DocType.on_update`, and
	# `frappe.modules.import_file` skips the import entirely (and so the save)
	# when the JSON's migration_hash is unchanged; the v0_0_17 patch is marked
	# already-applied on any site installed after it was written. A real site
	# therefore ran with no database-level uniqueness backstop while the test
	# suite that checks for it only ever ran on fresh CI installs.
	#
	# This hook has no such condition -- Frappe calls it on every migrate --
	# and `ensure_sequence_constraints` is idempotent, so it is the correct
	# owner. Deliberately NOT wrapped in `except: pass` like the cosmetic
	# repairs above: a missing uniqueness constraint is a correctness defect,
	# and it must fail the migration loudly rather than leave the operator
	# with a green run and no guarantee.
	from ai_fr_hg.ai.conversation_indexes import ensure_sequence_constraints

	ensure_sequence_constraints()

	# Idempotent: an existing site gets the built-in resource marketplace as
	# soon as the app is upgraded.
	try:
		seed_resource_marketplace()
	except Exception:
		frappe.log_error(title="Resource marketplace migrate seeding failed", message=frappe.get_traceback())


def before_tests() -> None:
	"""Clear cached metadata before the standalone app fixtures are created.

	The app supports a plain Frappe site and its tests do not require ERPNext.
	In particular, do not query or create ERPNext's ``Company`` DocType here:
	Frappe runs this hook before every test category, including on sites where
	that DocType does not exist.
	"""
	_enable_github_test_annotations()
	_fix_learning_doctype_modules()
	frappe.clear_cache()


def _fix_learning_doctype_modules() -> None:
	"""Ensure learning DocTypes have the correct module assignment.

	If the DocType was created from an older JSON with module ``"Core"`` instead
	of ``"AI Learning"``, Frappe resolves the controller under
	``frappe.core.doctype`` and ``frappe.new_doc("AI Knowledge Candidate")``
	fails.  A proper ``bench migrate`` runs the corresponding patch, but the
	fix is also applied here so test environments do not need a manual migrate.
	"""
	# Unit-test bootstraps and minimal Frappe installations may intentionally
	# expose no database facade.  The model-sync patch performs this repair in
	# real sites, so cache setup must remain safe in that environment.
	db = getattr(frappe, "db", None)
	if not callable(getattr(db, "get_value", None)):
		return

	for doctype in ("AI Knowledge Candidate", "AI Memory", "AI Skill"):
		current = db.get_value("DocType", doctype, "module")
		if current and current != "AI Learning":
			db.set_value("DocType", doctype, "module", "AI Learning")
