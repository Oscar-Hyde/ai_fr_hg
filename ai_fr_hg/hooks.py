app_name = "ai_fr_hg"
app_title = "Ai Fr Hg"
app_publisher = "Ai Fr Hg"
app_description = "Ai Fr Hg"
app_email = "oscarhyde2002@outlook.com"
app_license = "mit"

# Send non-GET requests for this app's endpoints as native `application/json`
# bodies instead of form-encoded, per-key JSON-stringified values.
use_json_request_body = True

# Apps
# ------------------

required_apps = []

# Each item in the list will be shown as an app in the apps page
add_to_apps_screen = [
	{
		"name": "ai_fr_hg",
		"logo": "/assets/ai_fr_hg/images/logo.svg",
		"title": "AI Platform",
		"route": "/app/ai-control-center",
		"has_permission": "ai_fr_hg.utils.permissions.has_app_permission",
	}
]

# Companion apps that extend a host app (instead of taking their own apps-screen icon) can pin
# their workspaces into the host app's workspace dock (rail) with this hook. Declaring it keeps
# the app off the apps screen, so it takes precedence over any add_to_apps_screen above. Who can
# see a pinned workspace is controlled by that workspace's own Roles table.
# add_to_workspace_dock = [
# 	{
# 		"app": "erpnext",
# 		"workspace": "My Workspace",
# 	}
# ]

# Includes in <head>
# ------------------

# include js, css files in header of desk.html
app_include_css = "ai_fr_hg.bundle.css"
app_include_js = "ai_fr_hg.bundle.js"

# include js, css files in header of web template
# web_include_css = "/assets/ai_fr_hg/css/ai_fr_hg.css"
# web_include_js = "/assets/ai_fr_hg/js/ai_fr_hg.js"

# include custom scss in every website theme (without file extension ".scss")
# website_theme_scss = "ai_fr_hg/public/scss/website"

# include js, css files in header of web form
# webform_include_js = {"doctype": "public/js/doctype.js"}
# webform_include_css = {"doctype": "public/css/doctype.css"}

# include js in page
# page_js = {"page" : "public/js/file.js"}

# File is a Frappe core DocType, so its supported list-view extension lives in
# this app's standard override location.  It augments FileView actions only;
# it never replaces the native File list/tree/grid presentation.
doctype_js = {"File": "public/js/file.js"}
doctype_list_js = {"File": "public/js/file_list.js"}
# AI Document owns its Tree View the same way it owns form and list scripts.
# Mutations stay in ai.document_tree; this file only configures Tree View.
doctype_tree_js = {"AI Document": "ai_knowledge/doctype/ai_document/ai_document_tree.js"}
# doctype_calendar_js = {"doctype" : "public/js/doctype_calendar.js"}

# Svg Icons
# ------------------
# include app icons in desk
# app_include_icons = "ai_fr_hg/public/icons.svg"

# Home Pages
# ----------

# application home page (will override Website Settings)
# home_page = "login"

# website user home page (by Role)
# role_home_page = {
# 	"Role": "home_page"
# }

# Setup Wizard
# ------------

# open a fresh site's setup in this app's own UI instead of the desk wizard.
# must be a non-desk route (not under /desk or /app); to customize setup within
# desk, use setup_wizard_stages / setup_wizard_complete instead.
# setup_wizard_url = "/ai_fr_hg/setup"

# Generators
# ----------

# automatically create page for each record of this doctype
# website_generators = ["Web Page"]

# automatically load and sync documents of this doctype from downstream apps
# importable_doctypes = [doctype_1]

# Jinja
# ----------

# add methods and filters to jinja environment
jinja = {
	"methods": ["ai_fr_hg.utils.jinja.get_ai_summary", "ai_fr_hg.utils.jinja.ai_search"],
}

# Installation
# ------------

before_install = "ai_fr_hg.install.before_install"
after_install = "ai_fr_hg.install.after_install"
after_migrate = "ai_fr_hg.install.after_migrate"

# Uninstallation
# ------------

before_uninstall = "ai_fr_hg.uninstall.before_uninstall"

# Disable / Enable
# ----------------
# Called when this app is logically disabled or re-enabled on a site,
# without uninstalling it. Use this to hide/restore fields this app adds
# to other apps' doctypes.

# before_disable = "ai_fr_hg.uninstall.before_disable"
# after_disable = "ai_fr_hg.uninstall.after_disable"
# before_enable = "ai_fr_hg.install.before_enable"
# after_enable = "ai_fr_hg.install.after_enable"

# Integration Setup
# ------------------
# To set up dependencies/integrations with other apps
# Name of the app being installed is passed as an argument

