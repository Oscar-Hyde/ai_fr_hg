# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Resource Marketplace service layer.

The package owns the *how* of every resource operation: discovery, download,
verification, dependency resolution, installation, activation, updates,
rollbacks, monitoring and removal. DocTypes in :mod:`ai_fr_hg.ai_resources`
are the durable storage/registry half; the whitelisted API in
:mod:`ai_fr_hg.api.resources` is the validation/permission half.
"""

from __future__ import annotations

# Public entry points used by the API and install hooks.
from ai_fr_hg.ai.resources.catalog import refresh_builtin_catalog  # noqa: F401
from ai_fr_hg.ai.resources.download import enqueue_download  # noqa: F401
from ai_fr_hg.ai.resources.lifecycle import uninstall_resource, update_resource  # noqa: F401

__all__ = ["refresh_builtin_catalog", "enqueue_download", "update_resource", "uninstall_resource"]
