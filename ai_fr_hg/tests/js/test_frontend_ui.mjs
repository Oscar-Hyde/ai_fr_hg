import assert from "node:assert/strict";
import { describe, it } from "node:test";

import {
  patternExplorerErrorView,
  pipelineCanCancel,
  pipelineRunIndicator,
  reconnectTranslationFromServer,
  taskActionsFor,
  translationRealtimeShouldReload,
  translationShouldShowStop,
} from "../../public/js/ui/desk_workflows.js";
import { createRealtimeSession } from "../../public/js/ui/realtime.js";
import {
  GATEWAY_TIMEOUT_MESSAGE,
  isGatewayTimeout,
  normalizeRpcError,
} from "../../public/js/ui/rpc.js";
import {
  parseExtractionEvidence,
  summarizeExtractionEvidence,
} from "../../public/js/ui/extraction_evidence.js";
import {
  parseAssistantRoute,
  serializeAssistantHash,
} from "../../public/js/ui/route_state.js";

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
      parseAssistantRoute({ search: "?conversation=AICONV-2026-00008" })
        .conversation,
      "AICONV-2026-00008"
    );
    assert.equal(
      parseAssistantRoute({ hash: "#c=AICONV-2026-00009" }).conversation,
      "AICONV-2026-00009"
    );
  });

  it("returns null when nothing identifies a conversation", () => {
    assert.equal(
      parseAssistantRoute({ route: ["ai-assistant"] }).conversation,
      null
    );
  });

  it("serializes a hash for deep links", () => {
    assert.equal(
      serializeAssistantHash({ conversation: "AICONV-2026-00001" }),
      "#c=AICONV-2026-00001"
    );
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

describe("TRN-04 translation Stop/reconnect contracts", () => {
  it("shows Stop only while queued or translating", () => {
    assert.equal(translationShouldShowStop("Queued"), true);
    assert.equal(translationShouldShowStop("Translating"), true);
    assert.equal(translationShouldShowStop("Cancelled"), false);
    assert.equal(translationShouldShowStop("Completed"), false);
  });

  it("reloads from realtime only for the same translation name", () => {
    assert.equal(
      translationRealtimeShouldReload({ translation: "TRN-1" }, "TRN-1"),
      true
    );
    assert.equal(
      translationRealtimeShouldReload({ translation: "TRN-2" }, "TRN-1"),
      false
    );
  });

  it("reconnects from server fields, not browser memory", () => {
    const restored = reconnectTranslationFromServer({
      status: "Cancelled",
      processing_progress: 40,
      processing_message: "Cancelled",
      cancel_requested: 1,
      total_tokens: 19,
    });
    assert.equal(restored.status, "Cancelled");
    assert.equal(restored.progress, 40);
    assert.equal(restored.cancel_requested, 1);
    assert.equal(restored.total_tokens, 19);
  });
});

describe("PAT-04 pattern explorer permission view", () => {
  it("denies without leaking entities", () => {
    const view = patternExplorerErrorView({
      message: "You cannot explore patterns in that knowledge base.",
    });
    assert.equal(view.kind, "permission-denied");
    assert.deepEqual(view.entities, []);
  });
});

describe("TASK-03 task action parity", () => {
  it("hides approve from the requester even when they are a manager", () => {
    const actions = taskActionsFor("Pending Approval", {
      isManager: true,
      isRequester: true,
    });
    assert.equal(actions.includes("approve"), false);
    assert.equal(actions.includes("reject"), true);
  });

  it("lets a manager who is not the requester approve", () => {
    const actions = taskActionsFor("Pending Approval", {
      isManager: true,
      isRequester: false,
    });
    assert.deepEqual(actions, ["approve", "reject", "cancel"]);
  });

  it("blocks run for users when approval is required", () => {
    const actions = taskActionsFor("Open", {
      isManager: false,
      isRequester: true,
      requiresApproval: true,
    });
    assert.equal(actions.includes("run"), false);
    assert.equal(actions.includes("submit"), true);
  });
});

describe("PIPE-03 run status contract", () => {
  it("uses orange for waiting approval and allows cancel", () => {
    assert.equal(pipelineRunIndicator("Waiting Approval"), "orange");
    assert.equal(pipelineCanCancel("Waiting Approval"), true);
    assert.equal(pipelineCanCancel("Completed"), false);
  });
});

describe("Wave 4 extraction evidence summary", () => {
  it("treats missing payload as empty", () => {
    const summary = summarizeExtractionEvidence("");
    assert.equal(summary.kind, "empty");
    assert.equal(summary.empty, true);
  });

  it("flags extension/magic mismatch and counts embedded objects", () => {
    const summary = summarizeExtractionEvidence({
      detector: {
        extension: "txt",
        magic: "pdf",
        family: "pdf",
        mismatch: true,
        reason: "extension_magic_mismatch",
      },
      reader: "PDF",
      embedded_objects: [{ kind: "image", name: "p1" }],
      structure: { block_count: 2, kinds: { page: 2 } },
      provenance: {
        bytes: 1200,
        checksum_sha256: "abc",
        word_count: 40,
        page_count: 2,
      },
    });
    assert.equal(summary.kind, "mismatch");
    assert.equal(summary.reader, "PDF");
    assert.equal(summary.embedded, 1);
    assert.equal(summary.blocks, 2);
    assert.equal(summary.word_count, 40);
  });

  it("parses JSON strings without throwing", () => {
    const parsed = parseExtractionEvidence('{"reader":"Plain Text"}');
    assert.equal(parsed.reader, "Plain Text");
    assert.deepEqual(parseExtractionEvidence("not-json"), {});
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

describe("Part 1 evidence version provenance", () => {
  it("surfaces the extractor timestamp and version triple", () => {
    const summary = summarizeExtractionEvidence(
      JSON.stringify({
        reader: "PDF",
        detector: { family: "pdf", extension: "pdf" },
        provenance: { bytes: 10 },
        extracted_on: "2026-08-21T10:00:00+00:00",
        versions: {
          app: "0.0.1",
          reader: "1.1",
          library: "pypdf",
          library_version: "6.16.1",
        },
      })
    );
    assert.equal(summary.extracted_on, "2026-08-21T10:00:00+00:00");
    assert.equal(summary.app_version, "0.0.1");
    assert.equal(summary.reader_version, "1.1");
    assert.equal(summary.library_version, "6.16.1");
    assert.equal(summary.legacy, false);
  });

  it("flags evidence produced before the versioned extractor", () => {
    const summary = summarizeExtractionEvidence(
      JSON.stringify({
        reader: "PDF",
        detector: { family: "pdf" },
        provenance: { bytes: 10 },
        versions: { app: "pre-0.0.2", reader: "unknown" },
      })
    );
    assert.equal(summary.legacy, true);
  });

  it("stays empty-safe when evidence has no version block", () => {
    const summary = summarizeExtractionEvidence("{}");
    assert.equal(summary.empty, true);
    assert.equal(summary.app_version, "");
    assert.equal(summary.legacy, false);
  });
});
