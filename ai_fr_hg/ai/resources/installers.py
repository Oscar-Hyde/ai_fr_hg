# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Concrete resource installers keyed by resource type."""

from __future__ import annotations

import frappe

from ai_fr_hg.ai.resources.catalog import expand_resource_code
from ai_fr_hg.ai.resources.install import (
	upsert_glossaries,
	upsert_knowledge_bases,
	upsert_models,
	upsert_pipelines,
	upsert_prompt_templates,
	upsert_skills,
)


def get_installers() -> dict[str, callable]:
	"""Return resource-type -> installer callable map."""
	installer = lambda _resource, manifest, _user: upsert_glossaries(  # noqa: E731
		manifest.get("payload", {}).get("glossary")
	)
	translation_memory = lambda _resource, manifest, _user: upsert_glossaries(  # noqa: E731
		manifest.get("payload", {}).get("glossary")
	)
	return {
		"Translation Package": installer,
		"Translation Memory Pack": translation_memory,
		"AI Prompt Template": lambda _resource, manifest, _user: upsert_prompt_templates(
			manifest.get("payload", {}).get("templates") or []
		),
		"AI Workflow Template": lambda _resource, manifest, _user: upsert_pipelines(
			manifest.get("payload", {}).get("pipelines") or []
		),
		"Agent Capability": lambda _resource, manifest, _user: upsert_skills(manifest.get("payload", {}).get("skills") or []),
		"Knowledge Resource": lambda _resource, manifest, _user: upsert_knowledge_bases(
			manifest.get("payload", {}).get("knowledge_bases") or []
		),
		"AI Model": lambda _resource, manifest, _user: upsert_models(manifest.get("payload", {}).get("models") or []),
		"Language Pack": lambda _resource, manifest, _user: _register_language_pack(manifest),
	}


def _register_language_pack(manifest: dict) -> list[dict]:
	"""A language pack is registration-only in this version.

	It records the pack's languages in the install payload so future engines
	can activate them, without assuming the core translation matrix supports
	locales it does not (currently Arabic / English / Hebrew only).
	"""
	payload = manifest.get("payload", {}).get("language_pack") or {}
	languages = payload.get("languages") or []
	notes = payload.get("notes") or "Registration only."
	frappe.cache().set_value(
		"ai_language_pack:" + expand_resource_code(manifest.get("resource_code")),
		{"languages": languages, "notes": notes, "pack": payload.get("pack_name")},
	)
	return []
