# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Filesystem paths for resources, bundles and downloaded packages."""

from __future__ import annotations

from pathlib import Path

import frappe

#: The directory that holds the signed, versioned resource bundles shipped with
#: this app. They are read on demand by the download engine.
BUNDLES_DIR_NAME = "bundles"
#: Registry of a single resource's downloaded package files.
DOWNLOADS_DIR_NAME = "ai_resource_downloads"
#: Installed package files (snapshotted bundles for rollback / offline export).
INSTALLS_DIR_NAME = "ai_resource_installs"


def bundles_dir() -> Path:
	"""Directory containing the built-in package JSON files."""
	return Path(Path(__file__).resolve().parent) / BUNDLES_DIR_NAME


def bundle_path(resource_code: str) -> Path:
	"""The bundle file for a built-in resource.

	Only uses the basename to guarantee a path traversal cannot escape the
	bundle directory.
	"""
	from ai_fr_hg.ai.resources.catalog import expand_resource_code

	return bundles_dir() / f"{expand_resource_code(resource_code)}.json"


def downloads_dir() -> Path:
	"""Site-private directory used for in-flight download files."""
	path = Path(frappe.get_site_path("private", "files", DOWNLOADS_DIR_NAME))
	path.mkdir(parents=True, exist_ok=True)
	return path


def download_path(resource_code: str) -> Path:
	"""Private path for the currently downloaded resource package."""
	from ai_fr_hg.ai.resources.catalog import expand_resource_code

	return downloads_dir() / f"{expand_resource_code(resource_code)}.json"


def installs_dir() -> Path:
	"""Site-private directory for installed package snapshots."""
	path = Path(frappe.get_site_path("private", "files", INSTALLS_DIR_NAME))
	path.mkdir(parents=True, exist_ok=True)
	return path


def install_path(resource_code: str, version: str) -> Path:
	"""The snapshot path for an installed resource version."""
	from ai_fr_hg.ai.resources.catalog import expand_resource_code

	safe_version = "".join(character for character in str(version or "0") if character.isalnum() or character in "._-")
	return installs_dir() / f"{expand_resource_code(resource_code)}-{safe_version}.json"
