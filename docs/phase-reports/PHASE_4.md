# Phase 4 — Ingestion, Intelligence, Patterns, and Translation

**Status date:** 2026-08-21  
**Phase gate:** `FAIL` (pending deferred evidence)  
**This is not a PASS close.** Phase 5 is **not started**.

## Phase 4 final status

| Layer | Status |
| --- | --- |
| Implementation (backend contracts in this phase) | COMPLETE for the items below |
| Hosted Frappe v17 CI | PASS |
| Backend contract verification | PASS on hosted Server |
| Browser / OS-level chaos verification | INCOMPLETE (Phase 7) |
| **Overall phase gate** | **FAIL pending deferred evidence** |

Label used here: **COMPLETE WITH DEFERRED EVIDENCE**. That is not `PASS` and not `PASS WITH DOCUMENTED NON-BLOCKING LIMITATION`. The owner has not waived the remaining evidence.

## Hosted Frappe v17 bench (authoritative)

Latest docs HEAD `d70406e` on [PR #40](https://github.com/Oscar-Hyde/ai_fr_hg/pull/40):

| Check | Result | Run |
| --- | --- | --- |
| Server | **pass** 2m35s | [32455341915](https://github.com/Oscar-Hyde/ai_fr_hg/actions/runs/32455341915) |
| Linter / Frontend static / Dependency audit | **pass** | [32455341737](https://github.com/Oscar-Hyde/ai_fr_hg/actions/runs/32455341737) |

Code baseline for ING-06 live/dead heartbeat: `09b5a1f` Server [32454916140](https://github.com/Oscar-Hyde/ai_fr_hg/actions/runs/32454916140).  
TRN-05 accounting: `8564db6` Server [32454131712](https://github.com/Oscar-Hyde/ai_fr_hg/actions/runs/32454131712).

This is `bench --site test_site run-tests --app ai_fr_hg` on the pinned Frappe v17 CI bench, not a sandbox `compileall`.

## Findings

| ID | Finding | Register status | Backend evidence | Deferred |
| --- | --- | --- | --- | --- |
| ING-01–05 | Folder/`.msg`/OCR, ZIP bombs, warnings | CLOSED earlier | Prior Server | — |
| ING-06 | Progress/cancel/recovery | IN PROGRESS — deferred evidence | Cancel before/mid extract; unauthorized cancel denied; dead heartbeat → `Failed`/`StaleWorker` + one enqueue; live heartbeat not reaped; in-flight enqueue no-op | OS-level RQ kill/restart; Desk reconnect |
| INT-01–04 | Extraction mapping / schema / coverage | CLOSED earlier | Register | — |
| TRN-01–03, 05–07 | Memory policy, index default, `as_user`, accounting, text-structure, glossary | CLOSED — IMPLEMENTED | Hosted Server including `32454131712` | — |
| TRN-04 | Progress/cancel/realtime | IN PROGRESS — deferred evidence | Durable cancel/progress fields; cancel is not Failed; realtime `ai_translation_progress` | Browser Stop/reconnect |
| PAT-01–03 | Durable empty scan, offsets, validators | CLOSED — IMPLEMENTED | Server tests | — |
| PAT-04 | Pattern Explorer | CLOSED — IMPLEMENTED (backend+page) | `explore_pattern_entities` + Desk page; unauthorized KB `PermissionError` | Browser permission UX |

## Architecture (unchanged)

- One worker identity: `ai_fr_hg.utils.authority.as_user`.
- Document recovery stays on `process_pending_documents` / `enqueue_processing` (no second job manager).
- Translation cancel/progress is DocType + `frappe.publish_realtime("ai_translation_progress")`.
- Pattern Explorer lists through `frappe.get_list` (permission query + `limit`/`start`).

## Option B (Phase 7 evidence) — in progress, not closed

Owner chose Option B. This increment is **not** a Desk/browser run and **not** an OS-level RQ kill.

Added:

- ING-06: queued document whose RQ job is gone (`is_job_enqueued` false) is re-enqueued once.
- TRN-04: `get_translation` after cancel is the reconnect payload. Node: Stop only while Queued/Translating; realtime reload is name-scoped; reconnect uses server fields.
- PAT-04: Node permission error view contains no entities; Desk matcher now treats `cannot explore` as denied.

Still missing:

1. ING-06 kill a live RQ worker process.
2. TRN-04 real Desk Stop + browser reconnect.
3. PAT-04 real Desk unauthorized explorer UX.

## Phase verdict

`FAIL` — Option B started; Node/API reconnect contracts are not Desk/OS chaos. **Do not start Phase 5.** PASS is not claimed.
