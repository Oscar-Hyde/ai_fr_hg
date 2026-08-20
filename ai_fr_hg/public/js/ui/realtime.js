// Copyright (c) 2026, Ai Fr Hg and contributors
// For license information, please see license.txt

/**
 * Realtime subscription lifecycle: subscribe on show, unsubscribe on hide.
 * Inject `{ on, off }` so tests do not need a socket.
 */

export function createRealtimeSession(hooks) {
	const on = hooks && typeof hooks.on === "function" ? hooks.on : () => {};
	const off = hooks && typeof hooks.off === "function" ? hooks.off : () => {};
	const handlers = [];

	return {
		subscribe(event, fn) {
			on(event, fn);
			handlers.push([event, fn]);
			return fn;
		},
		unsubscribeAll() {
			while (handlers.length) {
				const pair = handlers.pop();
				off(pair[0], pair[1]);
			}
		},
		get size() {
			return handlers.length;
		},
	};
}
