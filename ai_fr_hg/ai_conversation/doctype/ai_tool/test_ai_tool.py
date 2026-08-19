# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Frappe integration coverage for this DocType and its canonical domain services."""

from unittest.mock import patch

import frappe

from ai_fr_hg.tests.integration_test_case import AIPlatformTestCase


class TestToolDocType(AIPlatformTestCase):
	def test_tool_name_must_be_snake_case(self):
		doc = frappe.get_doc(
			{
				"doctype": "AI Tool",
				"tool_name": "Not Snake Case",
				"tool_type": "Builtin",
				"handler": "current_datetime",
				"description": "Invalid name.",
			}
		)
		with self.assertRaises(frappe.ValidationError):
			doc.insert(ignore_permissions=True)

	def test_unknown_builtin_handler_rejected(self):
		doc = frappe.get_doc(
			{
				"doctype": "AI Tool",
				"tool_name": "bogus_tool",
				"tool_type": "Builtin",
				"handler": "does_not_exist",
				"description": "Bad handler.",
			}
		)
		with self.assertRaises(frappe.ValidationError):
			doc.insert(ignore_permissions=True)

	def test_tool_schema_generated(self):
		doc = frappe.get_doc(
			{
				"doctype": "AI Tool",
				"tool_name": "sample_lookup",
				"tool_type": "Builtin",
				"handler": "current_datetime",
				"description": "Sample tool.",
				"parameters": [
					{"parameter": "query", "parameter_type": "String", "required": 1},
					{"parameter": "limit", "parameter_type": "Integer"},
				],
			}
		)
		doc.insert(ignore_permissions=True)

		schema = frappe.parse_json(doc.json_schema)
		self.assertEqual(schema["name"], "sample_lookup")
		self.assertEqual(schema["parameters"]["properties"]["query"]["type"], "string")
		self.assertIn("query", schema["parameters"]["required"])
		self.assertNotIn("limit", schema["parameters"]["required"])


