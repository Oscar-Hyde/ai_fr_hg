# AI Fr HG — audited project status

**Status date:** 2026-08-20
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
| Whitelisted methods | 117 |
| Custom Desk pages | 4 |
| Workspaces | 5 |
| Reports | 3 |
| Audited pre-Phase-0 Python test methods | 392 |
| Latest verified real-bench baseline | 392 passed, 1 skipped (before current Phase 0 changes) |
| Production JavaScript files | 52 |
| JavaScript/browser tests | 0 |

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
| Provider adapters | **PARTIAL / HARDENING REQUIRED** | Ollama and OpenAI-compatible paths work. Provider/model concurrency, provider rate limits, capability enforcement, equivalent-model failover, versions, and full model lifecycle remain. |
| Network guard | **READY / HARDENING REQUIRED** | Connection-level transport hardening is implemented and runtime-tested (pinned dial, no env proxies, redirect refusal, peer revalidation, allowlist policy). Browser/E2E remains Phase 7. |
| Ingestion | **PARTIAL** | File, text, URL, and DocType-record main paths exist. Unsupported Folder source and `.msg` exposure were removed; scanned-PDF OCR is explicitly unsupported; warnings/progress/cancel remain. |
| Readers | **PARTIAL** | 36 extensions are registered with optional dependency degradation. PDF means text-layer extraction; OCR applies to image files, not scanned PDFs. |
| Document tree | **READY / HARDENING REQUIRED** | Strong identity, locking, permissions, bulk operations, concurrency checks, and tests. Deep/large tree browser and load validation remain. |
| File/folder organization | **READY / HARDENING REQUIRED** | Canonical service is the sole mutation owner: native paste fallback removed, stable File identity enforced, storage folder is a validated File Link, “Shared Uploads”/“Public” naming is truthful, uploader uses a lazy native Link picker, and Desk patches are version-gated. Browser verification remains Phase 7. |
| Chunking/vector math | **READY** | Pure logic is well tested. |
| Retrieval | **READY / HARDENING REQUIRED** | Complete brute-force hybrid retrieval, mixed-model grouping, per-KB policy, folder descendants, context packing, and diagnostics. Browser E2E and 100k-chunk load remain Phase 7. |
| Knowledge Explorer | **PARTIAL** | Search/ask/upload/overview, diagnostics, pagination, folder and entity filters. Upload progress and browser E2E remain Phase 7. |
| Intelligence | **PARTIAL** | Summary/classify/extract/compare main paths exist. Extraction is explicitly JSON-only and the dormant target DocType field is hidden; whole-document strategies and strict local schema validation remain. |
| Pattern extraction | **PARTIAL** | Strong deterministic extraction and tests. Durable zero-result scan state, correct tail offsets, semantic value validation, and aggregate explorer remain. |
| Translation | **PARTIAL — isolation fix required** | Strong segmentation, masking, quality checks, repair, review, memory, and indexing core. Unscoped inline memory, policy-aware memory identity, progress/cancel, default index setting, and format output remain. |
| Assistant/agents | **PARTIAL** | Chat, retrieval, citations, tools, streaming, and friendly runtime failures work. Latest-history selection, route state, cancellation, focused document, fallback answer, KB weights, and conversation UX remain. |
| Tools/approvals | **PARTIAL / HARDENING REQUIRED** | Approval, audit, argument validation, permissions, and runtime limit exist. Generic count/field isolation, defaults, expiry, async approval execution, and pipeline resume remain. |
| Pipelines | **PARTIAL** | Ordered/nested execution, cycle guards, step logs, retries, cancel, and provenance exist. Trigger wiring, schedule claiming, resumable approval, and visual builder remain. |
| Automation | **PARTIAL** | Cached event dispatch and main actions exist. Delete-event snapshots, source validation, atomic counters, revision-aware dedupe, and execution coverage remain. |
| AI Tasks | **PARTIAL / DECLARED ONLY** | Several paths execute, but Compare/Custom, due date, priority, execution log, secure approval transitions, and matching UI are unfinished. |
| Governance | **PARTIAL — enforcement work required** | Request/token/document limits and capabilities exist. User/provider/model concurrency, rate limiting, and atomic quota reservations are not implemented. |
| Learning | **PARTIAL** | Candidate validation/promotion/recall/feedback core is strong. Learning dashboard, report wiring, semantic recall, skill relevance, and lifecycle maintenance remain. |
| Operations | **PARTIAL** | Readiness, health, usage snapshot, failures, approvals, and queues are visible. SLOs, charts, job detail, stale-state reconciliation, and timer lifecycle remain. |
| Backup/import/export | **PARTIAL — restore work required** | Text JSON round-trip exists. Exported embeddings are ignored by import; component completeness, streaming, format version, retention, and restore drills remain. |
| Encryption | **INTENTIONALLY UNSUPPORTED** | The dormant compatibility field is hidden/read-only, reset to 0, and rejected server-side. Use deployment-layer encrypted storage/database/backups. |
| CI/release | **BLOCKED** | Hosted Server is green on `main` (run `32325110514`). Quality fails on Semgrep Cloud + editable pip-audit. Branch protection is not active; the Arena GitHub App cannot write workflows or administer protection. |
| Frontend validation | **PARTIAL** | JavaScript parses and pages are implemented, but there are no JS unit, browser E2E, accessibility, or responsive tests. |

