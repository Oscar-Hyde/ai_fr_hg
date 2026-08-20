// Copyright (c) 2026, Ai Fr Hg and contributors
// For license information, please see license.txt

/**
 * Parse/serialize Assistant route state. No Frappe dependency so Node tests
 * can import this module directly (Phase 3 frontend architecture, §8.1).
 */

export function looksLikeConversationName(value) {
	if (!value || typeof value !== "string") return false;
	const text = value.trim();
	if (!text || text.length > 140) return false;
	if (/[/?#\s]/.test(text)) return false;
	return true;
}

function fromSearch(search) {
	if (!search) return "";
	const raw = String(search).startsWith("?") ? String(search).slice(1) : String(search);
	const params = new URLSearchParams(raw);
	return params.get("conversation") || params.get("c") || "";
}

function fromHash(hash) {
	if (!hash) return "";
	const raw = String(hash).startsWith("#") ? String(hash).slice(1) : String(hash);
	if (!raw) return "";
	if (raw.startsWith("conversation=") || raw.startsWith("c=")) {
		const params = new URLSearchParams(raw);
		return params.get("conversation") || params.get("c") || "";
	}
	return looksLikeConversationName(raw) ? raw : "";
}

/**
 * @param {{ route?: string[], routeOptions?: object, search?: string, hash?: string }} input
 * @returns {{ conversation: string | null }}
 */
export function parseAssistantRoute(input) {
	const source = input || {};
	const options = source.routeOptions || {};
	const fromOptions = options.conversation || options.name;
	if (looksLikeConversationName(fromOptions)) {
		return { conversation: String(fromOptions).trim() };
	}

	const parts = Array.isArray(source.route) ? source.route : [];
	for (const part of parts.slice(1)) {
		if (looksLikeConversationName(part) && String(part).toLowerCase() !== "ai-assistant") {
			return { conversation: String(part).trim() };
		}
	}

	const queryName = fromSearch(source.search);
	if (looksLikeConversationName(queryName)) {
		return { conversation: queryName.trim() };
	}

	const hashName = fromHash(source.hash);
	if (looksLikeConversationName(hashName)) {
		return { conversation: hashName.trim() };
	}

	return { conversation: null };
}

export function serializeAssistantHash(state) {
	const conversation = state && state.conversation;
	if (!looksLikeConversationName(conversation)) return "";
	return `#c=${encodeURIComponent(conversation)}`;
}
