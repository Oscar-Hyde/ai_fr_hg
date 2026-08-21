# Phase 6 — Governance, Operations, Learning, and Backup

**Objective:** turn declared governance and operational controls into enforced behaviour.

**Opened:** 2026-08-21
**Phase owner:** Governance + Provider + Operations + Learning
**Status:** **OPEN** — part A (governance and provider enforcement, health scheduling, learning reports) is complete and verified on the pinned Frappe v17 bench. Part B (operations UI, backup/restore, learning lifecycle, provider model lifecycle) has not started.

This phase is deliberately **not** being closed. Section 11.4 of the directive
requires anything incomplete to be stated plainly rather than dropped, and
section 4 forbids starting Phase 7 on a partially completed Phase 6. Nine of
the seventeen registered Phase 6 findings remain OPEN; they are listed in
§"Not completed" with the reason and their next phase.

---

## 1. Phase inventory

| ID | Finding | Current state | Required state | Files | Tests | Migration | Frontend | Status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| GOV-01 | Concurrency limits declared, unused | Redis TTL leases enforced in the engine | User/provider/model slots, safe release | `ai/limits.py`, `ai/governance.py`, `ai/engine.py` | 6 bench + policy | none | none needed | **COMPLETE** |
| GOV-02 | Provider rate limit unused | Sliding 60 s window per provider | Distributed, failover-aware | `ai/limits.py`, `ai/engine.py` | 4 bench | none | none needed | **COMPLETE** |
| GOV-03 | Check-then-use quota | Atomic reservation + reconciliation | No concurrent overrun | `ai/limits.py`, `ai/governance.py` | 7 bench | none | none needed | **COMPLETE** |
| GOV-04 | Explicit model type unchecked | Type policy enforced in `resolve_model` | Compatible types only | `ai/capability.py`, `ai/engine.py` | 6 offline + 4 bench | none | none needed | **COMPLETE** |
| PROV-01 | Failover keeps the wrong model name | Equivalent target resolution + truthful log | Compatible model on target provider | `ai/capability.py`, `ai/engine.py`, `ai/logging.py`, `ai/providers/__init__.py` | 7 offline + 3 bench | none | none needed | **COMPLETE** |
| PROV-02 | Capability fields cosmetic | Adapter ∧ model ∧ probe, enforced pre-request | Fail clearly before the runtime call | `ai/capability.py`, `ai/engine.py`, `ai/providers/*`, `ai_model.json` | 11 offline + 3 bench | `v0_0_21` backfill | field descriptions | **COMPLETE** |
| OPS-02 | Minute-modulo health scheduling | Timestamp threshold + `for_update` claim | Real cadence, exactly once | `ai/monitoring.py`, `tasks.py` | 5 bench | none | none needed | **COMPLETE** |
| LEARN-01 | Query Reports ignore `execute()` | Script Reports + validated filters | Filters actually filter | 3 report JSON/py, `ai/learning_utils.py` | 8 offline + 8 bench | `v0_0_21` reload | filters now live | **COMPLETE** |
| PROV-03 | `model_prefix`, versions, pull progress | unchanged | implement or remove each field | — | — | — | — | **OPEN** |
| OPS-03 | Operations UI drill-down | unchanged | timers, charts, filters, SLOs | — | — | — | — | **OPEN** |
| OPS-04 | Export/import is not a restore path | unchanged | versioned manifest, checksums, selective restore | — | — | — | — | **OPEN** |
| OPS-05 | Audit/trace links, stale reconciliation | unchanged | link identities, reconcile Running | — | — | — | — | **OPEN** |
| OPS-06 | Unbounded backup/cleanup jobs | unchanged | batching, continuation, savepoints | — | — | — | — | **OPEN** |
| LEARN-02 | No Learning Dashboard | unchanged | permission-safe dashboard | — | — | — | — | **OPEN** |
| LEARN-03 | Memory embeddings unused in recall | unchanged | hybrid semantic recall | — | — | — | — | **OPEN** |
| LEARN-04 | Skills not relevance-ranked | unchanged | rank against request | — | — | — | — | **OPEN** |
| LEARN-05 | No lifecycle maintenance | unchanged | merge/supersede/archive/re-embed | — | — | — | — | **OPEN** |

