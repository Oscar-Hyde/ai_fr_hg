# Phase 4 — Ingestion, Intelligence, Patterns, and Translation

**Status:** IN PROGRESS — ING-06 implementation started; no other Phase 4 finding is being changed in this increment.

## Phase inventory

| ID | Current state | Required state | Evidence target | Status |
|---|---|---|---|---|
| ING-06 | Processing has durable status but no user-visible progress/cancel contract | Durable progress, cancellation, reconnect/recovery | Real Frappe worker tests and browser/realtime evidence | IN PROGRESS |
| TRN-03 | Worker user restoration exists in a scoped helper but needs closure evidence | Restore requester on success/failure and revalidate access | Real worker success/failure/revocation tests | OPEN |
| TRN-04 | Translation has internal progress callback but no durable job progress/cancel/review refresh | Durable observable, cancellable translation jobs | Worker, cancellation, realtime, reconnect tests | OPEN |
| TRN-05 | Outcome stores aggregate token data and issue summary; accounting contract incomplete | Complete cost/usage/issues persistence, including partial/back-translation outcomes | Sampling, timeout, partial-result tests | OPEN |
| TRN-07 | Glossary lookup does not yet prove KB parity | Glossary list/get/use must match KB authority | Cross-KB and cross-user permission tests | OPEN |
| PAT-01 | Scan results are not durably recorded for zero-result documents | Persist checksum/completed-empty state | Scheduler no-rescan/content-change tests | OPEN |
| PAT-02 | Sampling offsets need source-position mapping | Findings reference original document offsets | Head/tail boundary tests | OPEN |
| PAT-03 | Pattern matching is primarily shape-based | Semantic validators and confidence | Entity false-positive/negative fixtures | OPEN |
| PAT-04 | Pattern presentation is document-local | Permission-safe paginated Pattern Explorer | Frontend workflow tests | OPEN |

INT-04 is already closed and reconciled in `PHASE_3_VERIFICATION.md`. ING-01–05, INT-01–03, and TRN-01/02/06 retain the authoritative statuses in `GAP_REGISTER.md`.

## ING-06 contract

- `AI Document.processing_progress`, `processing_message`, and `processing_heartbeat` are durable server fields.
- `cancel_requested` is server-controlled and hidden from ordinary forms.
- The worker checkpoints cancellation before extraction, after extraction, and before/after indexing; cancellation is terminal `Cancelled`, not a misleading `Failed`.
- Reprocessing explicitly clears cancellation and queues a new attempt.
- Progress uses native `frappe.publish_realtime` on `ai_document_progress`; the form reloads on completion and has a polling/reload fallback through existing Desk actions.
- Authorization is enforced in `ai.ingestion.cancel_processing`, not only in the form.
- The migration is idempotent: `v0_0_18_document_processing_progress`.

## Files changed in this increment

- `ai_fr_hg/ai/ingestion.py`
- `ai_fr_hg/ai/exceptions.py`
- `ai_fr_hg/ai_knowledge/doctype/ai_document/ai_document.json`
- `ai_fr_hg/ai_knowledge/doctype/ai_document/ai_document.py`
- `ai_fr_hg/ai_knowledge/doctype/ai_document/ai_document.js`
- `ai_fr_hg/ai_knowledge/doctype/ai_document/test_ai_document.py`
- `ai_fr_hg/patches/v0_0_18_document_processing_progress.py`
- `ai_fr_hg/patches.txt`

## Runtime gate

Hosted Frappe v17 verification for this increment passed: Server run `32398794487` completed in 3m02s and Quality run `32398794523` passed. The integration test covers migration-loaded fields, cancellation persistence, and requeue recovery. ING-06 remains `IN PROGRESS` because worker-failure recovery and reconnect/browser evidence are still outstanding; those are not being inferred from a green full-suite run.