# before_app_install = "ai_fr_hg.utils.before_app_install"
# after_app_install = "ai_fr_hg.utils.after_app_install"

# Integration Cleanup
# -------------------
# To clean up dependencies/integrations with other apps
# Name of the app being uninstalled is passed as an argument

# before_app_uninstall = "ai_fr_hg.utils.before_app_uninstall"
# after_app_uninstall = "ai_fr_hg.utils.after_app_uninstall"

# Build
# ------------------
# To hook into the build process

# after_build = "ai_fr_hg.build.after_build"

# Desk Notifications
# ------------------
# See frappe.core.notifications.get_notification_config

# notification_config = "ai_fr_hg.notifications.get_notification_config"

# Permissions
# -----------
# Permissions evaluated in scripted ways

permission_query_conditions = {
	"AI Conversation": "ai_fr_hg.utils.permissions.conversation_query",
	"AI Message": "ai_fr_hg.utils.permissions.message_query",
	"AI Knowledge Base": "ai_fr_hg.utils.permissions.knowledge_base_query",
	"AI Document": "ai_fr_hg.utils.permissions.document_query",
	"AI Document Chunk": "ai_fr_hg.utils.permissions.chunk_query",
	"AI Pattern Entity": "ai_fr_hg.utils.permissions.pattern_entity_query",
	"AI Entity Relationship": "ai_fr_hg.utils.permissions.entity_relationship_query",
	"AI Translation": "ai_fr_hg.utils.permissions.translation_query",
	"AI Translation Glossary": "ai_fr_hg.utils.permissions.glossary_query",
	"AI Agent": "ai_fr_hg.utils.permissions.agent_query",
	"AI Knowledge Candidate": "ai_fr_hg.utils.permissions.candidate_query",
	"AI Memory": "ai_fr_hg.utils.permissions.memory_query",
	"AI Skill": "ai_fr_hg.utils.permissions.skill_query",
	"AI Task": "ai_fr_hg.utils.permissions.task_query",
	"AI Automation Event": "ai_fr_hg.utils.permissions.automation_event_query",
	"AI Pipeline Run": "ai_fr_hg.utils.permissions.pipeline_run_query",
	"AI Execution Log": "ai_fr_hg.utils.permissions.execution_log_query",
	"AI Search Query": "ai_fr_hg.utils.permissions.search_query",
	"AI Tool Invocation": "ai_fr_hg.utils.permissions.tool_invocation_query",
	"AI Folder Settings": "ai_fr_hg.utils.permissions.folder_settings_query",
	"AI Folder Favorite": "ai_fr_hg.utils.permissions.folder_favorite_query",
	"AI Resource Download": "ai_fr_hg.utils.permissions.resource_download_query",
}

has_permission = {
	doctype: "ai_fr_hg.utils.permissions.has_document_permission" for doctype in permission_query_conditions
}

# Document Events
# ---------------
# Hook on document methods and events

doc_events = {
	"*": {
		"after_insert": "ai_fr_hg.ai.automation.handle_document_event",
		"on_update": "ai_fr_hg.ai.automation.handle_document_event",
		"on_submit": "ai_fr_hg.ai.automation.handle_document_event",
		"on_cancel": "ai_fr_hg.ai.automation.handle_document_event",
		"on_trash": "ai_fr_hg.ai.automation.handle_document_event",
	},
	"File": {
		"before_insert": "ai_fr_hg.utils.file_hooks.before_file_insert",
		"before_save": "ai_fr_hg.utils.file_hooks.before_file_save",
		"after_insert": "ai_fr_hg.utils.file_hooks.on_file_upload",
		"on_update": "ai_fr_hg.utils.file_hooks.on_file_update",
		"on_trash": "ai_fr_hg.utils.file_hooks.on_file_delete",
	},
	# The pattern layer owns its own rows; it never alters the document or the
	# ingestion pipeline. Frappe runs on_trash hooks before link validation, so
	# this cascade can never block document deletion.
	"AI Document": {
		"on_trash": [
			"ai_fr_hg.ai.patterns.handle_document_trashed",
			"ai_fr_hg.ai.semantic.handle_document_trashed",
		],
	},
}

# Scheduled Tasks
# ---------------

