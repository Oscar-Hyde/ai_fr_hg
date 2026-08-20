# Controlled audit gap register

**Register date:** 2026-08-19
**Authoritative finding source:** [`DEVELOPMENT_PLAN.md`](DEVELOPMENT_PLAN.md)
**Change control:** update a row only with its implementation evidence and phase review.
**Count:** 79 findings.

Status meanings:

- `OPEN` — owning phase has not completed the finding.
- `IN PROGRESS` — implementation/evidence is being produced in the current phase.
- `BLOCKED` — required work depends on an explicitly named external condition.
- `CLOSED — REMOVED` — unsupported behavior and its promise were comprehensively removed/hidden, with compatibility handling and regression evidence.
- `CLOSED — SCOPED` — product behavior was explicitly narrowed to what is implemented, with UI/docs/tests aligned.
- `CLOSED — IMPLEMENTED` — the complete acceptance contract passed.

A closed row is not permission to weaken the audit's cross-cutting requirements. Phase 7 still validates integrated behavior.

| ID | Finding | Phase | Owner | Status | Disposition | Required acceptance evidence |
| --- | --- | ---: | --- | --- | --- | --- |
| SEC-01 | Translation memory can be unscoped | 1 | Translation + Security | OPEN | Implement explicit authorized KB scope and policy identity. | Multi-user, multi-KB document/inline/tool isolation tests; no-scope test returns no lookup. |
| SEC-02 | Generic count bypasses row-level permissions | 1 | Tools + Security | OPEN | Centralize safe document query/count. | Manager/user/auditor/unrelated/unauthorized row-permission parity tests. |
| SEC-03 | Generic document tools expose fields unsafely | 1 | Tools + Security | OPEN | Enforce field read level and sensitive-field deny rules centrally. | Field-level permission and sensitive-field exfiltration tests across list/get. |
| SEC-04 | Local-only networking lacks connection-level enforcement | 1 | Provider + Security | OPEN | Harden transport, DNS, proxy, redirects, and address policy. | IPv4/IPv6, rebinding, redirect, proxy, allowlist, and connection-address tests. |
| SEC-05 | Stored-document encryption control is not implemented | 0 | Security | CLOSED — REMOVED | Compatibility field hidden/read-only, reset to 0, and rejected server-side; deployment encryption documented. | Metadata/controller/patch tests and product-claim regression. |
| SEC-06 | Folder-settings list visibility is too broad | 1 | File/Folder + Security | OPEN | Align list and direct-document permissions through Frappe permission hooks. | Cross-role list/get permission parity tests. |
| SEC-07 | Search telemetry bypasses logging redaction | 1 | Logging + Security | OPEN | Apply canonical redaction/retention policy before telemetry persistence. | Prompt/result redaction and retention tests with hostile patterns. |
| RET-01 | Semantic ranking truncates the corpus before ranking | 2 | Retrieval | OPEN | Implement complete supported-scale candidate evaluation. | Only-relevant-result-beyond-200 correctness test and large-corpus profile. |
| RET-02 | Keyword ranking truncates candidates | 2 | Retrieval | OPEN | Remove arbitrary pre-ranking correctness cap. | Only-relevant-result-beyond-old-cap keyword test. |
| RET-03 | Mixed embedding models are compared together | 2 | Retrieval | OPEN | Group compatible models/dimensions and fuse ranks. | Mixed-model/dimension retrieval and degraded fallback tests. |
| RET-04 | KB top-k, threshold, and weights are ignored | 2 | Retrieval | OPEN | Enforce policy and explicit override precedence. | Independent and combined policy-effect tests plus diagnostics. |
| RET-05 | Reranker is declared without execution | 0 | Retrieval | CLOSED — REMOVED | Model choice/discovery removed; legacy rows preserved and disabled. | Metadata, controller, discovery, and migration tests. |
| RET-06 | Oversized first context block can yield no context | 2 | Retrieval | OPEN | Pack useful bounded context, including block truncation/splitting. | Oversized-first-result and token/character-budget tests. |
| RET-07 | Folder subtree filtering uses unsafe prefix matching | 2 | Retrieval + File/Folder | OPEN | Use exact folder/descendant semantics. | Sibling-prefix and deep-descendant permission tests. |
| CHAT-01 | History selects oldest rather than latest messages | 3 | Conversation | OPEN | Load latest N and restore chronological prompt order. | 100+ message latest-context test. |
| CHAT-02 | Concurrent message sequence allocation races | 3 | Conversation | OPEN | Add turn identity and transactional sequence allocation. | Concurrent-send ordering/uniqueness tests. |
| CHAT-03 | Existing conversation state is not synchronized | 3 | Conversation + Frontend | OPEN | Restore persisted agent/model/KB/document configuration. | Reload/deep-link browser test and backend state test. |
| CHAT-04 | Focus document, fallback, weights, citations are disconnected | 3 | Conversation + Retrieval | OPEN | Wire all configuration through canonical retrieval/agent services. | Per-control behavioral and reload tests. |
| CHAT-05 | End-user conversation actions are missing | 3 | Conversation + Frontend | OPEN | Add rename, pin, archive/restore, pagination, export. | Permission-aware API and browser workflow tests. |
| CHAT-06 | Negative feedback lacks reason/correction | 3 | Learning + Frontend | OPEN | Persist reason/correction and connect Learning flow. | Feedback validation, permission, persistence, and browser tests. |
| CHAT-07 | Turn cancellation/reconnect is absent | 3 | Conversation + Jobs | OPEN | Persist turn status, cancellation, retry, reconnect recovery. | Worker cancellation, duplicate cancel/retry, and reconnect tests. |
| CHAT-08 | Attachment identity is inconsistent | 3 | Conversation + File/Folder | OPEN | Require stable File identity and processing state. | Ambiguous-URL, permission, upload-progress, and reconnect tests. |
| ING-01 | Folder source is non-functional | 0 | Ingestion + File/Folder | CLOSED — REMOVED | Select option removed; server rejects; native File remains sole folder authority. | Metadata/controller tests and legacy-row preservation statement. |
| ING-02 | Scanned-PDF OCR is advertised but absent | 0 | Ingestion | CLOSED — SCOPED | PDF text extraction and image OCR retained; scanned-PDF OCR promise/guidance removed. | Reader-warning and documentation contract tests. |
| ING-03 | `.msg` is not real Outlook parsing | 0 | Readers | CLOSED — REMOVED | `.msg` registry/UI claim removed; `.eml` remains supported. | Reader registry and supported-format API tests. |
| ING-04 | OpenDocument archive bomb validation is missing | 4 | Readers + Security | OPEN | Add bounded secure archive validation before parsing. | Compression ratio, member count/size, traversal, corruption, and timeout tests. |
| ING-05 | Reader warnings are not durable | 4 | Ingestion + Frontend | OPEN | Persist warnings and present partial processing state. | Persistence/retry/browser warning tests. |
| ING-06 | Processing lacks progress/cancel/recovery UX | 4 | Ingestion + Jobs + Frontend | OPEN | Implement durable job state, progress, cancel, recovery. | Worker failure/cancel/reconnect/browser tests. |
| INT-01 | Extraction target DocType is unused | 0 | Intelligence | CLOSED — REMOVED | Dormant field hidden/read-only; extraction described as JSON-only. | Metadata and documentation contract tests; existing values preserved. |
| INT-02 | Structured output is not fully validated locally | 4 | Intelligence | OPEN | Validate every output against local schema and fail safely. | Type/required/enum/nested/extra-field/provider-malformation tests. |
| INT-03 | Long summarization reduction is lossy | 4 | Intelligence | OPEN | Use bounded hierarchical coverage-preserving reduction. | Long-document fact-retention and coverage diagnostics tests. |
| INT-04 | Compare/classify/extract only inspect leading text | 4 | Intelligence | OPEN | Define whole-document strategy and report coverage. | Tail-only evidence and long-document coverage tests. |
| TRN-01 | Translation memory identity ignores policy | 1 | Translation + Security | OPEN | Include authorized KB and effective policy identity. | Cross-policy and cross-KB non-reuse tests. |
| TRN-02 | Translation index-output default is ignored | 4 | Translation | OPEN | Apply platform default with explicit-request precedence. | Defaults/override/API/form tests. |
| TRN-03 | Worker user switching is not restored | 4 | Translation + Security | OPEN | Use scoped user restoration and revalidate requester authority. | Success/failure restoration and revoked-access tests. |
| TRN-04 | Translation lacks progress/cancel/refresh | 4 | Translation + Jobs + Frontend | OPEN | Implement durable progress, cancellation, retry, realtime/polling. | Worker/browser cancel/reconnect/failure tests. |
| TRN-05 | Back-translation accounting/issues are incomplete | 4 | Translation | OPEN | Persist cost/usage/issues consistently. | Sampling, accounting, issue, timeout, and partial-result tests. |
| TRN-06 | Output is not original-format reconstruction | 0 | Translation | CLOSED — SCOPED | Control/docs now promise extracted-text structure only. | Metadata label/help and documentation contract tests. |
| TRN-07 | Glossary permissions lack KB parity | 4 | Translation + Security | OPEN | Enforce glossary/KB read and write scope parity. | Cross-KB/cross-user list/get/use tests. |
| PAT-01 | Zero-result pattern scan state is not durable | 4 | Patterns + Jobs | OPEN | Persist completed empty scans/checksum. | Scheduler no-rescan and content-change rescan tests. |
| PAT-02 | Tail offsets are not original-document offsets | 4 | Patterns | OPEN | Translate sampled offsets to source offsets. | Head/tail boundary and exact source-position tests. |
| PAT-03 | Regex shape is insufficient for high precision | 4 | Patterns | OPEN | Add semantic/checksum/domain validation and confidence. | False-positive/negative fixture suite by entity type. |
| PAT-04 | Pattern UI is document-local only | 4 | Patterns + Frontend | OPEN | Build permission-safe Pattern Explorer. | Filter/pagination/permission/browser tests. |
| AUTO-01 | Deleted-document automation lacks immutable snapshot | 5 | Automation | OPEN | Capture pre-delete event snapshot and authorize execution. | Delete-event condition/action/audit tests. |
| AUTO-02 | Automation source fields are not validated | 5 | Automation | OPEN | Validate source DocType/event/action field contracts. | Invalid field/type/permission tests and form feedback. |
| AUTO-03 | Automation counters race | 5 | Automation | OPEN | Use atomic counter updates. | Concurrent execution count tests. |
| AUTO-04 | Event dedupe drops meaningful updates | 5 | Automation | OPEN | Use revision/event identity and idempotency contract. | Duplicate delivery versus distinct update tests. |
| PIPE-01 | API and Document Ingest triggers are unwired | 5 | Pipelines | OPEN | Add authorized API trigger and canonical ingest event hook. | Trigger permission/idempotency/integration tests. |
| PIPE-02 | Scheduled runs are not atomically claimed | 5 | Pipelines + Jobs | OPEN | Atomic due-run claim with misfire policy. | Multi-scheduler duplicate/misfire tests. |
| PIPE-03 | Approval stops instead of resumably pausing | 5 | Pipelines + Approvals | OPEN | Persist Waiting Approval and resume exactly once. | Approve/reject/duplicate/concurrent/restart tests. |
| PIPE-04 | Pipeline configuration UI is raw | 5 | Pipelines + Frontend | OPEN | Typed Frappe-native builder and test runner. | Schema validation and browser builder/run tests. |
| TASK-01 | AI Task types have incomplete execution paths | 5 | Tasks | OPEN | Define and implement/remove each type. | Per-type success/failure/provider/permission tests. |
| TASK-02 | Task status and approval are not governed | 5 | Tasks + Approvals + Security | OPEN | Server-authorized transition state machine. | Role matrix, illegal transition, duplicate approval, worker tests. |
| TASK-03 | Task frontend mismatches schema | 5 | Tasks + Frontend | OPEN | Render canonical states/actions/fields only. | API-to-UI parity and browser workflow tests. |
| GOV-01 | Concurrent request limits are not enforced | 6 | Governance | OPEN | Distributed user/provider/model leases. | Saturation, expiry, worker-death, fairness, bypass tests. |
| GOV-02 | Provider rate limits are not enforced | 6 | Governance + Provider | OPEN | Distributed bounded rate limiter. | Burst/window/concurrency/failover tests. |
| GOV-03 | Quotas are check-then-use, not reserved | 6 | Governance | OPEN | Atomic reservation/reconciliation. | Concurrent overrun, rollback, actual-usage adjustment tests. |
| GOV-04 | Explicit model resolution ignores type | 6 | Engine + Governance | OPEN | Validate requested operation against model type/capability. | Wrong-type and capability-denied tests for every operation. |
| PROV-01 | Failover does not choose an equivalent target model | 6 | Provider + Engine | OPEN | Map capability/type-equivalent models and log actual identity. | Cross-provider failover compatibility and audit tests. |
| PROV-02 | Model capability fields are cosmetic | 6 | Provider + Engine | OPEN | Enforce effective adapter/model capabilities. | Streaming/tools/JSON/embedding/vision capability matrix. |
| PROV-03 | Provider/model operational fields are disconnected | 6 | Provider Operations | OPEN | Implement lifecycle/progress/delete/unload/version or remove each remaining field. Version UI is hidden in Phase 0; other behavior remains open. | Provider matrix, lifecycle jobs, progress, permission, migration tests. |
| FILE-01 | Canonical move can fall back to native mutation | 1 | File/Folder + Security | OPEN | Remove fallback; canonical service remains sole mutation owner. | Forced canonical failure proves no native mutation/provenance bypass. |
| FILE-02 | URL-only upload resolution is ambiguous | 1 | File/Folder + Security | OPEN | Require stable File identity. | Duplicate URL/name, cross-user, and upload resolution tests. |
| FILE-03 | “My Uploads” is shared | 1 | File/Folder + Frontend | OPEN | Implement per-user semantics or rename accurately. | User isolation and browser tab tests. |
| FILE-04 | Storage-folder setting shape/default is wrong | 1 | File/Folder | OPEN | Use native File Link/default semantics. | Fresh/upgrade/default-folder tests. |
| FILE-05 | Folder picker is eager, bounded, and shallow | 1 | File/Folder + Frontend | OPEN | Lazy permission-aware tree/search/pagination. | Deep-tree/load/permission/browser tests. |
| FILE-06 | “Shared with me” actually means public | 1 | File/Folder + Frontend | OPEN | Use Frappe sharing or rename Public. | Share/public/user-isolation browser tests. |
| FILE-07 | Global Desk monkey patches are fragile | 1 | Frontend + File/Folder | OPEN | Replace with supported hooks where possible; version-gate remainder. | Exact-v17 browser smoke and missing-bundle diagnostic tests. |
| OPS-01 | GitHub Actions and branch protection are not a gate | 0 | Release Engineering + Repository Owner | BLOCKED | Workflow definitions corrected; hosted execution and branch protection require account/plan/admin remediation. | Green Server/Linter/Frontend static/Dependency audit runs and GitHub API showing required protection. |
| OPS-02 | Health interval scheduling is approximate | 6 | Operations + Jobs | OPEN | Timestamp threshold plus atomic claim. | Irregular interval/multi-scheduler/time-boundary tests. |
| OPS-03 | Operations UI lifecycle/drill-down is incomplete | 6 | Operations + Frontend | OPEN | Timer cleanup, charts, filters, queue/job/SLO drill-down. | API metrics and browser lifecycle/filter/export tests. |
| OPS-04 | Export/import is not a restore path | 6 | Backup/Restore | OPEN | Versioned streaming manifest/checksums/selective restore/compatibility/retention. | Full component round-trip, corruption, model mismatch, automated restore drill. |
| OPS-05 | Audit/execution trace links and stale reconciliation are incomplete | 6 | Operations + Logging | OPEN | Link identities, queue/heartbeat timing, active traceback capture, stale reconciliation. | Message/task/step links, worker-death, traceback, reconciliation tests. |
| OPS-06 | Backup and cleanup jobs are unbounded | 6 | Operations + Jobs | OPEN | Batches, continuation, savepoints, metrics, summaries. | Large-data bounded-query/memory/failure-resume tests. |
| LEARN-01 | Learning report filters are disconnected | 6 | Learning + Reports | OPEN | Use Script Reports/query builder with validated filters. | Real report execution/filter/MariaDB permission tests. |
| LEARN-02 | Learning APIs have no first-class frontend | 6 | Learning + Frontend | OPEN | Build permission-safe Learning Dashboard. | Review/approval/filter/browser role tests. |
| LEARN-03 | Stored memory embeddings are unused | 6 | Learning + Retrieval | OPEN | Hybrid recall grouped by embedding model with lexical fallback. | Paraphrase/mixed-model/fallback tests. |
| LEARN-04 | Skills are not relevance ranked | 6 | Learning | OPEN | Rank by request with priority/conflict/version rules. | Relevance, conflict, scope, prompt-budget tests. |
| LEARN-05 | Learning lifecycle maintenance is incomplete | 6 | Learning + Jobs | OPEN | Merge/supersede/archive/review/re-embed lifecycle. | Transition, provenance, scheduler, model-change, permission tests. |

## Control totals

| Phase | Registered findings |
| ---: | ---: |
| 0 | 8 |
| 1 | 14 |
| 2 | 6 |
| 3 | 8 |
| 4 | 15 |
| 5 | 11 |
| 6 | 17 |
| **Total** | **79** |

Phase 7 is the integrated qualification gate; it owns no new audit ID and verifies all 79 closures in the real runtime, browser, load, chaos, upgrade, and restore matrices.
