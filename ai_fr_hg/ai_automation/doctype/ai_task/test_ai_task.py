# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

from unittest.mock import patch

import frappe

from ai_fr_hg.ai.tasks import task_method
from ai_fr_hg.tests.integration_test_case import AIPlatformTestCase


class TestAITaskContracts(AIPlatformTestCase):
	def make_user(self, email, roles):
		if frappe.db.exists("User", email):
			return frappe.get_doc("User", email)
		return frappe.get_doc(
			{
				"doctype": "User",
				"email": email,
				"first_name": email.split("@")[0],
				"send_welcome_email": 0,
				"roles": [{"role": role} for role in roles],
			}
		).insert(ignore_permissions=True)

	def make_task(self, **values):
		payload = {
			"doctype": "AI Task",
			"subject": values.pop("subject", "Task"),
			"task_type": values.pop("task_type", "Question"),
			"instruction": values.pop("instruction", "What is 2+2?"),
			**values,
		}
		return frappe.get_doc(payload).insert(ignore_permissions=True)

	def test_status_cannot_be_written_directly(self):
		task = self.make_task(subject="Direct Status")
		task.status = "Approved"
		with self.assertRaises(frappe.ValidationError):
			task.save(ignore_permissions=True)

	def test_user_cannot_self_approve(self):
		from ai_fr_hg.ai.tasks import approve_task, submit_task

		user = self.make_user("task-manager-owner@example.com", ["AI Manager"])
		frappe.set_user(user.name)
		try:
			task = self.make_task(subject="Self Approve", requires_approval=1)
			submit_task(task.name)
			with self.assertRaises(frappe.PermissionError):
				approve_task(task.name)
		finally:
			frappe.set_user("Administrator")

	def test_ai_user_cannot_approve(self):
		from ai_fr_hg.ai.tasks import approve_task, submit_task

		owner = self.make_user("task-owner@example.com", ["AI User"])
		other = self.make_user("task-other@example.com", ["AI User"])
		frappe.set_user(owner.name)
		try:
			task = self.make_task(subject="User Approve", requires_approval=1)
			submit_task(task.name)
		finally:
			frappe.set_user("Administrator")
		frappe.set_user(other.name)
		try:
			with self.assertRaises(frappe.PermissionError):
				approve_task(task.name)
		finally:
			frappe.set_user("Administrator")

	def test_compare_requires_two_documents(self):
		with self.assertRaises(frappe.ValidationError):
			self.make_task(subject="Bad Compare", task_type="Compare", instruction="compare")

	def test_compare_executes(self):
		from ai_fr_hg.ai.tasks import execute_task

		left = self.make_document("Left", "alpha")
		right = self.make_document("Right", "beta")
		task = self.make_task(
			subject="Compare Docs",
			task_type="Compare",
			instruction="Compare them",
			input_data=frappe.as_json({"document_a": left.name, "document_b": right.name}),
		)
		with patch("ai_fr_hg.ai.intelligence.compare_documents", return_value={"comparison": "same-ish"}):
			outcome = execute_task(task.name)
		task.reload()
		self.assertEqual(outcome["status"], "Completed")
		self.assertEqual(task.status, "Completed")
		self.assertIn("same-ish", task.result or "")

	def test_custom_method_contract(self):
		from ai_fr_hg.ai.tasks import execute_task

		task = self.make_task(
			subject="Custom Path",
			task_type="Custom",
			instruction="go",
			custom_method="ai_fr_hg.ai_automation.doctype.ai_task.test_ai_task.custom_ok",
		)
		outcome = execute_task(task.name)
		task.reload()
		self.assertEqual(outcome["status"], "Completed")
		self.assertIn("custom-ok", task.result or "")

	def test_classify_without_categories_fails_validation(self):
		with self.assertRaises(frappe.ValidationError):
			self.make_task(subject="No Cats", task_type="Classify", instruction="sort this")


@task_method
def custom_ok(task=None, payload=None):
	return {"result": "custom-ok"}