class TestCanonicalToolExecution(AIPlatformTestCase):
	def make_clock_tool(self, name="canonical_clock"):
		return frappe.get_doc(
			{
				"doctype": "AI Tool",
				"tool_name": name,
				"tool_type": "Builtin",
				"handler": "current_datetime",
				"enabled": 1,
				"is_readonly_tool": 1,
				"max_runtime_seconds": 30,
				"description": "Return the current site time.",
			}
		).insert(ignore_permissions=True)

	def test_success_is_persisted_with_requester_and_audit(self):
		from ai_fr_hg.ai.tools import execute_tool

		tool = self.make_clock_tool("canonical_clock_success")
		outcome = execute_tool(tool.name)

		self.assertEqual(outcome["status"], "Success")
		invocation = frappe.get_doc("AI Tool Invocation", outcome["invocation"])
		self.assertEqual(invocation.tool, tool.name)
		self.assertEqual(invocation.user, frappe.session.user)
		self.assertEqual(invocation.status, "Success")
		self.assertTrue(invocation.finished_at)
		self.assertTrue(
			frappe.db.exists(
				"AI Audit Log",
				{
					"action": f"Tool Executed: {tool.name}",
					"reference_doctype": "AI Tool Invocation",
					"reference_name": invocation.name,
				},
			)
		)

	def test_argument_contract_fails_before_dispatch(self):
		from ai_fr_hg.ai.tools import execute_tool

		tool = self.make_clock_tool("canonical_clock_contract")
		with patch("ai_fr_hg.ai.tools._dispatch") as dispatch:
			outcome = execute_tool(tool.name, {"unexpected": True})

		dispatch.assert_not_called()
		self.assertEqual(outcome["status"], "Failed")
		self.assertIn("unsupported arguments", outcome["error"])
		self.assertEqual(frappe.db.get_value("AI Tool Invocation", outcome["invocation"], "status"), "Failed")

	def test_invalid_pipeline_context_is_refused_without_an_invocation(self):
		from ai_fr_hg.ai.tools import execute_tool

		tool = self.make_clock_tool("canonical_clock_context")
		before = frappe.db.count("AI Tool Invocation", {"tool": tool.name})
		outcome = execute_tool(tool.name, pipeline_run="missing-run")
		self.assertEqual(outcome["status"], "Failed")
		self.assertEqual(frappe.db.count("AI Tool Invocation", {"tool": tool.name}), before)

	def test_write_tool_approval_resumes_once_with_original_provenance(self):
		from ai_fr_hg.ai.tools import approve_invocation, execute_tool

		description = "Created by governed tool approval test"
		tool = frappe.get_doc(
			{
				"doctype": "AI Tool",
				"tool_name": "governed_todo_create",
				"tool_type": "DocType Action",
				"target_doctype": "ToDo",
				"enabled": 1,
				"requires_approval": 1,
				"max_runtime_seconds": 30,
				"description": "Create one approved ToDo.",
				"parameters": [
					{
						"parameter": "action",
						"parameter_type": "String",
						"required": 1,
						"enum_values": "create",
					},
					{"parameter": "values", "parameter_type": "Object", "required": 1},
				],
			}
		).insert(ignore_permissions=True)

		pending = execute_tool(tool.name, {"action": "create", "values": {"description": description}})
		self.assertEqual(pending["status"], "Pending Approval")
		self.assertFalse(frappe.db.exists("ToDo", {"description": description}))

		outcome = approve_invocation(pending["invocation"])
		self.assertEqual(outcome["status"], "Success")
		invocation = frappe.get_doc("AI Tool Invocation", pending["invocation"])
		self.assertEqual(invocation.status, "Success")
		self.assertEqual(invocation.user, "Administrator")
		self.assertEqual(invocation.approved_by, "Administrator")
		self.assertEqual(frappe.db.count("ToDo", {"description": description}), 1)
		with self.assertRaises(frappe.ValidationError):
			approve_invocation(pending["invocation"])

	def test_rejected_tool_invocation_never_dispatches(self):
		from ai_fr_hg.ai.tools import execute_tool, reject_invocation

		description = "Rejected governed tool write"
		tool = frappe.get_doc(
			{
				"doctype": "AI Tool",
				"tool_name": "governed_todo_reject",
				"tool_type": "DocType Action",
				"target_doctype": "ToDo",
				"enabled": 1,
				"requires_approval": 1,
				"max_runtime_seconds": 30,
				"description": "Reject one proposed ToDo.",
				"parameters": [
					{
						"parameter": "action",
						"parameter_type": "String",
						"required": 1,
						"enum_values": "create",
					},
					{"parameter": "values", "parameter_type": "Object", "required": 1},
				],
			}
		).insert(ignore_permissions=True)
		pending = execute_tool(tool.name, {"action": "create", "values": {"description": description}})
		outcome = reject_invocation(pending["invocation"])

		self.assertEqual(outcome["status"], "Rejected")
		invocation = frappe.get_doc("AI Tool Invocation", pending["invocation"])
		self.assertEqual(invocation.status, "Rejected")
		self.assertEqual(invocation.rejected_by, frappe.session.user)
		self.assertTrue(invocation.rejected_at)
		self.assertFalse(frappe.db.exists("ToDo", {"description": description}))
		self.assertTrue(
			frappe.db.exists(
				"AI Audit Log",
				{
					"action": "Tool Invocation Rejected",
					"reference_doctype": "AI Tool Invocation",
					"reference_name": invocation.name,
				},
			)
		)

	def test_pipeline_tool_step_records_permitted_run_context(self):
		from ai_fr_hg.ai.pipeline import run_pipeline

		tool = self.make_clock_tool("canonical_clock_pipeline")
		pipeline = frappe.get_doc(
			{
				"doctype": "AI Pipeline",
				"pipeline_name": "Canonical Tool Context Pipeline",
				"enabled": 1,
				"steps": [
					{
						"step_name": "Read Clock",
						"step_type": "Tool",
						"tool": tool.name,
						"enabled": 1,
					}
				],
			}
		).insert(ignore_permissions=True)

		run = run_pipeline(pipeline.name, enqueue_job=False)
		run.reload()
		invocation = frappe.get_doc(
			"AI Tool Invocation",
			frappe.db.get_value("AI Tool Invocation", {"pipeline_run": run.name}, "name"),
		)
		self.assertEqual(run.status, "Completed")
		self.assertEqual(invocation.status, "Success")
		self.assertEqual(invocation.pipeline_run, run.name)
		self.assertEqual(invocation.user, run.triggered_by)


