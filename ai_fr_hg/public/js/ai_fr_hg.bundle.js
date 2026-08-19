// Copyright (c) 2026, Ai Fr Hg and contributors
// For license information, please see license.txt

/**
 * Bundle entry point. Registers the shared `frappe.ai` client helper used by
 * form scripts, list views and custom pages, plus the canonical folder selector.
 *
 * `desk_guard` is imported first so its global error and socket handlers are
 * active before any other Desk script runs.
 */

import "./desk_guard";
import "./ai_helpers";
import "./file_folder";