scheduler_events = {
	"cron": {
		# Provider reachability, throttled internally to the configured interval.
		"*/5 * * * *": [
			"ai_fr_hg.tasks.health_check",
		],
		# Due scheduled pipelines.
		"*/10 * * * *": [
			"ai_fr_hg.tasks.run_scheduled_pipelines",
			"ai_fr_hg.tasks.run_due_tasks",
		],
		# Resource marketplace recovery (stale checkpoints).
		"*/15 * * * *": [
			"ai_fr_hg.tasks.recover_resource_downloads",
		],
	},
	"hourly_long": [
		"ai_fr_hg.tasks.process_pending_documents",
		# Opt-in high-precision pattern extraction for indexed documents.
		"ai_fr_hg.tasks.scan_pending_pattern_entities",
		# Opt-in semantic entity/relationship extraction. Costs model calls, so
		# it is disabled by default and skips documents already scanned at
		# their current checksum.
		"ai_fr_hg.tasks.scan_pending_semantic_entities",
	],
	"daily_long": [
		"ai_fr_hg.tasks.sync_models",
		"ai_fr_hg.tasks.rollup_usage",
		"ai_fr_hg.tasks.backup_knowledge",
	],
	"weekly_long": [
		"ai_fr_hg.tasks.cleanup_logs",
	],
}

# Testing
# -------

before_tests = "ai_fr_hg.install.before_tests"

# Extend DocType Class
# ------------------------------
#
# Specify custom mixins to extend the standard doctype controller.
# extend_doctype_class = {
# 	"Task": "ai_fr_hg.custom.task.CustomTaskMixin"
# }

# Overriding Methods
# ------------------------------
#
# override_whitelisted_methods = {
# 	"frappe.desk.doctype.event.event.get_events": "ai_fr_hg.event.get_events"
# }
#
# each overriding function accepts a `data` argument;
# generated from the base implementation of the doctype dashboard,
# along with any modifications made in other Frappe apps
# override_doctype_dashboards = {
# 	"Task": "ai_fr_hg.task.get_dashboard_data"
# }

# exempt linked doctypes from being automatically cancelled
#
# auto_cancel_exempted_doctypes = ["Auto Repeat"]

# Ignore links to specified DocTypes when deleting documents
# -----------------------------------------------------------

# Audit references preserve historical identities and must never become
# retention constraints on the business/File records they describe.
ignore_links_on_delete = ["AI Audit Log", "AI Automation Event"]

# Request Events
# ----------------
# before_request = ["ai_fr_hg.utils.before_request"]
# after_request = ["ai_fr_hg.utils.after_request"]

# Job Events
# ----------
# before_job = ["ai_fr_hg.utils.before_job"]
# after_job = ["ai_fr_hg.utils.after_job"]

# after_file_upload = ["ai_fr_hg.utils.after_file_upload"]

# User Data Protection
# --------------------

# user_data_fields = [
# 	{
# 		"doctype": "{doctype_1}",
# 		"filter_by": "{filter_by}",
# 		"redact_fields": ["{field_1}", "{field_2}"],
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_2}",
# 		"filter_by": "{filter_by}",
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_3}",
# 		"strict": False,
# 	},
# 	{
# 		"doctype": "{doctype_4}"
# 	}
# ]

# Authentication and authorization
# --------------------------------

# auth_hooks = [
# 	"ai_fr_hg.auth.validate"
# ]

# Automatically update python controller files with type annotations for this app.
export_python_type_annotations = True

# Require all whitelisted methods to have type annotations
require_type_annotated_api_methods = True

default_log_clearing_doctypes = {
	"AI Execution Log": 90,
	"AI Service Health Log": 30,
	"AI Audit Log": 365,
	"AI Search Query": 30,
}

# Translation
# ------------
# List of apps whose translatable strings should be excluded from this app's translations.
# ignore_translatable_strings_from = []


# Extension Points
# ----------------
# Other apps extend the AI platform by contributing to these hooks. Each maps a
# key to a dotted path; see the documentation in docs/EXTENDING.md.
#
# ai_providers        - new AI runtime adapters       (BaseProvider subclasses)
# ai_document_readers - new file format readers       (BaseReader subclasses)
# ai_tools            - new built-in tool handlers    (plain callables)
# ai_pipeline_methods - trusted Custom Method steps   (plain callables)
# ai_task_methods     - trusted Custom AI Task methods (plain callables)
#
# Example, in another app's hooks.py:
#
# ai_providers = {"My Runtime": "my_app.providers.MyRuntimeProvider"}
# ai_document_readers = {"dwg": "my_app.readers.DWGReader"}
# ai_tools = {"lookup_customer": "my_app.tools.lookup_customer"}
# ai_pipeline_methods = {"sync_customer": "my_app.pipelines.sync_customer"}

ai_providers = {}
ai_document_readers = {}
ai_tools = {}
ai_pipeline_methods = {}

# Fixtures shipped with the app
# -----------------------------
fixtures = [
	{
		"dt": "Role",
		"filters": [["name", "in", ["AI Manager", "AI User", "AI Auditor"]]],
	},
]
