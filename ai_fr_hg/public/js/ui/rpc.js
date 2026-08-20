// Copyright (c) 2026, Ai Fr Hg and contributors
// For license information, please see license.txt

/**
 * Shared RPC error normalisation. Pure enough for Node tests; Desk wraps it
 * with frappe.xcall / frappe.call.
 */

export const GATEWAY_TIMEOUT_MESSAGE =
	"The model did not answer in time and the connection timed out. Local models are slowest on their first run — try again, or pick a smaller model.";

export function rpcStatus(error) {
	if (!error) return 0;
	return Number(error.status || error.responseJSON?.status || error.xhr?.status || 0);
}

export function isGatewayTimeout(error) {
	const status = rpcStatus(error);
	return status === 504 || status === 502 || status === 408;
}

export function normalizeRpcError(error, fallback) {
	if (isGatewayTimeout(error)) return GATEWAY_TIMEOUT_MESSAGE;
	return (
		error?.message ||
		error?.exc?.server_messages ||
		error?.server_messages ||
		error?.responseJSON?.exception ||
		fallback ||
		"The request failed."
	);
}