---

## 2. Architecture — where each responsibility now lives

| Responsibility | Owner | Notes |
| --- | --- | --- |
| "May this call start now?" | `ai.limits` | Sole owner of concurrency leases, rate windows, and quota reservations. Redis/Lua primitives only; no policy decisions. |
| "Which limits apply to this call?" | `ai.governance` | Sole reader of `AI Resource Policy`, `AI Provider` and `AI Model` limit fields. Returns `(scope, limit)` pairs; performs no I/O against Redis. |
| "May this model do this?" | `ai.capability` | Pure model-type, effective-capability, and failover-equivalence policy, plus the runtime probe cache. No SQL. |
| "Which adapter class backs a provider?" | `ai.providers` | New `get_provider_class` / `get_provider_class_for`; `get_provider` now composes them rather than duplicating the resolution. |
| Orchestration | `ai.engine` | Calls the four owners above in order. Contains no limit arithmetic, no capability rules, and no Redis access. |
| Health scheduling | `ai.monitoring` | `claim_due_providers`; `tasks.py` is a thin scheduler entry point that only reads the interval. |
| Report filter contract | `ai.learning_utils` | Pure, offline-testable; the three report modules consume it. |

No responsibility gained a second owner. `get_failover_providers` was not
duplicated — it was refactored into `get_failover_provider_rows` and kept as a
one-line wrapper.

### Frappe v17 capabilities used

`frappe.cache()` (native Redis handle) with server-side Lua; `frappe.get_all`
with `or_filters` and `limit`; `frappe.db.get_value(..., for_update=True)` row
locks — **the same claim pattern PIPE-02 already established**, not a second
one; `frappe.qb` query builder in the reports; native **Script Report** +
`frappe.desk.query_report.run`; `frappe.has_permission`; DocType field defaults
and descriptions; `frappe.reload_doc` in an idempotent patch; the existing
scheduler cron.

No `limit_page_length` / `limit_start`, no parallel permission system, no
parallel job or lock subsystem, no new realtime channel.

**Decisions recorded:** [ADR-009](../ARCHITECTURE_DECISIONS.md) (Redis admission
control, degrade-open) and [ADR-010](../ARCHITECTURE_DECISIONS.md) (enforced
capability defaults to adapter transport).

---

## 3. Security and authorization

- **Reservation before logging.** GOV-03 reserves and GOV-01 leases *before*
  `start_execution_log`, so a refused call leaves no stale `Running` log row.
- **Administrator exemption preserved** exactly as before; no new bypass.
- **Reports** enforce `frappe.has_permission(ref_doctype, "report")` in the
  service function, not only through the Report wrapper's role list — the §7.2
  rule that service-layer authorization must not depend on the API wrapper.
- **Report filters** are validated against a declared contract; unknown keys are
  dropped and a known key with an invalid value raises rather than silently
  showing unfiltered data under a filtered label.
- **Row limit** on every learning report is bounded (500).
- **No new whitelisted method** was added in this part.

---

## 4. Data and migration

Patch `ai_fr_hg.patches.v0_0_21_phase_6_governance` (post-model-sync, idempotent):

1. Reloads the three learning report definitions and force-sets
   `report_type=Script Report`, `is_standard=Yes`, `query=''` — an installed
   site keeps running the old static SQL otherwise.
2. Backfills `AI Model` capability flags from each provider adapter's transport
   capability. **It only ever raises a flag**, never lowers one an operator set,
   and skips models whose adapter cannot be resolved.

`AI Model` DocType defaults changed to `supports_tools=1`,
`supports_json_mode=1`, `supports_streaming=1` (ADR-010). No data is deleted or
converted. `bench migrate` ran successfully in CI before the test step.

---

## 5. Tests and runtime verification

### Offline (no bench) — `ai_fr_hg/tests/test_phase_6_units.py`

```
python3 -m unittest discover -s ai_fr_hg/tests -p 'test_phase_6_units.py'
Ran 40 tests in 0.007s
OK
```

