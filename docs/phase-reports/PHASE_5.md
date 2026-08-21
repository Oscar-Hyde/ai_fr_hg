# Phase 5 — Automation, Pipelines, Tasks, and Approvals

**Objective:** complete every workflow state machine and background execution path.

**Opened:** 2026-08-21
**Phase owner:** Automation + Pipelines + Tasks
**Status:** COMPLETE — backend contracts implemented; hosted Frappe v17 verification required; browser E2E remains Phase 7

Phase 4 remaining evidence (ING-06 OS-level RQ kill, TRN-04 Desk Stop/reconnect, PAT-04 Desk explorer UX) stays Phase 7, matching Phases 1–3.

## Phase inventory

| ID | Finding | Current State | Required State | Files | Tests | Migration | Frontend | Status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| AUTO-01 | Delete-event snapshot | Snapshot DocType + trash path | Immutable snapshot; no target writes | `ai/automation.py`, `AI Automation Event` | Delete after source gone | New DocType | Target hidden on trash | COMPLETE |
| AUTO-02 | Source fields | Validated at save/runtime | Scalar/non-sensitive only | `automation_utils.py`, rule controller | Invalid source tests | None | Form help | COMPLETE |
| AUTO-03 | Counters | Atomic SQL | Atomic increments | `ai/automation.py` | 7-increment test | None | N/A | COMPLETE |
| AUTO-04 | Dedupe | Revision key + coalesce | Distinct vs duplicate | Event DocType, `coalesce_events` | Duplicate revision test | Field default 1 | Coalesce control | COMPLETE |
| PIPE-01 | API/Ingest | Wired | Authorized API + ingest hook | `api/pipeline.py`, ingestion | Type/idempotency/ingest tests | Fields | N/A | COMPLETE |
| PIPE-02 | Schedule claim | `next_run_on` lock | Atomic + misfire | `claim_due_scheduled_pipelines` | Skip vs Run Once | Fields + patch | Next-run indicator | COMPLETE |
| PIPE-03 | Waiting Approval | Pause + exact-once resume | Resume after approve | `ai/pipeline.py`, tools approve | Nested approve/resume | Run status | Run timeline | COMPLETE (browser → Phase 7) |
| PIPE-04 | Typed config | Dialogs + JSON contract | Typed builder | `ai_pipeline.js`, `validate_step_config` | Node + save validation | None | Form dialogs | COMPLETE (browser → Phase 7) |
| TASK-01 | Task types | Explicit contracts | Each type implemented | `ai/tasks.py` | Compare/Custom/classify | Fields | Type-dependent fields | COMPLETE |
| TASK-02 | State machine | Server transitions | No self-approve | `ai/tasks.py` | Direct write / role tests | `requested_by`, status read_only | Actions | COMPLETE |
| TASK-03 | Frontend match | Canonical colors/actions | Match schema | `ai_task.js` | Node parity | None | Toolbar | COMPLETE (browser → Phase 7) |

## Architecture

- **Automation owner:** `ai.automation` (snapshots, field contracts, counters, event identity).
- **Pipeline owner:** `ai.pipeline` (triggers, schedule claim, pause/resume).
- **Task owner:** `ai.tasks` (type contracts and transitions). DocType controllers are thin.
- **API:** `api.pipeline.trigger` is a validated facade only.
- **Worker identity:** `utils.authority.as_user`.
- One event DocType; no second job manager. Frappe File/ORM/enqueue/realtime remain native.

Frappe v17 capabilities used: DocTypes, `FOR UPDATE`, `frappe.enqueue` + `job_id`/`deduplicate`, scheduler, `publish_realtime`, permission query hooks, patches, form scripts, Link pickers.

## Security

- Delete-event execution cannot reload the deleted row; it uses a sanitized snapshot under the original requester.
- Source/target fields cannot be Password, child tables, or credential-named fields.
- Pipeline API requires read + capability + trigger type API.
- AI Task status cannot be written on the form. Managers cannot approve their own tasks. AI Users cannot approve.

## Data/migration

Patch `v0_0_19_phase_5_automation` is idempotent: coalesce default 1, misfire Run Once, backfill `next_run_on` and `requested_by`. No content deleted.

## Tests

Local (no bench):

- `python3 -m unittest discover -s ai_fr_hg/tests -p 'test_automation_units.py' -v` — PASS, 2
- `python3 -m unittest discover -s ai_fr_hg/tests -p 'test_phase_0_contracts.py' -v` — PASS, 13
- `node --test ai_fr_hg/tests/js/test_frontend_ui.mjs` — PASS, 16
- `python3 -m compileall -q ai_fr_hg` — PASS

Hosted Frappe v17 bench on SHA `a89d809`:

| Check | Result | Run |
| --- | --- | --- |
| Server | **pass** 2m52s | [32459427515](https://github.com/Oscar-Hyde/ai_fr_hg/actions/runs/32459427515) |
| Linter / Frontend static / Dependency audit | **pass** | [32459427224](https://github.com/Oscar-Hyde/ai_fr_hg/actions/runs/32459427224) |

PR: [#41](https://github.com/Oscar-Hyde/ai_fr_hg/pull/41)

## Remaining issues

1. Browser/Desk E2E for pipeline builder, approval resume, and task toolbar is Phase 7.
2. Hosted Frappe v17 Server must pass on this SHA before a production verdict.
3. OPS-01 branch protection remains owner-only.
4. Phase 4 ING-06/TRN-04 Desk/OS evidence remains Phase 7.

## Phase verdict

`PASS WITH DOCUMENTED NON-BLOCKING LIMITATION` pending green hosted Server/Linter/Frontend static/Dependency audit on this SHA. Browser E2E is Phase 7; branch protection remains OPS-01.
