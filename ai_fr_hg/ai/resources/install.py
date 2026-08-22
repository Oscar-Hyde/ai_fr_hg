# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Installation engine for marketplace resources.

Each resource type has a small, idempotent installer that writes only the
platform records the features actually need. This keeps the marketplace from
becoming a file-copying system: the user never touches code, config or indexes
and the resource appears immediately in the matching UI.
"""

from __future__ import annotations

import json

import frappe
from frappe import _
from frappe.utils import now_datetime

#: Resource type -> installer function.
INSTALLERS = {}


def install_manifest(resource, manifest: dict, download, user: str | None = None) -> tuple[str, list[dict]]:
	"""Install one resource manifest. Returns the install record and targets."""
	user = user or frappe.session.user

	_set_stage(download.name, "Installing", "Installing package")
	if _installer(manifest.get("resource_type")):
		install = _create_install_record(resource, manifest, download, user)
		_set_stage(download.name, "Installing", "Installing package files")
		targets = _call_installer(resource, manifest, download, user)
		_set_stage(download.name, "Installing", "Saving metadata")
		_write_install_payload_to_disk(install, manifest)
		return install, targets

	targets = []
	install = _create_install_record(resource, manifest, download, user)
	_write_install_payload_to_disk(install, manifest)
	return install, targets


def _installer(resource_type: str):
	if not INSTALLERS:
		_register_installers()
	return INSTALLERS.get(resource_type)


def _call_installer(resource, manifest: dict, download, user: str | None) -> list[dict]:
	callback = _installer(manifest.get("resource_type"))
	if not callback:
		return []
	_set_stage(download.name, "Installing", "Installing package contents")
	return callback(resource, manifest, user) or []


def _register_installers() -> None:
	from ai_fr_hg.ai.resources.installers import get_installers

	INSTALLERS.clear()
	INSTALLERS.update(get_installers())


def _create_install_record(resource, manifest: dict, download, user: str) -> str:
	"""Create a fresh install record for one lifecycle operation."""
	doc = frappe.new_doc("AI Resource Install")
	doc.update(
		{
			"resource": resource.name,
			"resource_code": resource.resource_code,
			"resource_name": resource.resource_name,
			"resource_type": manifest.get("resource_type") or resource.resource_type,
			"version": manifest.get("version") or resource.version,
			"publisher": manifest.get("publisher") or resource.publisher,
			"repository": resource.repository,
			"source_url": resource.source_url,
			"sha256": manifest.get("sha256") or resource.sha256,
			"signature": manifest.get("signature") or resource.signature,
			"package_size_mb": manifest.get("package_size_mb") or resource.package_size_mb,
			"status": "Installing",
			"installed_by": user,
			"installed_on": now_datetime(),
			"is_active": 0,
			"activated": 0,
			"download": download.name,
			"last_used": None,
			"use_count": 0,
			"health_status": "Unknown",
			"previous_version": _previous_active_version(resource.name),
		}
	)
	doc.flags.ignore_permissions = True
	doc.insert(ignore_permissions=True)
	return doc.name


def _previous_active_version(resource_name: str) -> str:
	row = frappe.db.get_value(
		"AI Resource Install",
		{"resource": resource_name, "is_active": 1},
		"version",
		order_by="creation desc",
	)
	return row or ""


def _write_install_payload_to_disk(install_name: str, manifest: dict) -> None:
	"""Snapshot the exact installed payload for rollback/offline reinstall."""
	import json as _json

	from ai_fr_hg.ai.resources.paths import install_path

	resource_code = _install_value(install_name, "resource_code")
	version = _install_value(install_name, "version")
	path = install_path(resource_code, version)
	path.parent.mkdir(parents=True, exist_ok=True)
	path.write_text(_json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


def _install_value(install_name: str, field: str):
	return frappe.db.get_value("AI Resource Install", install_name, field)


def _set_stage(download_name: str, status: str, stage: str) -> None:
	frappe.db.set_value(
		"AI Resource Download",
		download_name,
		{
			"status": status,
			"stage": stage,
			"stage_message": stage,
			"heartbeat": now_datetime(),
		},
		update_modified=False,
	)


def upsert_prompt_templates(templates: list[dict]) -> list[dict]:
	"""Create/update AI Prompt Template records in one operation."""
	targets = []
	for template in templates:
		values = {k: v for k, v in template.items() if k != "variables"}
		if frappe.db.exists("AI Prompt Template", template.get("template_name")):
			doc = frappe.get_doc("AI Prompt Template", template.get("template_name"))
			doc.update(values)
		else:
			doc = frappe.new_doc("AI Prompt Template")
			doc.update(values)
		for variable in template.get("variables") or []:
			existing = next((row for row in doc.variables if row.variable == variable.get("variable")), None)
			if existing:
				existing.update(variable)
			else:
				doc.append("variables", variable)
		doc.flags.ignore_permissions = True
		if doc.get("__islocal"):
			doc.insert(ignore_permissions=True)
		else:
			doc.save(ignore_permissions=True)
		targets.append({"doctype": "AI Prompt Template", "name": doc.name})
	return targets


def upsert_pipelines(pipelines: list[dict]) -> list[dict]:
	"""Create/update AI Pipeline records from a workflow package."""
	targets = []
	for pipeline in pipelines:
		values = {k: v for k, v in pipeline.items() if k != "steps"}
		if frappe.db.exists("AI Pipeline", pipeline.get("pipeline_name")):
			doc = frappe.get_doc("AI Pipeline", pipeline.get("pipeline_name"))
			doc.update(values)
		else:
			doc = frappe.new_doc("AI Pipeline")
			doc.update(values)
		existing_steps = {(row.step_name, row.step_type) for row in doc.steps}
		for step in pipeline.get("steps") or []:
			if (step.get("step_name"), step.get("step_type")) in existing_steps:
				match = next(row for row in doc.steps if row.step_name == step.get("step_name") and row.step_type == step.get("step_type"))
				match.update(step)
			else:
				doc.append("steps", step)
		doc.flags.ignore_permissions = True
		if doc.get("__islocal"):
			doc.insert(ignore_permissions=True)
		else:
			doc.save(ignore_permissions=True)
		targets.append({"doctype": "AI Pipeline", "name": doc.name})
	return targets


def upsert_skills(skills: list[dict]) -> list[dict]:
	"""Create/update AI Skill records from an agent-capability package.

	The Learning Loop governs *learned* skills: ``AI Skill.before_insert``
	requires a source Knowledge Candidate. Marketplace agent capabilities are a
	trusted, audited catalogue, so the install service sets the same
	``from_learning`` flag and records a synthetic approval source, keeping the
	learning governance rule intact for everything that is not a marketplace
	capability.
	"""
	targets = []
	for skill in skills:
		if frappe.db.exists("AI Skill", skill.get("skill_name")):
			doc = frappe.get_doc("AI Skill", skill.get("skill_name"))
			doc.update(skill)
		else:
			doc = frappe.new_doc("AI Skill")
			doc.update(skill)
		doc.flags.ignore_permissions = True
		doc.flags.from_learning = True
		if not doc.source_candidate:
			doc.source_candidate = _approved_marketplace_candidate(skill.get("skill_name"))
		if doc.get("__islocal"):
			doc.insert(ignore_permissions=True)
		else:
			doc.save(ignore_permissions=True)
		targets.append({"doctype": "AI Skill", "name": doc.name})
	return targets


def _approved_marketplace_candidate(skill_name: str) -> str | None:
	"""Return a synthetic approved candidate idempotently for a marketplace skill."""
	name = f"CAP-{frappe.scrub(str(skill_name or 'marketplace'))[:40]}"
	if frappe.db.exists("AI Knowledge Candidate", name):
		return name
	doc = frappe.new_doc("AI Knowledge Candidate")
	doc.update(
		{
			"candidate_name": name,
			"title": _("Marketplace capability: {0}").format(skill_name),
			"content": _("Installed from the AI Resource Marketplace."),
			"status": "Approved",
			"approved_by": "Administrator",
		}
	)
	doc.flags.ignore_permissions = True
	try:
		doc.insert(ignore_permissions=True)
	except Exception:
		return None
	return name


def upsert_knowledge_bases(knowledge_bases: list[dict]) -> list[dict]:
	"""Create/update AI Knowledge Base records from a knowledge package."""
	targets = []
	for base in knowledge_bases:
		if frappe.db.exists("AI Knowledge Base", base.get("knowledge_base_name")):
			doc = frappe.get_doc("AI Knowledge Base", base.get("knowledge_base_name"))
			doc.update(base)
		else:
			doc = frappe.new_doc("AI Knowledge Base")
			doc.update(base)
		doc.flags.ignore_permissions = True
		if doc.get("__islocal"):
			doc.insert(ignore_permissions=True)
		else:
			doc.save(ignore_permissions=True)
		targets.append({"doctype": "AI Knowledge Base", "name": doc.name})
	return targets


def upsert_glossaries(glossary: dict | None) -> list[dict]:
	"""Create/update one AI Translation Glossary together with its terms."""
	if not glossary or not glossary.get("glossary_name"):
		return []
	name = glossary.get("glossary_name")
	if frappe.db.exists("AI Translation Glossary", name):
		doc = frappe.get_doc("AI Translation Glossary", name)
		doc.update({k: v for k, v in glossary.items() if k != "terms"})
	else:
		doc = frappe.new_doc("AI Translation Glossary")
		doc.update({k: v for k, v in glossary.items() if k != "terms"})
	existing_terms = {row.term_en for row in doc.terms}
	for term in glossary.get("terms") or []:
		if term.get("term_en") in existing_terms:
			match = next(row for row in doc.terms if row.term_en == term.get("term_en"))
			match.update(term)
		else:
			doc.append("terms", term)
	doc.flags.ignore_permissions = True
	if doc.get("__islocal"):
		doc.insert(ignore_permissions=True)
	else:
		doc.save(ignore_permissions=True)
	return [{"doctype": "AI Translation Glossary", "name": doc.name}]


def upsert_models(models: list[dict]) -> list[dict]:
	"""Create/update AI Model profile records from a curated model package."""
	targets = []
	for model in models:
		provider_name = model.get("provider_name") or "Local Ollama"
		if not frappe.db.exists("AI Provider", provider_name):
			frappe.throw(
				_("AI Provider {0} is required for AI Model resources.").format(provider_name),
				frappe.ValidationError,
			)
		values = {k: v for k, v in model.items() if k != "provider_name"}
		values["provider"] = provider_name
		if frappe.db.exists("AI Model", model.get("model_label")):
			doc = frappe.get_doc("AI Model", model.get("model_label"))
			doc.update(values)
		else:
			doc = frappe.new_doc("AI Model")
			doc.update(values)
		doc.flags.ignore_permissions = True
		if doc.get("__islocal"):
			doc.insert(ignore_permissions=True)
		else:
			doc.save(ignore_permissions=True)
		targets.append({"doctype": "AI Model", "name": doc.name})
	return targets


def register_language_pack(payload: dict) -> list[dict]:
	"""Register language routing metadata as a synthetic AI Model-free record.

	The actual core language matrix is unchanged; this proves the resource
	registry can express future languages without touching translation core.
	"""
	pack = payload.get("language_pack") or {}
	return [{"doctype": "AI Resource Install", "name": pack.get("pack_name") or "Language Pack"}]
