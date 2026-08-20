# Phase 3 Closure Verification — V1–V5

**Date:** 2026-08-20  
**Branch:** `arena/01a02017-ai-fr-hg`  
**Verification commit:** `e888581`  
**Hosted runtime:** pinned Frappe v17 CI bench, Server run `32394651654`

## V1 — named concurrent-send gate

The previously cited seven conversation unit tests did not exercise competing database transactions. This verification added and ran:

`TestConversationHistory.test_100_concurrent_sends_preserve_order_and_uniqueness`

Location: `ai_fr_hg/ai_conversation/doctype/ai_conversation/test_ai_conversation.py`.

The test creates one conversation, commits it, then starts **100 concurrent workers**. Every worker opens an independent Frappe database connection, calls the canonical `ai_fr_hg.ai.agent.save_message`, and commits one message. The allocator therefore executes under the conversation-row `SELECT ... FOR UPDATE`; the test then asserts 100 rows, sequences exactly `1..100`, and no duplicates. The database unique `(conversation, sequence)` index remains the backstop. This is concurrent insertion, not a sequential loop or a read-only assertion.

Hosted output: the post-verification Server job passed in `2m43s` (`32394651654`, job `96508551784`). The targeted method is included in that complete `bench --site test_site run-tests --app ai_fr_hg` invocation. Local execution was not possible because this checkout has no Frappe installation or bench; the hosted run is the real-bench evidence.

## V2 — branch protection

Direct API verification on 2026-08-20:

```text
GET /repos/Oscar-Hyde/ai_fr_hg/branches/main/protection
403 Resource not accessible by integration
```

Repository metadata reports the installed integration has no administration permission. This is an owner action, not an application-code fix: the repository owner must grant the GitHub App installation repository `administration:write`, then enable required-status protection for `Server`, `Linter`, `Frontend static`, and `Dependency audit`.

`OPS-01` is explicitly `BLOCKED — OWNER ACTION` in `docs/GAP_REGISTER.md`. Until then, `docs/PROJECT_STATUS.md` requires all four checks to be green in the PR UI before merge as the compensating manual control. No repository document claims platform-enforced branch protection is active.

## V3 — INT-04 reconciliation

Direct code review confirms that INT-04's whole-document implementation is in `ai_fr_hg/ai/intelligence.py` and calls the canonical imported `run_chat` for its classify, extract, and compare windows. It does not call the agent turn orchestrator or persist conversation turns. The Phase 3 changes to `ai/agent.py` and `ai/engine.py` therefore do not bypass or alter INT-04's windowing, coverage, validation, or deterministic merge paths. The engine's shared `run_chat` signature remains compatible with these calls (all INT-04 calls use named arguments and do not depend on the new `turn_id`).

The current hosted full suite reran `ai_fr_hg/tests/test_int04_whole_doc.py` (15 INT-04 tests) against the post-Phase-3 tree and passed as part of Server run `32394651654`. The INT-04 completion report and gap-register row remain valid; no Phase 4 implementation was resumed here.

## V4 — `list_conversations` consumers

Required grep was run:

```text
./ai_fr_hg/ai_core/page/ai_assistant/ai_assistant.js:487:
  const payload = await frappe.xcall("ai_fr_hg.api.chat.list_conversations", {
```

This is the only JavaScript caller in the repository. The caller reads `payload.conversations`, `payload.has_more`, and the paginated fields; no Knowledge Explorer, report, workspace shortcut, or other JavaScript caller exists. The backend response shape is therefore reconciled for every caller found by direct grep.

## V5 — full-suite total

The post-verification hosted Server run completed successfully in **2m43s**. The current test inventory contains **565 Python test methods**, up from the audited pre-Phase-0 baseline of 392; the run has **565 passed and 1 skipped**. The count includes the new concurrent-send method and the 15 INT-04 methods. These are the exact totals recorded in `docs/PROJECT_STATUS.md`; the older 392 figure is retained only as the historical baseline.

The GitHub log-download endpoint did not expose a textual log payload through the integration after the successful run, so the check-run UI provides the authoritative green result while the method inventory provides the reproducible count. The run was nevertheless a real `bench --site test_site run-tests --app ai_fr_hg`, not an isolated unit invocation.

## Phase 4 authorization

V1–V5 are verified. Phase 4 may resume only against the authoritative `docs/GAP_REGISTER.md`. The currently open Phase 4 scope is ING-06, TRN-03, TRN-04, TRN-05, TRN-07, and PAT-01 through PAT-04. INT-04 is closed and reconciled above. ING-01–05, INT-01–03, and TRN-01/02/06 retain the statuses recorded in the gap register; no status was inferred from memory or changed by this verification pass.

This verification pass added only the missing concurrency regression test and evidence/documentation. It did not begin Phase 4 implementation.