---

## Highest-priority open work

### P0 — fix before production use

1. Restore GitHub Actions execution and require checks before merge (owner branch protection; OPS-01).
2. Phase 3 conversation correctness (latest-history, concurrent sequencing, cancellation).

Phase 1 isolation/API safety and Phase 2 retrieval correctness are closed on this branch.

### P1 — complete existing product promises

1. Enforce resource/provider/model concurrency and provider rate limits.
2. Fix latest conversation history and atomic message sequencing.
3. Complete conversation route state, cancel/retry, pin/rename/archive/restore, and feedback correction UX.
4. Keep removed Folder/`.msg`/reranker/target-mapping controls absent; implement translation/ingestion progress, parser hardening, and provider model lifecycle. Scanned-PDF OCR and original-format reconstruction remain intentionally unsupported unless a future decision supersedes Phase 0.
5. Add pipeline API/document-ingest triggers, atomic schedule claims, waiting-approval resume, and a typed builder.
6. Complete AI Task types, state transitions, scheduling, audit links, and UI.
7. Add translation/pattern/ingestion progress and cancellation.
8. Build Learning and Pattern Explorer dashboards and fix reports.
9. Make export/import a versioned, tested restore path.
10. Add browser, real-runtime, optional dependency, load, concurrency, security, and migration tests.

---

## Confirmed disconnected or inert controls

The complete analysis is in the development plan. The most important current examples are:

- `AI Resource Policy.max_concurrent_requests` — unused.
- `AI Provider.max_concurrent_requests` and `rate_limit_per_minute` — unused.
- `AI Model.max_concurrent_requests` and `supports_json_mode` — not effectively connected; the unimplemented Versions table is retained but hidden/read-only.
- `AI Provider.model_prefix` — unused.
- `AI Agent.fallback_answer` — unused.
- `AI Agent Knowledge Base.weight` — unused.
- `AI Conversation.context_document` — unused.
- `AI Message.execution_log` and `AI Task.execution_log` — not populated.
- `AI Execution Log.queue_time_ms` — not populated.
- `AI Folder Settings.knowledge_tag` and `is_archived` — unused.
- `AI Prompt Variable.variable_type` — unused.
- `AI Extraction Schema.target_doctype` — retained for compatibility but hidden/read-only; extraction is JSON-only.
- `AI Usage Snapshot.document_count` — unused.
- Folder source, `.msg`, and Reranker choices — removed from supported metadata/registration; legacy database rows are preserved where applicable.
- Platform translation index-output default — loaded but not applied when new translations are created.

Remaining visible controls require implementation, repurposing, or removal in
their owning phase. Phase 0 decisions are recorded in
[`ARCHITECTURE_DECISIONS.md`](ARCHITECTURE_DECISIONS.md).

---

## Quality and CI status

### Passing

- Last audited pre-Phase-0 real-bench suite: 392 passed, 1 skipped.
- Python compile check.
- JSON parsing.
- JavaScript syntax check.
- Current document-tree, folder, translation, pattern, learning, pipeline, tools, model, provider, and permission regression tests shown in the supplied run.

### Not yet covered adequately

- Browser/Desk end-to-end flows.
- JavaScript unit tests.
- Accessibility and mobile behavior.
- Real Ollama/OpenAI-compatible runtime behavior.
- PDF/Office/OCR optional dependencies as a matrix.
- Large-corpus retrieval correctness/performance.
- Multi-request concurrency and quota races.
- DNS/proxy/redirect hostile networking.
- Backup restore completeness.
- Upgrade from prior public data snapshots.
- Worker death, Redis outage, and stale-running reconciliation.

### GitHub Actions

The Phase 0 workflow definitions now pin Frappe v17 development and define
Server, Linter, Frontend static, and Dependency audit statuses. Recent hosted
jobs still have zero steps and fail immediately. GitHub’s check annotation says:

> The job was not started because recent account payments have failed or your spending limit needs to be increased.

This is an account/repository operations issue, not a passing or failing code
test. In addition, GitHub reports `main` as unprotected and the current private
repository plan rejects rulesets. The repository owner must remediate the
account/plan and require all four checks before Phase 0 can pass.

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

1. Repair GitHub Actions billing/spending/plan limits, run all four checks, and protect `main`.
2. Stop; do not begin Phase 1 until the Phase 0 report can truthfully pass.
3. Then fix translation-memory scope, generic tool permissions, provider transport, File ownership, and shared API bounds in the registered Phase 1 order.
4. Rebuild retrieval only after the Phase 1 review passes.

See the roadmap for phased effort, frontend/backend deliverables, migration strategy, and exit criteria.
