# AI Fr HG — audited project status

**Status date:** 2026-08-21 (Phase 6 part A)
**App version:** `0.0.1`
**Framework baseline:** pinned Frappe `17.0.0-dev`, Python `>=3.14,<3.15`, Node 24, MariaDB 11.8
**Release qualification:** not production-ready; upstream stable Frappe v17 does not yet exist

This branch-neutral summary is controlled by the detailed
[`DEVELOPMENT_PLAN.md`](DEVELOPMENT_PLAN.md),
[`GAP_REGISTER.md`](GAP_REGISTER.md), and
[`ARCHITECTURE_DECISIONS.md`](ARCHITECTURE_DECISIONS.md).

---

## Executive status

AI Fr HG is a **feature-rich technical beta**. It has a broad and coherent tested core, but it is not yet production-complete.

The previous version of this document described nearly every module as READY or IMPLEMENTED and contained stale branch/PR/site instructions. That classification was too optimistic. A repository-wide audit found a mixture of:

- complete and well-tested main paths;
- small-corpus or expected-input implementations;
- backend building blocks without complete UI workflows;
- visible settings/fields that do not currently affect behavior;
- security, isolation, scale, concurrency, and operational work required before production release.

### Current baseline

| Measure | Value |
| --- | ---: |
| DocTypes | 47 |
| Whitelisted methods | 118 |
| Custom Desk pages | 4 |
| Workspaces | 5 |
| Reports | 3 |
| Audited pre-Phase-0 Python test methods | 392 |
| Current verified real-bench suite | 565 passed, 1 skipped (CI run 32395611779, 2m27s) |
| Production JavaScript files | 56 |
| JavaScript unit test files | 1 (Node); browser E2E still 0 |

The supplied `site1.local` run passed:

- 194 unit tests;
- 179 integration tests (1 skipped);
- 19 other-category tests.

Green tests prove the covered paths. They do not cover browser behavior, real runtimes, optional parsers/OCR, large-corpus retrieval, concurrency/load, hostile network inputs, or release upgrades.

---

## Status legend

| Label | Meaning |
| --- | --- |
| **READY** | Main path, permission path, failure path, UI path, and meaningful automated coverage exist. |
| **PARTIAL** | Main path exists, but a material scale, failure, UX, or integration path remains. |
| **DISCONNECTED** | Schema/backend/API building block exists but is not connected to its intended workflow. |
| **DECLARED ONLY** | Visible field/setting/option has no effective implementation. |
| **HARDENING REQUIRED** | Functional, but not safe/reliable enough for production use. |

---

## Capability status