40 tests over the pure policy layer: GOV-04 type matrix, PROV-02 effective
capability and probe classification, PROV-01 ranking and determinism, LEARN-01
filter contract, PROV-02 discovery defaults, and Phase 6 source/registration
contracts.

### Real Frappe v17 bench — `ai_fr_hg/tests/test_phase_6_governance.py`

Hosted CI runs `bench --site test_site migrate` then
`bench --site test_site run-tests --app ai_fr_hg` against pinned Frappe
`d7000da3d5862087d3df08e009fe76518ea649c4`, MariaDB 11.8, Python 3.14, with real
Redis cache and queue services.

| Run | SHA | Checks | Result |
| --- | --- | --- | --- |
| [32465400357](https://github.com/Oscar-Hyde/ai_fr_hg/actions/runs/32465400357) | `07ef911` | Server | **FAIL** — 8 Phase 6 tests |
| [32465952941](https://github.com/Oscar-Hyde/ai_fr_hg/actions/runs/32465952941) | `fc8e50e` | Server (`Run server tests` step) | **PASS**, 2m47s |
| [32465953238](https://github.com/Oscar-Hyde/ai_fr_hg/actions/runs/32465953238) | `fc8e50e` | Linter, Frontend static, Dependency audit | **PASS**, 1m12s |

**The failing run is the evidence, not an embarrassment.** The directive
requires every fix to ship with a test that demonstrably fails before the fix
and passes after. Run `32465400357` is that "before": the new tests found two
defects that `compileall`, `ruff`, and the 40 offline tests all passed over.

1. **A production defect in `ai/limits.py`.** `_describe_scope` unpacked
   `str.partition()` into `_`, shadowing Frappe's translation function. Every
   concurrency and rate-limit rejection therefore raised
   `TypeError: 'str' object is not callable` instead of the intended policy
   error — GOV-01 and GOV-02 would have crashed on their first real rejection
   in production. Fixed, with all three scope prefixes now covered.
2. **A wrong test fixture.** It inserted `AI Memory` directly, which
   `before_insert` forbids: memories exist only as promoted
   `AI Knowledge Candidate` records. The fixture now honours that invariant
   rather than bypassing it.

> **Evidence limitation, stated plainly.** GitHub's Actions log-retrieval
> endpoint returned `EOF` on every attempt to download the raw stdout for run
> `32465952941` while this report was written, so the exact
> `Ran N tests … OK` line is not pasted here. What *is* verified: the `Server`
> job and its `Run server tests` step both report `conclusion: success` via the
> GitHub API, and the annotations for the preceding failing run list precisely
> the eight Phase 6 tests that the two fixes address. The Phase 7 gate should
> re-capture the full counted output once GitHub's log service recovers.

### Coverage by finding (real bench)

| Finding | Bench tests |
| --- | --- |
| GOV-01 | saturate-and-release, zero-means-unlimited, TTL expiry after simulated worker death, partial-acquisition rollback, named-scope error, all three scope prefixes, policy resolution of user/provider/model scopes |
| GOV-02 | burst refusal with measured retry delay, zero disables, per-scope independence, provider record read |
| GOV-03 | in-flight requests counted (the concurrent-overrun case the old check-then-use test could not stop), committed + in-flight combined, worst-case token budget, TTL expiry, idempotent release, no-limit no-op, Administrator exemption |
| GOV-04 | embedding-for-chat refused, chat-for-embedding refused, compatible resolution still works, `run_chat` refuses before `get_provider` is called |
| PROV-01 | failover targets are real models on other providers, embedding dimensions never crossed, execution log records the backup provider **and** backup model and dials the backup's own runtime model name |
| PROV-02 | non-regressive defaults, adapter ∧ model intersection, probe narrows then clears, tool request refused before any runtime call |
| OPS-02 | 7-minute interval honoured (the exact case minute-modulo broke), exactly-once claim, never-checked provider due, disabled excluded, source contract |
| LEARN-01 | Script Report registration, status filter filters, unfiltered returns both, numeric filter, injection-shaped value refused, `query_report.run` end-to-end, permission denied for a role-less user, date contract |

---

## 6. Frontend

No frontend work was required by part A. The only user-visible change is that
the three learning report filter sidebars now actually filter, and `AI Model`
capability checkboxes carry descriptions stating that they are enforced. The
Operations and Learning dashboards (OPS-03, LEARN-02) are part B.

---

## 7. Reconciliation with earlier phases

Checked, not assumed:

- **CHAT-07 turn cancellation** still works through the same `turn_id`;
  `_complete_chat` gained `allow_streaming` but the cancellation check in
  `_complete_via_stream` is untouched.
- **Streaming** now consults the *effective* capability instead of only the
  adapter flag. ADR-010 defaults keep every existing model streaming.
- **PIPE-02 atomic claiming** was reused for OPS-02 rather than reimplemented.
- **SEC-01/SEC-02 permission-aware querying** is unchanged; no limit path calls
  `frappe.db.count` for authorization.
- **Frappe v17 pagination discipline** holds: `ai/limits.py` and the new engine
  code contain no `limit_page_length` / `limit_start` (asserted by test).

---

## 8. Not completed — deferred with reason

Stated plainly, per directive §11.4. None of these were attempted and none are
silently dropped; all nine remain `OPEN` in the gap register with their owner.

| ID | Why not completed | Now targeted at |
| --- | --- | --- |
| PROV-03 | Needs a product decision per remaining field (`model_prefix`, version records, pull progress, delete/unload) before code; ADR-005 currently only hides versions. | Phase 6 part B, after the decision is recorded |
| OPS-03 | Substantial frontend work (timer lifecycle, charts, filters, drill-down, SLO cards) that depends on the metrics API OPS-05 completes. | Phase 6 part B |
| OPS-04 | Backup/restore is its own workstream: versioned manifest, streaming export, checksums, selective restore, embedding-compatibility validation, and an automated restore drill. | Phase 6 part B |
| OPS-05 | Audit/execution trace linkage and stale-`Running` reconciliation touch message, task, and step identities across four modules. | Phase 6 part B |
| OPS-06 | Bounded batching for backup/cleanup jobs; sequenced after OPS-04 so both use one batching contract. | Phase 6 part B |
| LEARN-02 | Learning Dashboard is a new Desk page; should be built on the shared frontend components the cross-phase requirement establishes, alongside OPS-03. | Phase 6 part B |
| LEARN-03 | Hybrid semantic recall must reuse the RET-03 mixed-embedding-model grouping rather than create a second grouping implementation. | Phase 6 part B |
| LEARN-04 | Skill relevance ranking depends on LEARN-03's recall scoring. | Phase 6 part B |
| LEARN-05 | Lifecycle maintenance (merge/supersede/archive/re-embed) depends on LEARN-03's embedding refresh job. | Phase 6 part B |

Carried in from earlier phases, unchanged by this work:

- **OPS-01** — branch protection remains `BLOCKED — OWNER ACTION`. The GitHub
  App still has no `administration:write`; `main` reports `protected: false`.
  The manual compensating control is unchanged: all four checks green in the PR
  UI before merge.
- **ING-06 / TRN-04** — OS-level RQ kill and Desk Stop/reconnect evidence remain
  Phase 7, as agreed in Phases 4 and 5.

---

## 9. Phase verdict

`FAIL` **as a phase** — and deliberately so. Phase 6 has seventeen registered
findings; eight are complete and verified on a real bench, nine have not been
started. A phase may not be marked `PASS` when required functionality remains
partial (directive §17), and Phase 7 must not begin.

Part A, assessed on its own scope, is `PASS`: every finding it claims is
implemented, integrated, migrated, permission-checked, and demonstrated on the
pinned Frappe v17 bench, with a documented before/after failing run.

**Next action:** Phase 6 part B, in register order — PROV-03 decision first
(it is the only remaining item blocked on a product decision rather than on
engineering), then OPS-05 → OPS-03, OPS-04 → OPS-06, then LEARN-03 → LEARN-04 →
LEARN-05 → LEARN-02.
