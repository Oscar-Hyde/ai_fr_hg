# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Frappe integration coverage for this DocType and its canonical domain services."""

from unittest.mock import patch

import frappe

from ai_fr_hg.ai.pipeline import pipeline_step_method
from ai_fr_hg.tests.integration_test_case import AIPlatformTestCase


class TestPipeline(AIPlatformTestCase):
	def test_pipeline_requires_steps(self):
		doc = frappe.get_doc({"doctype": "AI Pipeline", "pipeline_name": "Empty Pipeline", "enabled": 1})
		with self.assertRaises(frappe.ValidationError):
			doc.insert(ignore_permissions=True)

	def test_pipeline_cannot_call_itself(self):
		doc = frappe.get_doc(
			{
				"doctype": "AI Pipeline",
				"pipeline_name": "Recursive Pipeline",
				"enabled": 1,
				"steps": [
					{
						"step_name": "Self Call",
						"step_type": "Pipeline",
						"sub_pipeline": "Recursive Pipeline",
						"enabled": 1,
					}
				],
			}
		)
		with self.assertRaises(frappe.ValidationError):
			doc.insert(ignore_permissions=True)

	def test_registered_external_custom_method_resolves_from_hook(self):
		from ai_fr_hg.ai.pipeline import resolve_pipeline_step_method

		dotted_path = "external_enterprise_app.ai.steps.enrich_record"
		with (
			patch("ai_fr_hg.ai.pipeline.frappe.get_hooks", return_value={"enrich": dotted_path}) as hooks,
			patch(
				"ai_fr_hg.ai.pipeline.frappe.get_attr", return_value=unmarked_pipeline_method
			) as get_attr,
		):
			resolved = resolve_pipeline_step_method(dotted_path)

		self.assertIs(resolved, unmarked_pipeline_method)
		hooks.assert_called_once_with("ai_pipeline_methods")
		get_attr.assert_called_once_with(dotted_path)

	def test_unregistered_external_custom_method_is_rejected(self):
		doc = frappe.get_doc(
			{
				"doctype": "AI Pipeline",
				"pipeline_name": "Untrusted External Method Pipeline",
				"enabled": 1,
				"steps": [
					{
						"step_name": "Untrusted",
						"step_type": "Custom Method",
						"method": "frappe.utils.now",
						"enabled": 1,
					}
				],
			}
		)
		with self.assertRaises(frappe.ValidationError):
			doc.insert(ignore_permissions=True)

	def test_unmarked_app_custom_method_is_rejected(self):
		doc = frappe.get_doc(
			{
				"doctype": "AI Pipeline",
				"pipeline_name": "Unmarked App Method Pipeline",
				"enabled": 1,
				"steps": [
					{
						"step_name": "Unmarked",
						"step_type": "Custom Method",
						"method": "ai_fr_hg.ai_automation.doctype.ai_pipeline.test_ai_pipeline.unmarked_pipeline_method",
						"enabled": 1,
					}
				],
			}
		)
		with self.assertRaises(frappe.ValidationError):
			doc.insert(ignore_permissions=True)

	def test_custom_method_trust_is_rechecked_at_execution(self):
		from ai_fr_hg.ai.pipeline import run_pipeline

		pipeline = frappe.get_doc(
			{
				"doctype": "AI Pipeline",
				"pipeline_name": "Runtime Trust Recheck Pipeline",
				"enabled": 1,
				"steps": [
					{
						"step_name": "Trusted At Validation",
						"step_type": "Custom Method",
						"method": "ai_fr_hg.ai_automation.doctype.ai_pipeline.test_ai_pipeline.always_works",
						"enabled": 1,
					}
				],
			}
		).insert(ignore_permissions=True)

		marker = "_ai_pipeline_step_method"
		delattr(always_works, marker)
		try:
			run = run_pipeline(pipeline.name, enqueue_job=False)
		finally:
			setattr(always_works, marker, True)
		run.reload()
		self.assertEqual(run.status, "Failed")
		self.assertIn("not been marked", run.error_message)

	def test_nested_pipeline_records_parent_provenance(self):
		from ai_fr_hg.ai.pipeline import run_pipeline

		child = frappe.get_doc(
			{
				"doctype": "AI Pipeline",
				"pipeline_name": "Nested Child Pipeline",
				"enabled": 1,
				"steps": [
					{
						"step_name": "Child Work",
						"step_type": "Custom Method",
						"method": "ai_fr_hg.ai_automation.doctype.ai_pipeline.test_ai_pipeline.always_works",
						"output_field": "child_result",
						"enabled": 1,
					}
				],
			}
		).insert(ignore_permissions=True)
		parent = frappe.get_doc(
			{
				"doctype": "AI Pipeline",
				"pipeline_name": "Nested Parent Pipeline",
				"enabled": 1,
				"steps": [
					{
						"step_name": "Run Child",
						"step_type": "Pipeline",
						"sub_pipeline": child.name,
						"output_field": "nested",
						"enabled": 1,
					}
				],
			}
		).insert(ignore_permissions=True)

		parent_run = run_pipeline(parent.name, input_data={"seed": "value"}, enqueue_job=False)
		parent_run.reload()
		child_run = frappe.get_all(
			"AI Pipeline Run",
			filters={"parent_pipeline_run": parent_run.name, "pipeline": child.name},
			fields=["name", "status", "triggered_by"],
			limit=1,
		)[0]
		self.assertEqual(parent_run.status, "Completed")
		self.assertEqual(child_run.status, "Completed")
		self.assertEqual(child_run.triggered_by, frappe.session.user)
		self.assertIn("child_result", frappe.parse_json(parent_run.output_data)["nested"])

	def test_nested_pipeline_failure_is_persisted_in_child_and_parent(self):
		from ai_fr_hg.ai.pipeline import run_pipeline

		child = frappe.get_doc(
			{
				"doctype": "AI Pipeline",
				"pipeline_name": "Nested Failing Child Pipeline",
				"enabled": 1,
				"steps": [
					{
						"step_name": "Child Failure",
						"step_type": "Custom Method",
						"method": "ai_fr_hg.ai_automation.doctype.ai_pipeline.test_ai_pipeline.always_fails",
						"on_error": "Stop",
						"enabled": 1,
					}
				],
			}
		).insert(ignore_permissions=True)
		parent = frappe.get_doc(
			{
				"doctype": "AI Pipeline",
				"pipeline_name": "Nested Failing Parent Pipeline",
				"enabled": 1,
				"steps": [
					{
						"step_name": "Run Failing Child",
						"step_type": "Pipeline",
						"sub_pipeline": child.name,
						"on_error": "Stop",
						"enabled": 1,
					}
				],
			}
		).insert(ignore_permissions=True)

		parent_run = run_pipeline(parent.name, enqueue_job=False)
		child_run = frappe.get_doc(
			"AI Pipeline Run",
			frappe.db.get_value("AI Pipeline Run", {"parent_pipeline_run": parent_run.name}, "name"),
		)
		parent_run.reload()
		self.assertEqual(child_run.status, "Failed")
		self.assertEqual(parent_run.status, "Failed")
		self.assertIn("Intentional pipeline failure", child_run.error_message)
		self.assertIn(child.name, parent_run.error_message)
		for run in (parent_run, child_run):
			self.assertTrue(
				frappe.db.exists(
					"AI Audit Log",
					{
						"action": "Pipeline Run Failed",
						"reference_doctype": "AI Pipeline Run",
						"reference_name": run.name,
					},
				)
			)

	def test_nested_pipeline_preserves_tool_approval_request(self):
		from ai_fr_hg.ai.pipeline import run_pipeline

		tool = frappe.get_doc(
			{
				"doctype": "AI Tool",
				"tool_name": "nested_approval_tool",
				"tool_type": "DocType Action",
				"target_doctype": "ToDo",
				"enabled": 1,
				"requires_approval": 1,
				"max_runtime_seconds": 30,
				"description": "Create an approved ToDo from a nested pipeline.",
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
		child = frappe.get_doc(
			{
				"doctype": "AI Pipeline",
				"pipeline_name": "Nested Approval Child Pipeline",
				"enabled": 1,
				"steps": [
					{
						"step_name": "Request Governed Write",
						"step_type": "Tool",
						"tool": tool.name,
						"config": frappe.as_json(
							{
								"arguments": {
									"action": "create",
									"values": {"description": "Nested pipeline approved write"},
								}
							}
						),
						"on_error": "Stop",
						"enabled": 1,
					}
				],
			}
		).insert(ignore_permissions=True)
		parent = frappe.get_doc(
			{
				"doctype": "AI Pipeline",
				"pipeline_name": "Nested Approval Parent Pipeline",
				"enabled": 1,
				"steps": [
					{
						"step_name": "Run Approval Child",
						"step_type": "Pipeline",
						"sub_pipeline": child.name,
						"on_error": "Stop",
						"enabled": 1,
					}
				],
			}
		).insert(ignore_permissions=True)

		parent_run = run_pipeline(parent.name, enqueue_job=False)
		child_run = frappe.get_doc(
			"AI Pipeline Run",
			frappe.db.get_value("AI Pipeline Run", {"parent_pipeline_run": parent_run.name}, "name"),
		)
		invocation = frappe.get_doc(
			"AI Tool Invocation",
			frappe.db.get_value(
				"AI Tool Invocation", {"pipeline_run": child_run.name, "status": "Pending Approval"}, "name"
			),
		)
		parent_run.reload()
		self.assertEqual(child_run.status, "Failed")
		self.assertEqual(parent_run.status, "Failed")
		self.assertEqual(invocation.user, frappe.session.user)
		self.assertFalse(frappe.db.exists("ToDo", {"description": "Nested pipeline approved write"}))
		self.assertIn("requires approval", child_run.error_message)
		self.assertEqual(invocation.status, "Pending Approval")

	def test_configuration_rejects_indirect_nested_pipeline_cycle(self):
		pipeline_a = frappe.get_doc(
			{
				"doctype": "AI Pipeline",
				"pipeline_name": "Configuration Cycle Pipeline A",
				"enabled": 1,
				"steps": [
					{
						"step_name": "Initial Safe Work",
						"step_type": "Custom Method",
						"method": "ai_fr_hg.ai_automation.doctype.ai_pipeline.test_ai_pipeline.always_works",
						"enabled": 1,
					}
				],
			}
		).insert(ignore_permissions=True)
		pipeline_b = frappe.get_doc(
			{
				"doctype": "AI Pipeline",
				"pipeline_name": "Configuration Cycle Pipeline B",
				"enabled": 1,
				"steps": [
					{
						"step_name": "Call A",
						"step_type": "Pipeline",
						"sub_pipeline": pipeline_a.name,
						"enabled": 1,
					}
				],
			}
		).insert(ignore_permissions=True)
		pipeline_a.set("steps", [])
		pipeline_a.append(
			"steps",
			{
				"step_name": "Call B",
				"step_type": "Pipeline",
				"sub_pipeline": pipeline_b.name,
				"enabled": 1,
			},
		)
		with self.assertRaisesRegex(frappe.ValidationError, "dependency cycle"):
			pipeline_a.save(ignore_permissions=True)

	def test_runtime_rejects_cycle_in_parent_run_ancestry(self):
		from ai_fr_hg.ai.exceptions import PipelineError
		from ai_fr_hg.ai.pipeline import run_pipeline

		def make_pipeline(name):
			return frappe.get_doc(
				{
					"doctype": "AI Pipeline",
					"pipeline_name": name,
					"enabled": 1,
					"steps": [
						{
							"step_name": "Safe Work",
							"step_type": "Custom Method",
							"method": "ai_fr_hg.ai_automation.doctype.ai_pipeline.test_ai_pipeline.always_works",
							"enabled": 1,
						}
					],
				}
			).insert(ignore_permissions=True)

		pipeline_a = make_pipeline("Runtime Ancestry Pipeline A")
		pipeline_b = make_pipeline("Runtime Ancestry Pipeline B")
		run_a = frappe.get_doc(
			{
				"doctype": "AI Pipeline Run",
				"pipeline": pipeline_a.name,
				"status": "Running",
				"triggered_by": "Administrator",
			}
		).insert(ignore_permissions=True)
		run_b = frappe.get_doc(
			{
				"doctype": "AI Pipeline Run",
				"pipeline": pipeline_b.name,
				"status": "Running",
				"triggered_by": "Administrator",
				"parent_pipeline_run": run_a.name,
			}
		).insert(ignore_permissions=True)
		before = frappe.db.count("AI Pipeline Run", {"parent_pipeline_run": run_b.name})
		with self.assertRaisesRegex(PipelineError, "Recursive nested pipeline call"):
			run_pipeline(pipeline_a.name, enqueue_job=False, _parent_run=run_b.name)
		self.assertEqual(
			frappe.db.count("AI Pipeline Run", {"parent_pipeline_run": run_b.name}),
			before,
		)

	def test_summarize_pipeline_runs(self):
		from ai_fr_hg.ai.pipeline import run_pipeline

		pipeline = frappe.get_doc(
			{
				"doctype": "AI Pipeline",
				"pipeline_name": "Summarize Pipeline",
				"enabled": 1,
				"trigger_type": "Manual",
				"steps": [
					{
						"step_name": "Summarize",
						"step_type": "Summarize",
						"enabled": 1,
						"input_field": "content",
						"output_field": "summary",
					}
				],
			}
		).insert(ignore_permissions=True)

		with patch("ai_fr_hg.ai.intelligence.summarize", return_value="A short summary."):
			run = run_pipeline(
				pipeline.name,
				input_data={"content": "Some long text to summarise."},
				enqueue_job=False,
			)

		run.reload()
		self.assertEqual(run.status, "Completed")
		output = frappe.parse_json(run.output_data)
		self.assertEqual(output["summary"], "A short summary.")

	def test_failing_step_marks_run_failed(self):
		from ai_fr_hg.ai.pipeline import run_pipeline

		pipeline = frappe.get_doc(
			{
				"doctype": "AI Pipeline",
				"pipeline_name": "Failing Pipeline",
				"enabled": 1,
				"steps": [
					{
						"step_name": "Boom",
						"step_type": "Custom Method",
						"method": "ai_fr_hg.ai_automation.doctype.ai_pipeline.test_ai_pipeline.always_fails",
						"on_error": "Stop",
						"enabled": 1,
					}
				],
			}
		).insert(ignore_permissions=True)

		run = run_pipeline(pipeline.name, enqueue_job=False)
		run.reload()

		self.assertEqual(run.status, "Failed")
		self.assertTrue(run.error_message)
		self.assertEqual(run.step_logs[0].status, "Failed")

	def test_continue_on_error(self):
		from ai_fr_hg.ai.pipeline import run_pipeline

		pipeline = frappe.get_doc(
			{
				"doctype": "AI Pipeline",
				"pipeline_name": "Tolerant Pipeline",
				"enabled": 1,
				"steps": [
					{
						"step_name": "Boom",
						"step_type": "Custom Method",
						"method": "ai_fr_hg.ai_automation.doctype.ai_pipeline.test_ai_pipeline.always_fails",
						"on_error": "Continue",
						"enabled": 1,
					},
					{
						"step_name": "Fine",
						"step_type": "Custom Method",
						"method": "ai_fr_hg.ai_automation.doctype.ai_pipeline.test_ai_pipeline.always_works",
						"enabled": 1,
						"output_field": "ok",
					},
				],
			}
		).insert(ignore_permissions=True)

		run = run_pipeline(pipeline.name, enqueue_job=False)
		run.reload()

		self.assertEqual(run.status, "Completed")
		self.assertEqual(run.step_logs[0].status, "Failed")
		self.assertEqual(run.step_logs[1].status, "Success")


def unmarked_pipeline_method(context=None, step=None, config=None):
	return {"unsafe": True}


@pipeline_step_method
def always_fails(context=None, step=None, config=None):
	raise ValueError("This step always fails, by design.")


@pipeline_step_method
def always_works(context=None, step=None, config=None):
	return {"ok": True}
