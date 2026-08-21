# Phase 4 — Ingestion, Intelligence, Patterns, and Translation

**Status:** IN PROGRESS — 2026-08-21 increment closed TRN-03, TRN-07, PAT-01, PAT-02, PAT-03. TRN-04/05 remain in progress. PAT-04 and ING-06 browser evidence remain open. Phase is **not** closed.

## Phase inventory

| ID | Current state | Required state | Evidence target | Status |
|---|---|---|---|---|
| ING-06 | Durable progress/cancel on AI Document | Worker-failure + browser reconnect | Real Frappe worker tests and browser/realtime evidence | IN PROGRESS |
| TRN-03 | Canonical `as_user` + disabled requester rejection | Same | Restoration and disabled-user tests | CLOSED — IMPLEMENTED |
| TRN-04 | Cancel/progress fields + API | Browser Stop/reconnect | Cancel test; Desk → Phase 7 | IN PROGRESS |
| TRN-05 | Back-translation tokens in verification | Timeout/partial matrix | Remaining timeout tests | IN PROGRESS |
| TRN-07 | Glossary query + has_permission + load_glossary | Same | Unauthorized glossary use test | CLOSED — IMPLEMENTED |
| PAT-01 | `pattern_scan_checksum` including empty scans | Same | Zero-result no-rescan test | CLOSED — IMPLEMENTED |
| PAT-02 | Scan-window offset mapper | Same | Tail identifier source offset test | CLOSED — IMPLEMENTED |
| PAT-03 | IPv4/calendar/money semantic checks | Same | Invalid IP/date unit test | CLOSED — IMPLEMENTED |
| PAT-04 | Document-local listing only | Pattern Explorer UI | Frontend workflow tests | OPEN |

## Architecture

- Worker identity lives in `ai_fr_hg.utils.authority.as_user` — one owner. Translation no longer has a parallel `set_user` block.
- Glossary row access uses the same KB grant as documents (`glossary_query` / `has_document_permission`).
- Pattern empty scans are durable on `AI Document.pattern_scan_checksum`, not Redis-only.

## Remaining in this phase

- PAT-04 Pattern Explorer
- TRN-04 browser cancel/review refresh
- TRN-05 timeout/partial accounting tests
- ING-06 worker-death/browser reconnect
- Hosted `bench run-tests` evidence for this increment (CI after push)

## Phase verdict

`FAIL` as a phase close — required items remain OPEN/IN PROGRESS. This increment is a continuation, not a gate pass.
