# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Detect local AI runtime artifacts on disk.

The marketplace must not require a second download when a model, embedding
index or language artifact already exists on the machine. This module scans
the standard bench runtime directories (``services/ollama/models`` and
``services/qdrant/storage``), records the discovered AI Model entries, and
marks matching marketplace resources as Ready/Installed automatically.

It is intentionally tolerant: missing directories or a partially scanned tree
never blocks the marketplace payload.
"""

from __future__ import annotations

import json
from pathlib import Path

import frappe
from frappe import _
from frappe.utils import cint, flt, now_datetime

#: Engines the local scanner knows how to inspect.
KNOWN_MODEL_NAMES = {
	"all-minilm",
	"llama3.1",
	"llava",
	"mistral",
	"mxbai-embed-large",
	"nomic-embed-text",
	"phi3",
	"qwen2.5",
}


def bench_root() -> Path:
	"""Likely bench root containing the ``services`` directory."""
	site = Path(frappe.get_site_path()).resolve()
	if site.parent.name == "sites":
		return site.parent.parent
	if site.name == "sites":
		return site.parent
	return site.parents[2] if len(site.parents) > 2 else site


def services_root() -> Path:
	return bench_root() / "services"


def ollama_dir() -> Path:
	return services_root() / "ollama"


def ollama_manifests_dir() -> Path:
	return ollama_dir() / "models" / "manifests" / "registry.ollama.ai"


def ollama_blobs_dir() -> Path:
	return ollama_dir() / "models" / "blobs"


def qdrant_dir() -> Path:
	return services_root() / "qdrant"


def qdrant_collections_dir() -> Path:
	return qdrant_dir() / "storage" / "collections"


def discover_ollama_bundles() -> list[dict]:
	"""Read Ollama manifests from disk and return model metadata."""
	root = ollama_manifests_dir()
	if not root.exists():
		return []
	bundles = []
	for manifest in sorted(root.rglob("*/[!_]*")):
		if not manifest.is_file():
			continue
		try:
			name = manifest.parent.parent.name
			tag = manifest.name
			namespace = manifest.parent.name
		except IndexError:
			continue
		if not name or not tag or namespace not in ("library",):
			continue
		model_name = f"{name}:{tag}"
		metadata = _read_ollama_manifest(manifest)
		bundles.append(
			{
				"model_name": model_name,
				"raw_name": name,
				"tag": tag,
				"namespace": namespace,
				"source_path": str(manifest),
				"digest": metadata.get("digest") or "",
				"size_bytes": cint(metadata.get("size_bytes")),
				"family": metadata.get("family") or name,
				"parameter_size": metadata.get("parameter_size") or "",
				"quantization": metadata.get("quantization") or "",
				"context_window": cint(metadata.get("context_window") or metadata.get("context_length") or 8192),
				"model_type": metadata.get("model_type") or _guess_local_type(name),
			}
		)
	return bundles


def _read_ollama_manifest(path: Path) -> dict:
	"""Best-effort read of an Ollama manifest + config blob."""
	try:
		manifest = json.loads(path.read_text(encoding="utf-8"))
	except Exception:
		return {}
	config_digest = ""
	config_size = 0
	for layer in manifest.get("config") or []:
		if isinstance(layer, dict):
			config_digest = layer.get("digest") or ""
			config_size = cint(layer.get("size"))
			break
	config = _read_ollama_config(config_digest)
	if config.get("size") is None:
		config["size"] = config_size
	config["digest"] = manifest.get("digest") or ""
	return config


def _read_ollama_config(digest: str) -> dict:
	"""Read and parse the config blob referenced by a manifest."""
	if not digest:
		return {}
	filename = digest.replace(":", "-")
	path = ollama_blobs_dir() / filename
	if not path.exists():
		return {}
	try:
		return json.loads(path.read_text(encoding="utf-8"))
	except Exception:
		return {}


def _guess_local_type(name: str) -> str:
	lowered = (name or "").lower()
	if any(token in lowered for token in ("embed", "bge", "gte", "e5-", "minilm", "nomic", "mxbai")):
		return "Embedding"
	if any(token in lowered for token in ("llava", "vision", "-vl", "bakllava", "minicpm-v")):
		return "Vision"
	return "Chat"


def discover_qdrant_collections() -> list[dict]:
	"""Return Qdrant collection directories on disk."""
	root = qdrant_collections_dir()
	if not root.exists():
		return []
	result = []
	for collection in sorted(root.iterdir()):
		if not collection.is_dir():
			continue
		config_path = collection / "config.json"
		config = {}
		if config_path.exists():
			try:
				config = json.loads(config_path.read_text(encoding="utf-8"))
			except Exception:
				config = {}
		segment_count = 0
		for segment_dir in (collection / "0" / "segments").glob("*"):
			if segment_dir.is_dir():
				segment_count += 1
		result.append(
			{
				"collection_name": collection.name,
				"source_path": str(collection),
				"config": config,
				"segment_count": segment_count,
				"ready": (collection / "0" / "segments").exists(),
			}
		)
	return result


def local_runtime_summary() -> dict:
	"""Read-only summary for the marketplace header / detection banner."""
	models = discover_ollama_bundles()
	collections = discover_qdrant_collections()
	registered_models = _registered_local_model_names()
	return {
		"ollama_model_count": len(models),
		"ollama_models": models[:100],
		"qdrant_collection_count": len(collections),
		"qdrant_collections": collections[:20],
		"registered_local_models": registered_models,
		"detected": bool(models or collections),
	}


def register_local_models(user: str | None = None, *, quiet: bool = False) -> dict:
	"""Register discovered local runtime artifacts and mark matching resources Ready.

	This is the one-click "recognize what is already on the machine" operation.
	It is idempotent: existing AI Model records are updated, missing ones are
	created, and marketplace resources whose payload matches a discovered model
	are marked Installed/Healthy so the UI shows Ready without another download.
	"""
	user = user or frappe.session.user
	models = discover_ollama_bundles()
	collections = discover_qdrant_collections()

	provider = _runtime_provider()
	created, updated = [], []
	for model in models:
		record = _upsert_local_model(provider, model)
		(target := (created if record.get("created") else updated)) and record.get("name") and target.append(record.get("name"))

	marketplace_ready = _mark_marketplace_models_ready(models, user=user)

	_qdrant_index_ready(collections)

	result = {
		"provider": provider,
		"discovered_models": len(models),
		"created_models": created,
		"updated_models": updated,
		"marketplace_ready": marketplace_ready,
		"qdrant_collections_detected": len(collections),
		"user": user,
		"timestamp": now_datetime().isoformat(),
	}
	if not quiet:
		frappe.db.commit()  # nosemgrep: frappe-manual-commit
	return result


def _runtime_provider() -> str:
	"""Best provider for local registration: enabled Ollama, else Local Ollama."""
	provider = frappe.db.get_value(
		"AI Provider",
		{"enabled": 1, "provider_type": "Ollama"},
		"name",
		order_by="is_default desc, priority asc, creation asc",
	)
	return provider or "Local Ollama"


def _upsert_local_model(provider: str, model: dict) -> dict:
	"""Create or update an AI Model record from a disk-detected bundle."""
	from ai_fr_hg.ai.monitoring import _guess_model_type

	model_type = model.get("model_type") or _guess_model_type(model["model_name"])
	label = f"{model['model_name']} ({provider})"
	existing = frappe.db.get_value(
		"AI Model",
		{"model_name": model["model_name"], "provider": provider},
		"name",
	)
	created = False
	if existing:
		record = frappe.get_doc("AI Model", existing)
		record.update(
			{
				"status": "Available",
				"last_checked": now_datetime(),
				"last_error": None,
				"digest": model.get("digest") or "",
				"size_bytes": cint(model.get("size_bytes")),
				"family": model.get("family") or "",
				"parameter_size": model.get("parameter_size") or "",
				"quantization": model.get("quantization") or "",
				"context_window": cint(model.get("context_window") or record.context_window or 8192),
				"model_type": model_type,
				"enabled": 1,
			}
		)
		record.flags.ignore_permissions = True
		record.save(ignore_permissions=True)
	else:
		record = frappe.new_doc("AI Model")
		record.update(
			{
				"model_label": label,
				"provider": provider,
				"model_name": model["model_name"],
				"model_type": model_type,
				"family": model.get("family") or "",
				"parameter_size": model.get("parameter_size") or "",
				"quantization": model.get("quantization") or "",
				"context_window": cint(model.get("context_window") or 8192),
				"digest": model.get("digest") or "",
				"size_bytes": cint(model.get("size_bytes")),
				"status": "Available",
				"last_checked": now_datetime(),
				"enabled": 1,
				"supports_streaming": 1,
				"supports_json_mode": 1 if model_type != "Embedding" else 0,
			}
		)
		record.flags.ignore_permissions = True
		record.insert(ignore_permissions=True)
		created = True
	return {"name": record.name, "created": created}


def _mark_marketplace_models_ready(discovered: list[dict], user: str) -> list[dict]:
	"""Mark catalog AI Model resources Ready when their payload model is present."""
	ready = []
	for resource in frappe.get_all("AI Resource", filters={"resource_type": "AI Model", "enabled": 1}, fields=["name", "resource_code", "resource_name", "version", "publisher", "repository"], limit=100):
		manifest = _resource_payload(resource.resource_code)
		payload = (manifest or {}).get("payload", {}).get("models") or []
		matched = []
		for model in payload:
			model_name = model.get("model_name") if isinstance(model, dict) else model
			if any(item.get("model_name") == model_name for item in discovered):
				matched.append(model_name)
		if not matched:
			continue
		target_records = _find_model_targets(matched)
		if not target_records:
			continue
		ready.append(
			_mark_install_ready(
				resource,
				user=user,
				target_records=target_records,
				note=", ".join(matched),
			)
		)
	return ready


def _resource_payload(resource_code: str) -> dict:
	try:
		from ai_fr_hg.ai.resources.paths import bundle_path

		path = bundle_path(resource_code)
		if path.exists():
			return json.loads(path.read_text(encoding="utf-8"))
	except Exception:
		return {}
	return {}


def _find_model_targets(model_names: list[str]) -> list[dict]:
	targets = []
	current_provider = _runtime_provider()
	for model_name in model_names:
		record = frappe.db.get_value(
			"AI Model",
			{"model_name": model_name, "provider": current_provider},
			"name",
		)
		if record:
			targets.append({"doctype": "AI Model", "name": record})
	return targets


def _mark_install_ready(resource: dict, *, user: str, target_records: list[dict], note: str) -> dict:
	"""Idempotently create/retain an Active + Healthy install for a detected model."""
	active = frappe.get_all(
		"AI Resource Install",
		filters={"resource": resource["name"], "is_active": 1, "status": "Active"},
		fields=["name", "health_status"],
		limit=1,
	)
	now = now_datetime()
	if active:
		install = frappe.get_doc("AI Resource Install", active[0].name)
		install.health_status = "Healthy"
		install.last_checked = now
		install.flags.ignore_permissions = True
		install.save(ignore_permissions=True)
		return {"resource": resource["resource_code"], "status": "Ready", "install": install.name}

	install = frappe.new_doc("AI Resource Install")
	install.update(
		{
			"resource": resource["name"],
			"resource_code": resource["resource_code"],
			"resource_name": resource["resource_name"],
			"resource_type": "AI Model",
			"version": resource.get("version") or "detected",
			"publisher": resource.get("publisher") or "",
			"repository": resource.get("repository") or "",
			"status": "Active",
			"is_active": 1,
			"activated": 1,
			"activated_on": now,
			"installed_on": now,
			"installed_by": user,
			"health_status": "Healthy",
			"last_checked": now,
			"target_records": json.dumps(target_records, default=str),
		}
	)
	install.flags.ignore_permissions = True
	install.insert(ignore_permissions=True)

	from ai_fr_hg.ai.resources.lifecycle import write_event_for_install

	write_event_for_install(install.name, "Ready", _("Detected on local runtime ({0}).").format(note), user=user)
	return {"resource": resource["resource_code"], "status": "Ready", "install": install.name}


def _qdrant_index_ready(collections: list[dict]) -> list[dict]:
	"""Record Qdrant vector index health against matching AI Knowledge Bases."""
	synced = []
	for collection in collections:
		name = collection.get("collection_name") or ""
		# Collection names are opaque (e.g. file_analysis_site1_local_all_minilm_384);
		# map them back to the embedding model they represent when unambiguous.
		model_name = _embedding_model_from_collection(name)
		if model_name and frappe.db.exists("AI Model", {"model_name": model_name}):
			model = frappe.get_value("AI Model", {"model_name": model_name}, "name")
			synced.append({"collection": name, "model": model, "ready": bool(collection.get("ready"))})
	return synced


def _embedding_model_from_collection(collection: str) -> str:
	lowered = (collection or "").lower()
	# The collection name usually ends with the model slug. Preserve the full
	# "family:tag" only when it is derivable; otherwise return the raw slug.
	for token in ("all-minilm", "nomic-embed", "mxbai-embed", "minilm", "nomic"):
		if token in lowered:
			return token + ":latest"
	return ""


def _registered_local_model_names() -> list[str]:
	try:
		ids = frappe.get_all("AI Model", filters={"status": ("in", ("Available", "Healthy"))}, pluck="model_name")
	except Exception:
		return []
	return sorted(set(ids or []))
