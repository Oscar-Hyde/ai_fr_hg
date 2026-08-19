# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Fix the module assignment for learning DocTypes.

The DocTypes ``AI Knowledge Candidate``, ``AI Memory`` and ``AI Skill`` were
originally created with ``module = "Core"`` on some sites, which makes Frappe
resolve their controller under ``frappe.core.doctype`` instead of
``ai_fr_hg.ai_learning.doctype``, causing ``frappe.new_doc`` to fail with::

    ImportError: No module named 'frappe.core.doctype.ai_knowledge_candidate'

The JSON files already declare ``"module": "AI Learning"``.  This patch
corrects the database records that missed the update.
"""

import frappe

LEARNING_DOCTYPES = ("AI Knowledge Candidate", "AI Memory", "AI Skill")
CORRECT_MODULE = "AI Learning"


def execute():
	for doctype in LEARNING_DOCTYPES:
		current = frappe.db.get_value("DocType", doctype, "module")
		if current and current != CORRECT_MODULE:
			frappe.db.set_value("DocType", doctype, "module", CORRECT_MODULE)
