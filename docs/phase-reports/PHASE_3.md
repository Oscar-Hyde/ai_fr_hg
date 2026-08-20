# Phase 3 — Conversation and Agent Completion

**Objective:** make the Assistant stateful, concurrent-safe, cancellable, and fully integrated.

**Opened:** 2026-08-20
**Phase owner:** Conversation
**Status:** COMPLETE — backend contracts and hosted Frappe v17 verification complete; browser E2E remains Phase 7

## Phase inventory

| ID | Finding | Current State | Required State | Files | Tests | Migration | Frontend | Status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| CHAT-01 | History uses oldest messages | Latest-N window + tool-group expansion | Latest N chronological, tool groups intact | `ai/conversation.py`, `ai/conversation_utils.py`, `ai/agent.py` | 100-message unit + integration | None | N/A (prompt) | COMPLETE |
| CHAT-02 | Sequence races | Row lock + unique index + turn_id | Locked unique sequences per conversation | `ai/conversation.py`, `AI Message`, patch `v0_0_17` | Uniqueness + duplicate-index | Unique `(conversation, sequence)` | N/A | COMPLETE |
| CHAT-03 | Route state unsynced | Route-options/path/query/hash parser; open restores selectors | Deep-link + reload restore | `public/js/ui/route_state.js`, `ai_assistant.js`, conversation form | JS route tests | None | Assistant + Open in Assistant | COMPLETE (browser → Phase 7) |
| CHAT-04 | Disconnected fields | Focused document, fallback, footnote, weights | Each field has behavior | `ai/agent.py`, `ai/conversation.py` | Focus/fallback/footnote tests | None | Focused-document chip | COMPLETE |
| CHAT-05 | Missing actions | Service + Assistant menus | Rename/pin/archive/restore/export/pagination | `ai/conversation.py`, `api/chat.py`, Assistant | Permission + pagination | None | Menus, archived filter, load earlier | COMPLETE (browser → Phase 7) |
| CHAT-06 | Negative feedback UI | Dialog + persisted reason/correction | Reason/correction + learning-disabled rating | `ai_assistant.js`, `ai/learning.py` | Persistence test | None | Improve-this-answer dialog | COMPLETE (browser → Phase 7) |
| CHAT-07 | No cancel/reconnect | turn_id cache flag, Streaming placeholder, engine abort | Stop, reconnect, Cancelled status | `ai/conversation.py`, `ai/engine.py`, `ai/agent.py` | Cancel isolation + stream abort | `turn_id` field | Stop button, recover Streaming | COMPLETE (browser → Phase 7) |
| CHAT-08 | Attachment identity | `file_record: file.name`; pending chips; restore on failure | Stable File identity | `ai_assistant.js` | Source contract | None | Chips + remove | COMPLETE (browser → Phase 7) |

## Architecture

- **Canonical owner:** `ai.conversation` for history, sequence allocation, turn cancel/status, list/export/actions, and configuration sync.
- **Agent runtime:** `ai.agent.run_agent_turn` orchestrates retrieval + model loop and persists a Streaming placeholder keyed by the same `turn_id` used for `ai_fr_hg:chat_token`.
- **Engine:** `run_chat(..., turn_id=)` checks the cancel cache between stream fragments and raises `TurnCancelledError` without failover.
- **API:** `api/chat.py` is a thin validated facade. Pagination uses Frappe v17 `limit`/`offset` (`start` on `get_all`).
- **Frontend architecture (§8.1):** `public/js/ui/{route_state,rpc,realtime}.js` plus `frappe.ai.ui` bundle wiring. Node tests under `ai_fr_hg/tests/js`.

No parallel permission, pagination, realtime, or File system was introduced.

## Frappe v17 integration

- DocType field `AI Message.turn_id`; status already included `Cancelled`.
- Idempotent patch `v0_0_17_conversation_turn_identity` (MariaDB unique index + turn_id index; duplicate sequences renumbered). A two-argument `_index_exists()` call shipped in this patch aborted the first real-bench execution on 2026-08-20; fixed with behavioral regressions in `ai_fr_hg/tests/test_patch_regressions.py` (see `PHASE_4.md` "Deploy regression found and fixed").
- Conversation row `SELECT ... FOR UPDATE` for sequence allocation.
- `frappe.get_list`/`get_all` with `limit`/`offset`/`start`.
- Native `frappe.publish_realtime` for tokens and `ai_turn_cancelled`.
- Desk page route `ai-assistant/<name>` plus `frappe.route_options`.

## Remaining issues

1. Browser/Desk E2E for deep-link, Stop, upload chips, and conversation menus is Phase 7.
2. The named 100-worker concurrent-send test now runs on independent Frappe connections and passed in hosted Server run `32394651654`; see `PHASE_3_VERIFICATION.md`.
3. `AI Message.execution_log` remains unpopulated (OPS-05, Phase 6).
4. Branch protection on `main` remains owner-only (OPS-01); four green checks are required by manual process until the owner enables platform protection.

## Phase verdict

`PASS WITH DOCUMENTED NON-BLOCKING LIMITATION` — required Phase 3 conversation contracts and the named concurrent-send gate are implemented and passed on a hosted Frappe v17 bench. Browser E2E is Phase 7; branch protection remains an explicit repository-owner limitation documented as OPS-01.
