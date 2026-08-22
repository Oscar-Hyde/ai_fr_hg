# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Marketplace monitoring, usage aggregation and smart recommendations."""

from __future__ import annotations

import frappe
from frappe.utils import cint


def resource_summary() -> dict:
	"""Headline counts for the marketplace dashboard."""
	return {
		"installed_count": frappe.db.count("AI Resource Install", {"is_active": 1, "status": "Active"}),
		"updates_available": frappe.db.count("AI Resource Install", {"is_active": 1, "status": "Update Available"}),
		"active_downloads": frappe.db.count(
			"AI Resource Download",
			{"status": ("in", ("Queued", "Preparing", "Downloading", "Verifying", "Installing", "Registering", "Activating"))},
		),
		"catalog_count": frappe.db.count("AI Resource", {"enabled": 1}),
	}


def recommendations(limit: int = 6) -> list[dict]:
	"""Rank available resources by likely value, using usage and gaps.

	Ranks resources that are not installed, are compatible, and either share a
	resource type with heavy usage or provide currently missing capabilities.
	"""
	installed_map = _installed_map()
	rows = frappe.get_all(
		"AI Resource",
		filters={"enabled": 1},
		fields=["name", "resource_code", "resource_name", "resource_type", "category", "version", "description"],
		limit=500,
	)

	usage_by_type = _usage_by_resource_type()
	scores = []
	for row in rows:
		if row["resource_code"] in installed_map:
			continue
		if frappe.db.exists(
			"AI Resource Download",
			{"resource": row["name"], "status": ("in", ("Preparing", "Downloading", "Verifying", "Installing"))},
		):
			continue
		base = 0.4
		base += min(0.5, usage_by_type.get(row["resource_type"], 0) * 0.05)
		if row["resource_type"] in ("Translation Package", "AI Prompt Template"):
			base += 0.15
		scores.append((base, row))

	scores.sort(key=lambda pair: pair[0], reverse=True)
	results = []
	for _score, row in scores[:limit]:
		row["score"] = round(_score, 2)
		row["installed"] = False
		results.append(row)
	return results


def usage_metrics(resource_code: str | None = None) -> list[dict]:
	"""Usage counters and health for installed resources."""
	filters = {"is_active": 1}
	if resource_code:
		filters["resource_code"] = resource_code
	rows = frappe.get_all(
		"AI Resource Install",
		filters=filters,
		fields=["name", "resource_code", "resource_type", "version", "use_count", "last_used", "health_status", "installed_on"],
		order_by="use_count desc",
		limit=100,
	)
	for row in rows:
		row["usage_grade"] = _usage_grade(cint(row.get("use_count")))
		row["active"] = row.get("status") != "Update Available"
	return rows


def _installed_map() -> dict:
	rows = frappe.get_all(
		"AI Resource Install", filters={"is_active": 1, "status": "Active"}, fields=["resource_code"], limit=500
	)
	return {row["resource_code"]: True for row in rows}


def _usage_by_resource_type() -> dict:
	rows = frappe.get_all("AI Resource Install", fields=["resource_type", "use_count"], limit=500)
	result = {}
	for row in rows:
		result[row["resource_type"]] = result.get(row["resource_type"], 0) + cint(row.get("use_count"))
	return result


def _usage_grade(use_count: int) -> str:
	if use_count >= 100:
		return "Excellent"
	if use_count >= 25:
		return "Good"
	if use_count >= 1:
		return "Fair"
	return "Unused"
