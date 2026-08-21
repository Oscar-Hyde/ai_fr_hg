# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Idempotent Wave 4 backfill for durable extraction evidence on AI Document."""

from __future__ import annotations

import frappe


def execute():
	if not frappe.db.table_exists("AI Document"):
		return
	if not frappe.db.has_column("AI Document", "extraction_evidence"):
		frappe.db.sql("ALTER TABLE `tabAI Document` ADD COLUMN `extraction_evidence` longtext")
	frappe.db.sql(
		"""
		update `tabAI Document`
		set extraction_evidence = '{}'
		where extraction_evidence is null or extraction_evidence = ''
		"""
	)
