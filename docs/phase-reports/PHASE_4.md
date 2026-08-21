# Phase 4 — Ingestion, Intelligence, Patterns, and Translation

**Status date:** 2026-08-21  
**Phase verdict:** `FAIL` as a close — TRN-05 accounting is now on a green hosted Server run, but Desk/browser and live RQ evidence remain.

## Hosted Frappe v17 bench (authoritative)

Commit `8564db6` on [PR #40](https://github.com/Oscar-Hyde/ai_fr_hg/pull/40):

| Check | Result | Run |
| --- | --- | --- |
| Server | **pass** 2m19s | [32454131712](https://github.com/Oscar-Hyde/ai_fr_hg/actions/runs/32454131712) |
| Linter | **pass** 1m0s | [32454131635](https://github.com/Oscar-Hyde/ai_fr_hg/actions/runs/32454131635) |
| Frontend static | **pass** | same Quality run |
| Dependency audit | **pass** | same Quality run |

This is `bench --site test_site run-tests --app ai_fr_hg` on the pinned Frappe v17 CI bench, not a sandbox `compileall`.

Prior green code baseline for stale-worker/cancel status: `5b256d1` Server [32453526161](https://github.com/Oscar-Hyde/ai_fr_hg/actions/runs/32453526161).

## Inventory

| ID | Finding | Status | Evidence on this branch | Remaining |
| --- | --- | --- | --- | --- |
| ING-01–05 | Folder/`.msg`/OCR claims, ZIP bombs, warnings | CLOSED earlier | Register + prior Server | — |
| ING-06 | Progress/cancel/stale worker | IN PROGRESS | Cancel before/mid extract; unauthorized cancel denied; stale heartbeat → `Failed(StaleWorker)` then one `enqueue_processing` | Live RQ process death and Desk reconnect remain Phase 7 |
| INT-01–04 | Extraction mapping / schema / coverage | CLOSED earlier | Register | — |
| TRN-01–02, 06 | Memory policy, index default, text-structure | CLOSED earlier | Register | — |
| TRN-03 | Worker `as_user` | CLOSED — IMPLEMENTED | Disabled requester + restore-after-failure | — |
| TRN-04 | Progress/cancel/realtime | IN PROGRESS | Cancel not Failed; `get_translation` returns progress fields | Browser Stop/reconnect → Phase 7 |
| TRN-05 | Back-translation accounting | CLOSED — IMPLEMENTED | Verification tokens in `total_tokens`; failed run keeps usage; deadline `partial`; timeout-after-generation and cancel-after-partial persist usage on Server `32454131712` | — |
| TRN-07 | Glossary KB parity | CLOSED — IMPLEMENTED | Unauthorized glossary use raises `PermissionError` | — |
| PAT-01–03 | Durable empty scan, offsets, validators | CLOSED — IMPLEMENTED | Integration + unit tests on Server | — |
| PAT-04 | Pattern Explorer | CLOSED — IMPLEMENTED (backend+page) | Pagination/filter + unauthorized KB `PermissionError` | Browser workflow → Phase 7 |

## Architecture

- One worker identity: `ai_fr_hg.utils.authority.as_user`.
- Document recovery stays on `process_pending_documents` / `enqueue_processing` (no second job manager).
- Translation cancel/progress is DocType + `frappe.publish_realtime("ai_translation_progress")`.
- Pattern Explorer lists through `frappe.get_list` (permission query + `limit`/`start`).

## Not completed (not dropped)

1. Desk/browser E2E for ING-06, TRN-04, PAT-04 — Phase 7.
2. Live RQ worker-death (heartbeat test is synthetic) — Phase 7 / remaining ING-06 evidence.

## Phase verdict

`FAIL` — do not start Phase 5 until remaining ING-06/TRN-04 evidence is closed or explicitly deferred in a reviewed report.
