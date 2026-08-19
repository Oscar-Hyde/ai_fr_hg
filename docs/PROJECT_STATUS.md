# AI Fr HG — audited project status

**Status date:** 2026-08-19
**Current main revision:** `0d8848eb5178afeee2dd64b15c30c54dea6b899d`
**Latest merged change:** PR #27, integration compatibility fixes
**App version:** `0.0.1`
**Target declared by the project:** Frappe v17, Python 3.14+

This document is the current, branch-neutral status summary. The detailed findings, unfinished-function inventory, frontend/backend plan, sequencing, acceptance criteria, and release roadmap are in [`DEVELOPMENT_PLAN.md`](DEVELOPMENT_PLAN.md).

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
| Python test methods | 392 |
| Latest real-bench result | 392 passed, 1 skipped |
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
| Network guard | **PARTIAL / HARDENING REQUIRED** | Local/private validation exists. Connection-level DNS/proxy/redirect hardening is still required before using absolute “never leaves” claims. |
| Ingestion | **PARTIAL** | File, text, URL, and DocType-record main paths exist. Folder source is unsupported, scanned-PDF OCR is absent, `.msg` support is incomplete, and job progress/cancel need work. |
| Readers | **PARTIAL** | 37 extensions are registered, with optional dependency degradation. Registered extension does not always mean production-grade format support. |
| Document tree | **READY / HARDENING REQUIRED** | Strong identity, locking, permissions, bulk operations, concurrency checks, and tests. Deep/large tree browser and load validation remain. |
| File/folder organization | **PARTIAL / HARDENING REQUIRED** | Rich canonical service exists. Global Desk patches, native move fallback, stable File identity, default-folder semantics, and deep folder picker need revision. |
| Chunking/vector math | **READY** | Pure logic is well tested. |
| Retrieval | **PARTIAL — production blocker** | Hybrid flow works on small corpora. Semantic/keyword candidates are bounded before ranking, mixed KB embedding models are not reconciled, and KB-specific retrieval settings/weights are not fully applied. |
| Knowledge Explorer | **PARTIAL** | Search/ask/upload/overview work. Pagination, folder/entity facets, diagnostics, and robust upload progress remain. |
| Intelligence | **PARTIAL** | Summary/classify/extract/compare main paths exist. Target DocType mapping is unused; whole-document strategies and strict local schema validation remain. |
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
| Encryption | **DECLARED ONLY** | The visible setting does not encrypt stored document/chunk/translation text. It must be implemented or removed/hidden. |
| CI/release | **BLOCKED** | Local/bench tests pass, but GitHub Actions jobs do not start because of an account billing/spending-limit error. Versioning/release qualification also remains. |
| Frontend validation | **PARTIAL** | JavaScript parses and pages are implemented, but there are no JS unit, browser E2E, accessibility, or responsive tests. |

---

## Highest-priority open work

### P0 — fix before production use

1. Scope translation memory on inline and tool paths; no scope must never mean all knowledge bases.
2. Make retrieval correct beyond the first bounded chunk candidates and support multiple embedding models across KBs.
3. Make generic tool counts and field output permission-aware.
4. Enforce API input caps and pagination for messages, conversations, chunks, entities, translations, and searches.
5. Harden provider networking at connection time against proxy/DNS/redirect weaknesses.
6. Remove the native File mutation fallback that can bypass canonical AI-document provenance updates.
7. Require stable File identities in upload/folder APIs.
8. Implement or hide the document-encryption setting.
9. Restore GitHub Actions execution and require checks before merge.

### P1 — complete existing product promises

1. Enforce resource/provider/model concurrency and provider rate limits.
2. Fix latest conversation history and atomic message sequencing.
3. Complete conversation route state, cancel/retry, pin/rename/archive/restore, and feedback correction UX.
4. Implement/remove Folder source, scanned-PDF OCR, `.msg`, reranker, model versions, and extraction target mapping.
5. Add pipeline API/document-ingest triggers, atomic schedule claims, waiting-approval resume, and a typed builder.
6. Complete AI Task types, state transitions, scheduling, audit links, and UI.
7. Add translation/pattern/ingestion progress and cancellation.
8. Build Learning and Pattern Explorer dashboards and fix reports.
9. Make export/import a versioned, tested restore path.
10. Add browser, real-runtime, optional dependency, load, concurrency, security, and migration tests.

---

## Confirmed disconnected or inert controls

The complete analysis is in the development plan. The most important current examples are:

- `AI Platform Settings.encrypt_documents` — unused.
- `AI Resource Policy.max_concurrent_requests` — unused.
- `AI Provider.max_concurrent_requests` and `rate_limit_per_minute` — unused.
- `AI Model.max_concurrent_requests`, `supports_json_mode`, and `versions` — not effectively connected.
- `AI Provider.model_prefix` — unused.
- `AI Agent.fallback_answer` — unused.
- `AI Agent Knowledge Base.weight` — unused.
- `AI Conversation.context_document` — unused.
- `AI Message.execution_log` and `AI Task.execution_log` — not populated.
- `AI Execution Log.queue_time_ms` — not populated.
- `AI Folder Settings.knowledge_tag` and `is_archived` — unused.
- `AI Prompt Variable.variable_type` — unused.
- `AI Extraction Schema.target_doctype` — unused.
- `AI Usage Snapshot.document_count` — unused.
- `AI Document` source type `Folder` — offered but rejected by ingestion.
- `Reranker` model type — no reranking execution path.
- Platform translation index-output default — loaded but not applied when new translations are created.

Each requires an implement/repurpose/hide/remove decision.

---

## Quality and CI status

### Passing

- Real-bench Python test suite: 392 passed, 1 skipped.
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

Recent CI/Linter jobs have zero steps and fail immediately. GitHub’s check annotation says:

> The job was not started because recent account payments have failed or your spending limit needs to be increased.

This is an account/repository operations issue, not a passing or failing code test. It must be fixed before CI can be treated as a merge gate.

---

## Documentation status

The repository has strong explanatory documents, but several claims need revision until their implementation work is complete:

- “complete” platform;
- absolute local-only/no-egress guarantees;
- scanned-PDF OCR;
- `.msg` support;
- full knowledge backup/restore with embeddings;
- all settings being effective;
- all pipeline trigger types being active;
- all AI Task types being implemented;
- retrieval behavior at enterprise corpus sizes.

The active revision plan is [`DEVELOPMENT_PLAN.md`](DEVELOPMENT_PLAN.md). README and feature guides should be updated alongside the corresponding implementation phases.

---

## Recommended immediate sequence

1. Repair GitHub Actions billing/spending limits and rerun all checks.
2. Fix translation-memory scope with multi-KB regression tests.
3. Fix generic tool row/field permissions.
4. Remove unsafe File fallback and require stable File identity.
5. Add shared public API bounds/validation.
6. Hide the unimplemented encryption field unless a real design is approved.
7. Rebuild retrieval for full-corpus correctness and mixed embedding models.
8. Only then proceed to Assistant UX, ingestion/translation progress, and automation/task completion.

See the roadmap for phased effort, frontend/backend deliverables, migration strategy, and exit criteria.
