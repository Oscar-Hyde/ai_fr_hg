// Copyright (c) 2026, Ai Fr Hg and contributors
// For license information, please see license.txt

/**
 * Desk guard — keeps Desk usable when optional bundles or the realtime
 * socket are temporarily unavailable.
 *
 * Two failures were seen on returning to Desk:
 *
 *  1. "Loading failed for the <script> with source
 *     .../build_events.bundle.MOHTM67Z.js" — a stale hashed asset.
 *  2. "Error connecting to socket.io: xhr poll error" — the realtime channel
 *     is down (Redis / Node not ready, or host mismatch).
 *
 * Neither should brick the Desk. The file browser, AI Assistant, Knowledge
 * Explorer and all other Desk routes are independent of the Event calendar
 * and of the chat streaming socket.
 *
 * This module is loaded via `ai_fr_hg.bundle.js` (app_include_js) on every
 * Desk boot. It is deliberately defensive and never throws.
 */

(() => {
	if (typeof window === "undefined") return;

	// -------------------------------------------------------------------
	// 0. Version gate. These patches target the supported Frappe v17
	//    pre-release revision (17.0.0-dev, immutable SHA d7000da3...).
	//    On any other framework version the guards must not run: a stale
	//    monkey patch is a greater risk than the failures it papers over.
	// -------------------------------------------------------------------
	const SUPPORTED_MAJOR = "17";
	const frappeVersion = () => {
		try {
			return window.frappe?.boot?.versions?.frappe || window.frappe?.version || "";
		} catch (_e) {
			return "";
		}
	};
	let versionDiagnosticShown = false;
	const versionSupported = () => {
		const version = frappeVersion();
		if (!version) return true; // framework still booting; installers retry anyway
		const supported = String(version).startsWith(`${SUPPORTED_MAJOR}.`);
		if (!supported && !versionDiagnosticShown) {
			versionDiagnosticShown = true;
			console.warn(
				`Desk guard: ai_fr_hg patches are gated to Frappe v${SUPPORTED_MAJOR}; ` +
					`found "${version}". Patches disabled.`
			);
		}
		return supported;
	};
	if (!versionSupported()) return;

	// -------------------------------------------------------------------
	// 1. Script load failures.
	//    - Optional feature bundles (calendar) may be tolerated.
	//    - Core Desk bundles (desk/form/list) are deployment errors: never
	//      silently continue with a half-working Desk. Show a recoverable
	//      rebuild diagnostic instead.
	// -------------------------------------------------------------------
	window.addEventListener(
		"error",
		(event) => {
			const target = event.target;
			// Only handle resource load errors (Script / Link), not JS
			// runtime errors. Resource errors have a src/href and no message stack.
			const src = target && (target.src || target.href);
			if (!src) return;

			// Optional calendar bundle: stale hash must not brick the Desk.
			if (src.includes("build_events.bundle")) {
				console.warn("Desk guard: bundle failed to load, continuing without it:", src);
				// Prevent the error from bubbling to Frappe's global handler
				// which otherwise shows a blocking "Loading failed" modal.
				event.preventDefault();
				// Stub minimal globals that some bundles expect so later code
				// does not throw "undefined" when it checks for the feature.
				if (typeof window.frappe !== "undefined") {
					window.frappe = window.frappe || {};
					// Calendar / Event may check for frappe.views.calendar
					window.frappe.views = window.frappe.views || {};
				}
				// Remove the failed script tag to avoid retry loops.
				try {
					if (target.parentNode) target.parentNode.removeChild(target);
				} catch (_e) {
					// ignore
				}
				return;
			}

			// Core Desk bundles: a missing desk/form/list bundle means the
			// built assets are stale or incomplete. Suppressing this silently
			// would leave a partially functioning Desk, so surface a clear,
			// recoverable rebuild diagnostic.
			if (
				src.includes("desk.bundle") ||
				src.includes("form.bundle") ||
				src.includes("list.bundle")
			) {
				console.error("Desk guard: core Desk bundle failed to load:", src);
				event.preventDefault();
				try {
					const rebuild =
						"Run 'bench build --app ai_fr_hg' and 'bench restart', then reload this page.";
					const message = "A required Desk bundle could not be loaded. " + rebuild;
					if (window.frappe?.msgprint) {
						window.frappe.msgprint({
							title:
								typeof __ === "function"
									? __("Desk assets failed to load")
									: "Desk assets failed to load",
							message,
							indicator: "red",
						});
					}
				} catch (_e) {
					// ignore
				}
				return;
			}

			// Socket.io client script missing — also non-fatal.
			if (src.includes("socket.io")) {
				console.warn("Desk guard: socket.io script failed to load:", src);
				event.preventDefault();
			}
		},
		true
	);

	// Frappe also uses `frappe.require` to load bundles dynamically.
	// If that helper rejects, it can show a blocking alert. Wrap it so a
	// missing build_events bundle is tolerated.
	function patchFrappeRequire(attempts = 20) {
		if (typeof frappe === "undefined" || typeof frappe.require !== "function") {
			if (attempts > 0) setTimeout(() => patchFrappeRequire(attempts - 1), 300);
			return;
		}
		if (frappe.require.__ai_desk_guard__) return;
		const originalRequire = frappe.require.bind(frappe);
		frappe.require = function (path, callback) {
			// Detect hashed build_events path that is known to 404 on stale builds.
			const isBuildEvents = typeof path === "string" && path.includes("build_events.bundle");
			if (!isBuildEvents) {
				return originalRequire(path, callback);
			}
			// Try the original, but swallow a 404 so Desk doesn't show a modal.
			const result = originalRequire(path, callback);
			if (result && typeof result.fail === "function") {
				result.fail((xhr, _textStatus, _err) => {
					if (xhr && xhr.status === 404) {
						console.warn(
							"Desk guard: build_events bundle 404, Desk will remain usable"
						);
						// Resolve the callback with a no-op to keep the caller happy.
						if (typeof callback === "function") {
							try {
								callback();
							} catch (_e) {
								// ignore
							}
						}
						// Return a rejected-then-resolved chain so callers using .fail().done() don't hang.
						return;
					}
				});
			}
			// Also handle Promise-style require.
			if (result && typeof result.catch === "function") {
				return result.catch((error) => {
					if (String(error).includes("build_events") || String(error).includes("404")) {
						console.warn("Desk guard: suppressed build_events load error", error);
						if (typeof callback === "function") {
							try {
								callback();
							} catch (_e) {
								// ignore
							}
						}
						return;
					}
					throw error;
				});
			}
			return result;
		};
		frappe.require.__ai_desk_guard__ = true;
	}
	patchFrappeRequire();

	// -------------------------------------------------------------------
	// 2. Socket.io / frappe.realtime resilience
	// -------------------------------------------------------------------
	function patchSocketIO(attempts = 30) {
		if (typeof frappe === "undefined") {
			if (attempts > 0) setTimeout(() => patchSocketIO(attempts - 1), 500);
			return;
		}
		const socketio = frappe.socketio;
		const realtime = frappe.realtime;
		if (!socketio && !realtime) {
			if (attempts > 0) setTimeout(() => patchSocketIO(attempts - 1), 500);
			return;
		}

		// Patch socketio.init to add retry and to never throw when returning to Desk.
		if (socketio && !socketio.__ai_desk_guard__) {
			const originalInit = socketio.init;
			if (typeof originalInit === "function") {
				socketio.init = function (...args) {
					try {
						const result = originalInit.apply(this, args);
						// The socket is stored on frappe.socketio.socket (Frappe >= v14)
						const sock = socketio.socket || (window.io && window.io.sockets);
						if (sock && typeof sock.on === "function") {
							// Suppress the noisy "xhr poll error" that otherwise
							// shows as a Desk error toast on every Desk return
							// when the socket server is still starting.
							const suppress = (err) => {
								const msg = String(err && err.message ? err.message : err);
								if (
									msg.includes("xhr poll error") ||
									msg.includes("websocket error")
								) {
									console.warn(
										"Desk guard: suppressed socket.io poll error, will retry",
										msg
									);
									return;
								}
								console.warn("Desk guard: socket.io error", err);
							};
							try {
								sock.on("connect_error", suppress);
								sock.on("error", suppress);
								// Reconnect with back-off instead of spamming the server.
								let reconnectAttempts = 0;
								sock.on("disconnect", (reason) => {
									if (
										reason === "io server disconnect" ||
										reason === "transport close"
									) {
										reconnectAttempts += 1;
										const delay = Math.min(
											1000 * Math.pow(1.5, reconnectAttempts),
											15000
										);
										console.warn(
											`Desk guard: socket disconnected (${reason}), reconnecting in ${delay}ms`
										);
										setTimeout(() => {
											try {
												if (sock.disconnected) sock.connect();
											} catch (_e) {
												// ignore
											}
										}, delay);
									}
								});
								sock.on("connect", () => {
									reconnectAttempts = 0;
								});
							} catch (_e) {
								// ignore
							}
						}
						return result;
					} catch (error) {
						console.warn(
							"Desk guard: socketio.init failed, Desk will stay usable",
							error
						);
						// Do not re-throw — Desk must remain interactive even without realtime.
						return null;
					}
				};
			}
			socketio.__ai_desk_guard__ = true;
		}

		// Patch frappe.realtime.on/off to be safe when socket is absent.
		if (realtime && !realtime.__ai_desk_guard__) {
			const originalOn = realtime.on;
			if (typeof originalOn === "function") {
				realtime.on = function (event, callback) {
					try {
						return originalOn.call(this, event, callback);
					} catch (error) {
						console.warn("Desk guard: realtime.on failed for", event, error);
						return null;
					}
				};
			}
			realtime.__ai_desk_guard__ = true;
		}

		// If socket.io already tried to connect and is in failed state on Desk
		// return, give it one quiet retry after a short delay.
		setTimeout(() => {
			try {
				if (socketio && socketio.socket && socketio.socket.disconnected) {
					// Only retry if the page is still the Desk (not a heavy chat page).
					if (window.location && window.location.pathname.includes("/app")) {
						console.warn("Desk guard: retrying socket.io connection on Desk return");
						socketio.socket.connect();
					}
				}
			} catch (_e) {
				// ignore
			}
		}, 2000);
	}
	patchSocketIO();

	// Also run patch on every Desk route change (SPA navigation: e.g.
	// Knowledge Explorer -> Desk). The Desk router reuses the same window
	// but may have destroyed and recreated the socket.
	if (typeof frappe !== "undefined" && frappe.router && typeof frappe.router.on === "function") {
		try {
			frappe.router.on("change", () => {
				// Defer so the new page has mounted before we inspect the socket.
				setTimeout(() => patchSocketIO(5), 500);
			});
		} catch (_e) {
			// ignore
		}
	}
})();
