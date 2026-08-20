import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { createRealtimeSession } from "../../public/js/ui/realtime.js";
import { GATEWAY_TIMEOUT_MESSAGE, isGatewayTimeout, normalizeRpcError } from "../../public/js/ui/rpc.js";
import { parseAssistantRoute, serializeAssistantHash } from "../../public/js/ui/route_state.js";

describe("parseAssistantRoute", () => {
	it("prefers route_options from Open in Assistant", () => {
		const parsed = parseAssistantRoute({
			route: ["ai-assistant"],
			routeOptions: { conversation: "AICONV-2026-00042" },
		});
		assert.equal(parsed.conversation, "AICONV-2026-00042");
	});

	it("reads a conversation path segment", () => {
		const parsed = parseAssistantRoute({
			route: ["ai-assistant", "AICONV-2026-00007"],
		});
		assert.equal(parsed.conversation, "AICONV-2026-00007");
	});

	it("reads query and hash fallbacks", () => {
		assert.equal(
			parseAssistantRoute({ search: "?conversation=AICONV-2026-00008" }).conversation,
			"AICONV-2026-00008"
		);
		assert.equal(parseAssistantRoute({ hash: "#c=AICONV-2026-00009" }).conversation, "AICONV-2026-00009");
	});

	it("returns null when nothing identifies a conversation", () => {
		assert.equal(parseAssistantRoute({ route: ["ai-assistant"] }).conversation, null);
	});

	it("serializes a hash for deep links", () => {
		assert.equal(serializeAssistantHash({ conversation: "AICONV-2026-00001" }), "#c=AICONV-2026-00001");
		assert.equal(serializeAssistantHash({ conversation: "" }), "");
	});
});

describe("rpc errors", () => {
	it("names gateway timeouts", () => {
		assert.equal(isGatewayTimeout({ status: 504 }), true);
		assert.equal(normalizeRpcError({ status: 502 }), GATEWAY_TIMEOUT_MESSAGE);
	});

	it("falls back to the provided message", () => {
		assert.equal(normalizeRpcError({ message: "nope" }, "fallback"), "nope");
		assert.equal(normalizeRpcError({}, "fallback"), "fallback");
	});
});

describe("realtime session", () => {
	it("unsubscribes every handler on hide", () => {
		const calls = [];
		const session = createRealtimeSession({
			on: (event, fn) => calls.push(["on", event, fn]),
			off: (event, fn) => calls.push(["off", event, fn]),
		});
		const fn = () => {};
		session.subscribe("ai_fr_hg:chat_token", fn);
		session.subscribe("ai_turn_cancelled", fn);
		assert.equal(session.size, 2);
		session.unsubscribeAll();
		assert.equal(session.size, 0);
		assert.equal(calls.filter((row) => row[0] === "off").length, 2);
	});
});
