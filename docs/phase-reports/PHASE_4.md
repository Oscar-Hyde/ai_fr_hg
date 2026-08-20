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

## Deploy regression found and fixed (2026-08-20)

The first real-bench execution of the two newest patches — on an existing site where patches actually run, unlike fresh CI sites — exposed two defects that a green CI run had not caught. Both are fixed in this increment, with behavioral regression tests that demonstrably failed before the fix:

1. **`v0_0_17_conversation_turn_identity`** aborted `bench migrate` with
   `TypeError: _index_exists() takes 1 positional argument but 2 were given` —
   the turn_id index guard passed the table name to a one-argument helper. MariaDB's
   implicit DDL commit had already persisted the renumbering and (where it succeeded)
   the unique index, so the fixed patch resumes idempotently: it skips work already
   committed and only adds the missing `turn_id_index`.
2. **`v0_0_18_document_processing_progress`** would have *silently no-oped* on every
   site: `frappe.db.table_exists("tabAI Document")` double-prefixes the table name
   (`table_exists` itself prepends `tab`), so the probe always returned False and the
   `Indexed`→100% backfill would never have run. Fixed to `table_exists("AI Document")`,
   matching the call convention used by `v0_0_5`/`v0_0_6`/`v0_0_8`.

Regression evidence: `ai_fr_hg/tests/test_patch_regressions.py` executes both real
patch modules against an in-memory implementation of the Frappe database semantics
they rely on (bare-DocType `table_exists`/`has_column`, index inspection, renumbering).
Against the pre-fix code the suite errors with the exact bench `TypeError` and fails
on the skipped backfill; against the fixed code all 8 tests pass. The same file runs
under `bench --site site1.local run-tests --app ai_fr_hg` on the real bench.

**Gate status correction:** because of these findings, the earlier statement that the
migration is "idempotent" was true only of the code's intent, not of any executed run.
It becomes a verified claim only when `bench migrate` completes cleanly on the
production-like site; the bench output belongs in this section before ING-06 closes.
