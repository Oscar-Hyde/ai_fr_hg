// Copyright (c) 2026, Ai Fr Hg and contributors
// For license information, please see license.txt

/**
 * Shared Desk UI layer: route state, RPC errors, realtime lifecycle.
 * Loaded on every Desk boot via ai_fr_hg.bundle.js.
 */

import * as realtime from "./ui/realtime";
import * as rpc from "./ui/rpc";
import * as routeState from "./ui/route_state";

(() => {
	if (typeof frappe === "undefined" || typeof frappe.provide !== "function") return;
	frappe.provide("frappe.ai.ui");
	Object.assign(frappe.ai.ui, routeState, rpc, realtime);
})();