| Area | Current status | Summary |
| --- | --- | --- |
| Provider adapters | **PARTIAL / HARDENING REQUIRED** | Ollama and OpenAI-compatible paths work. Provider/model concurrency, provider rate limits, effective capability enforcement, and equivalent-model failover are enforced and bench-tested (GOV-01/02, PROV-01/02). `model_prefix`, model versions, pull progress, and delete/unload remain (PROV-03). |
| Network guard | **READY / HARDENING REQUIRED** | Connection-level transport hardening is implemented and runtime-tested (pinned dial, no env proxies, redirect refusal, peer revalidation, allowlist policy). Browser/E2E remains Phase 7. |
| Ingestion | **PARTIAL** | File, text, URL, and DocType-record main paths exist. Unsupported Folder source and `.msg` exposure were removed; scanned-PDF OCR is explicitly unsupported. Progress/cancel and heartbeat reaper are on hosted Server; OS-level RQ kill remains Phase 7. |
| Readers | **PARTIAL** | 36 extensions are registered with optional dependency degradation. PDF means text-layer extraction; OCR applies to image files, not scanned PDFs. |
| Document tree | **READY / HARDENING REQUIRED** | Strong identity, locking, permissions, bulk operations, concurrency checks, and tests. Deep/large tree browser and load validation remain. |
| File/folder organization | **READY / HARDENING REQUIRED** | Canonical service is the sole mutation owner: native paste fallback removed, stable File identity enforced, storage folder is a validated File Link, “Shared Uploads”/“Public” naming is truthful, uploader uses a lazy native Link picker, and Desk patches are version-gated. Browser verification remains Phase 7. |
| Chunking/vector math | **READY** | Pure logic is well tested. |
| Retrieval | **READY / HARDENING REQUIRED** | Complete brute-force hybrid retrieval, mixed-model grouping, per-KB policy, folder descendants, context packing, and diagnostics. Browser E2E and 100k-chunk load remain Phase 7. |
| Knowledge Explorer | **PARTIAL** | Search/ask/upload/overview, diagnostics, pagination, folder and entity filters. Upload progress and browser E2E remain Phase 7. |
| Intelligence | **PARTIAL** | Summary/classify/extract/compare main paths exist. Extraction is explicitly JSON-only and the dormant target DocType field is hidden; whole-document strategies and strict local schema validation remain. |
| Pattern extraction | **PARTIAL** | Durable zero-result scan, original-document tail offsets, semantic IP/date/money validators, and Pattern Explorer API/page are on hosted Server. Browser explorer UX remains Phase 7. |
| Translation | **PARTIAL** | Strong segmentation, masking, quality checks, repair, review, memory, and indexing core. Memory requires authorized KB scope and policy identity (SEC-01/TRN-01). Worker restoration, glossary/KB parity, index default, and usage accounting (TRN-05) are closed on hosted Server. Progress/cancel Desk Stop/reconnect remains Phase 7. |
| Assistant/agents | **READY / HARDENING REQUIRED** | Latest-N history, locked sequence + turn_id, route-state restore, focused document, fallback answer, footnote citations, rename/pin/archive/export, negative-feedback reason/correction, cooperative cancel/reconnect, and stable File identity on upload. Browser E2E remains Phase 7. |
| Tools/approvals | **PARTIAL / HARDENING REQUIRED** | Approval, audit, argument validation, permissions, and runtime limit exist. Generic count/field isolation, defaults, expiry, async approval execution, and pipeline resume remain. |
| Pipelines | **READY / HARDENING REQUIRED** | API, Document Ingest, and Scheduled triggers are wired. Atomic `next_run_on` claims with misfire policy. Waiting Approval pauses and resumes exactly once after tool approval. Typed step config on the form. Browser builder/run E2E remains Phase 7. |
| Automation | **READY / HARDENING REQUIRED** | Immutable event snapshots (including `on_trash`), source-field contracts, atomic counters, and revision/coalesce dedupe. Browser wizard E2E remains Phase 7. |
| AI Tasks | **READY / HARDENING REQUIRED** | Every task type has an explicit contract. Status is server-authorized; AI Users cannot self-approve. Form actions match canonical states. Browser workflow remains Phase 7. |
| Governance | **READY / HARDENING REQUIRED** | Request/token/document limits and capability gates exist. User/provider/model concurrency limits, provider rate limits, and atomic quota reservations are enforced through Redis-backed admission control (GOV-01/02/03), and explicit model resolution validates model type (GOV-04). Load and saturation behaviour under real concurrency remains Phase 7. |
| Learning | **PARTIAL** | Candidate validation/promotion/recall/feedback core is strong. Reports are Script Reports with validated, working filters and a service-layer permission check (LEARN-01). Learning dashboard (LEARN-02), semantic recall (LEARN-03), skill relevance (LEARN-04), and lifecycle maintenance (LEARN-05) remain. |
| Operations | **PARTIAL** | Readiness, health, usage snapshot, failures, approvals, and queues are visible. Health checks are scheduled from real timestamps and claimed exactly once (OPS-02). SLOs, charts, job detail, stale-state reconciliation, timer lifecycle, bounded cleanup, and a real restore path remain (OPS-03/04/05/06). |
| Backup/import/export | **PARTIAL — restore work required** | Text JSON round-trip exists. Exported embeddings are ignored by import; component completeness, streaming, format version, retention, and restore drills remain. |
| Encryption | **INTENTIONALLY UNSUPPORTED** | The dormant compatibility field is hidden/read-only, reset to 0, and rejected server-side. Use deployment-layer encrypted storage/database/backups. |
| CI/release | **PARTIAL — owner branch protection** | Hosted Server, Linter, Frontend static, and Dependency audit are green on the Phase 2 PR and on `main` after Phase 1. Branch protection on `main` is still off (OPS-01; GitHub App HTTP 403). |
| Frontend validation | **PARTIAL** | Shared route-state/RPC/realtime helpers have Node unit tests. Browser E2E, accessibility, and responsive tests remain Phase 7. |

---

## Highest-priority open work

### P0 — fix before production use

1. Repository owner must require Server, Linter, Frontend static, and Dependency audit on `main` (OPS-01; the GitHub App cannot enable branch protection).

Phases 0–5 are closed. **Phase 6 is OPEN:** eight of its seventeen findings
(GOV-01/02/03/04, PROV-01/02, OPS-02, LEARN-01) are closed and verified on the
pinned Frappe v17 bench; nine remain (PROV-03, OPS-03/04/05/06,
LEARN-02/03/04/05). See [`phase-reports/PHASE_6.md`](phase-reports/PHASE_6.md).
Browser E2E remains Phase 7.

### P1 — complete existing product promises

1. ~~Enforce resource/provider/model concurrency and provider rate limits.~~ **Closed in Phase 6 part A** (GOV-01/02/03, PROV-01/02, OPS-02, LEARN-01).
2. Keep removed Folder/`.msg`/reranker/target-mapping controls absent; implement translation/ingestion progress, parser hardening, and provider model lifecycle. Scanned-PDF OCR and original-format reconstruction remain intentionally unsupported unless a future decision supersedes Phase 0.
3. Add pipeline API/document-ingest triggers, atomic schedule claims, waiting-approval resume, and a typed builder.
4. Complete AI Task types, state transitions, scheduling, audit links, and UI.
5. Phase 4 remaining proof is Phase 7 browser/chaos (ING-06/TRN-04/PAT-04). Phase 5 automation/pipeline/task contracts are implemented on this branch; hosted Server verification is required before a Phase 5 PASS.
6. Build the Learning dashboard (LEARN-02); reports are fixed (LEARN-01). Pattern Explorer backend exists.
7. Make export/import a versioned, tested restore path.
8. Add browser, real-runtime, optional dependency, load, concurrency, security, and migration tests.