class TestBuiltinTools(AIPlatformTestCase):
	def test_current_datetime(self):
		from ai_fr_hg.ai.tools.builtin import current_datetime

		result = current_datetime()
		self.assertIn("date", result)
		self.assertIn("timezone", result)

	def test_count_documents(self):
		from ai_fr_hg.ai.tools.builtin import count_documents

		result = count_documents("AI Provider")
		self.assertEqual(result["doctype"], "AI Provider")
		self.assertGreaterEqual(result["count"], 1)

	def test_get_document_text(self):
		from ai_fr_hg.ai.tools.builtin import get_document_text

		document = self.make_document("Tool Read Document", "Readable content for the tool.")
		# By primary key name
		result = get_document_text(document.name)
		self.assertIn("Readable content", result["content"])
		self.assertEqual(result["document"], document.name)

		# By title
		result_by_title = get_document_text(document.title)
		self.assertIn("Readable content", result_by_title["content"])

		# By file_path alias
		result_by_path = get_document_text(file_path="Tool Read Document.docx")
		self.assertIn("Readable content", result_by_path["content"])

	def test_get_document_text_does_not_embed_inline(self):
		"""Reading a document must not run the embedding pipeline in-request.

		Embedding is many model round trips; doing it inside a chat turn is
		what pushed `send_message` past the gateway timeout.
		"""
		from ai_fr_hg.ai.tools.builtin import get_document_text

		document = self.make_document("Unextracted Document", "placeholder")
		document.db_set("content", None, update_modified=False)
		document.db_set("status", "Queued", update_modified=False)
		document.db_set("source_file", "/files/unextracted.docx", update_modified=False)

		with patch("ai_fr_hg.ai.ingestion.process_document") as process:
			get_document_text(document.name)

		self.assertTrue(process.called, "text should still be extracted on demand")
		self.assertIs(
			process.call_args.kwargs.get("index"),
			False,
			"indexing must be deferred to a background worker",
		)

	def test_missing_document_reports_the_alternatives(self):
		"""A failed lookup should help the model, not just say 'not found'."""
		from ai_fr_hg.ai.tools.builtin import get_document_text

		self.make_document("A Findable Report", "Some content.")
		result = get_document_text("No_Such_File_At_All.docx")

		self.assertFalse(result["found"])
		self.assertIn("available_documents", result)
		titles = [row.get("title") for row in result["available_documents"]]
		self.assertIn("A Findable Report", titles)

	def test_empty_document_explains_why(self):
		"""'No text' must be distinguishable from 'no such document'."""
		from ai_fr_hg.ai.tools.builtin import get_document_text

		document = self.make_document("Scanned Only Document", "placeholder")
		document.db_set("content", "", update_modified=False)
		document.db_set("status", "Failed", update_modified=False)
		document.db_set("error_message", "No text could be extracted.", update_modified=False)

		result = get_document_text(document.name)

		self.assertTrue(result["found"])
		self.assertIn("could not be processed", result["error"])
		self.assertIn("No text could be extracted", result["error"])

	def test_list_documents_respects_limit(self):
		from ai_fr_hg.ai.tools.builtin import list_documents

		results = list_documents("AI Provider", limit=1)
		self.assertLessEqual(len(results), 1)