---

## Confirmed disconnected or inert controls

The complete analysis is in the development plan. The most important current examples are:

- `AI Resource Policy.max_concurrent_requests` — **enforced** (GOV-01).
- `AI Provider.max_concurrent_requests` and `rate_limit_per_minute` — **enforced** (GOV-01/GOV-02).
- `AI Model.max_concurrent_requests` — **enforced** (GOV-01). `supports_tools`,
  `supports_streaming`, `supports_json_mode` and `supports_vision` — **enforced**
  (PROV-02), seeded from adapter transport by discovery and by patch
  `v0_0_21` so enforcement is non-regressive (ADR-010). The unimplemented
  Versions table is retained but hidden/read-only (ADR-005; PROV-03 open).
- `AI Provider.model_prefix` — still unused; awaiting the PROV-03 implement-or-remove decision.
- `AI Message.execution_log` and `AI Task.execution_log` — not populated.
- `AI Execution Log.queue_time_ms` — not populated.
- `AI Folder Settings.knowledge_tag` and `is_archived` — unused.
- `AI Prompt Variable.variable_type` — unused.
- `AI Extraction Schema.target_doctype` — retained for compatibility but hidden/read-only; extraction is JSON-only.
- `AI Usage Snapshot.document_count` — unused.
- Folder source, `.msg`, and Reranker choices — removed from supported metadata/registration; legacy database rows are preserved where applicable.

Remaining visible controls require implementation, repurposing, or removal in
their owning phase. Phase 0 decisions are recorded in
[`ARCHITECTURE_DECISIONS.md`](ARCHITECTURE_DECISIONS.md).

---

## Quality and CI status

### Passing

- Last audited pre-Phase-0 real-bench suite: 392 passed, 1 skipped.
- Current post-Phase-3 real-bench suite: **565 passed, 1 skipped** on CI run `32395611779` (Server, 2m27s). The count includes the 100-worker concurrent conversation test.
- Python compile check.
- JSON parsing.
- JavaScript syntax check.
- Current document-tree, folder, translation, pattern, learning, pipeline, tools, model, provider, and permission regression tests shown in the supplied run.

### Not yet covered adequately

- Browser/Desk end-to-end flows.
- Broader JavaScript unit coverage (route/RPC/realtime helpers exist).
- Accessibility and mobile behavior.
- Real Ollama/OpenAI-compatible runtime behavior.
- PDF/Office/OCR optional dependencies as a matrix.
- Large-corpus retrieval correctness/performance.
- Multi-request concurrency and quota races.
- DNS/proxy/redirect hostile networking in a real provider matrix (unit coverage exists for SEC-04).
- Backup restore completeness.
- Upgrade from prior public data snapshots.
- Worker death, Redis outage, and stale-running reconciliation.

### GitHub Actions

The Phase 0 workflow definitions pin Frappe v17 development and define
Server, Linter, Frontend static, and Dependency audit statuses. Those four
checks execute and pass on current `main` and on the post-Phase-3 verification
push (Server run `32395611779`; Quality run `32395611703`). GitHub still reports
`main` as unprotected (`protected: false`); the branch-protection API returns
HTTP 403, and repository metadata reports the installed integration has no
administration permission. Branch protection is therefore **not platform
active**. Until the repository owner grants the GitHub App repository
`administration:write` permission and enables required-status protection, the
manual merge rule is: the PR UI must show green Server, Linter, Frontend
static, and Dependency audit checks before merge.

---

## Documentation status

Phase 0 removed or narrowed claims for encryption, Folder ingestion, `.msg`,
reranking, target-DocType extraction, model versions, scanned-PDF OCR, and
original-format translation. Documentation now identifies local-network,
retrieval-scale, failover, governance, backup/restore, workflow, and test gaps.
Claim regressions remain part of the quality gate.

The active revision plan is [`DEVELOPMENT_PLAN.md`](DEVELOPMENT_PLAN.md); every
future implementation phase must update its owning guide with evidence.

---

## Recommended immediate sequence

1. Repository owner: require Server, Linter, Frontend static, and Dependency audit on `main` (OPS-01).
2. Phase 6 part B, in register order: the PROV-03 implement-or-remove decision, then OPS-05 → OPS-03, OPS-04 → OPS-06, then LEARN-03 → LEARN-04 → LEARN-05 → LEARN-02.
3. Phase 7 only after Phase 6 closes with all seventeen findings resolved.

See the roadmap for phased effort, frontend/backend deliverables, migration strategy, and exit criteria.
