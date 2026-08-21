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
| SEC-01 | Translation memory can be unscoped | 1 | Translation + Security | CLOSED — IMPLEMENTED | Authorized KB scope enforced on document, inline, and tool paths; policy identity in the memory fingerprint. | Multi-user, multi-KB document/inline/tool isolation tests; no-scope test returns no lookup. |
| SEC-02 | Generic count bypasses row-level permissions | 1 | Tools + Security | CLOSED — IMPLEMENTED | Central safe query/count in `ai.tools.query`; count runs through permission-aware listing. | Row-permission parity tests across the role matrix. |
| SEC-03 | Generic document tools expose fields unsafely | 1 | Tools + Security | CLOSED — IMPLEMENTED | Central permlevel-aware projection plus sensitive-field deny (Password types, names, operator-configured list). | Field-level and sensitive-field exfiltration tests across list/get. |
| SEC-04 | Local-only networking lacks connection-level enforcement | 1 | Provider + Security | CLOSED — IMPLEMENTED | Guarded session: trust_env=False, single validated resolution, pinned dial, redirect refusal, peer revalidation. | 17 runtime tests against a real loopback HTTP/TLS server. |
| SEC-05 | Stored-document encryption control is not implemented | 0 | Security | CLOSED — REMOVED | Compatibility field hidden/read-only, reset to 0, and rejected server-side; deployment encryption documented. | Metadata/controller/patch tests and product-claim regression. |
| SEC-06 | Folder-settings list visibility is too broad | 1 | File/Folder + Security | CLOSED — IMPLEMENTED | List condition composes the registered File permission query hooks in a subquery on the settings row's folder. | Cross-role list/get permission parity tests. |
| SEC-07 | Search telemetry bypasses logging redaction | 1 | Logging + Security | CLOSED — IMPLEMENTED | Query and bounded result snippets pass canonical redaction; full content never stored; `log_search_queries` control; native 30-day retention. | Redaction, bounded-snippet, disable-control, and retention-contract tests. |
| RET-01 | Semantic ranking truncates the corpus before ranking | 2 | Retrieval | CLOSED — IMPLEMENTED | Complete keyset-paged brute-force scan; ceiling flags latency only. | Only-relevant-result-beyond-200 correctness test. |
| RET-02 | Keyword ranking truncates candidates | 2 | Retrieval | CLOSED — IMPLEMENTED | FULLTEXT when eligible; otherwise complete LIKE scan. No 500-row cap. | Only-relevant-result-beyond-old-cap keyword test. |
| RET-03 | Mixed embedding models are compared together | 2 | Retrieval | CLOSED — IMPLEMENTED | Group by model/dimensions; skip stale/incompatible chunks. | Mixed-model/dimension retrieval and diagnostics tests. |
| RET-04 | KB top-k, threshold, and weights are ignored | 2 | Retrieval | CLOSED — IMPLEMENTED | Request override → KB policy → platform default; weights in fusion. | Independent and combined policy-effect tests plus diagnostics. |
| RET-05 | Reranker is declared without execution | 0 | Retrieval | CLOSED — REMOVED | Model choice/discovery removed; legacy rows preserved and disabled. | Metadata, controller, discovery, and migration tests. |
| RET-06 | Oversized first context block can yield no context | 2 | Retrieval | CLOSED — IMPLEMENTED | Truncate first block; dedupe overlap; preserve packed citation mapping. | Oversized-first-result and character-budget tests. |
| RET-07 | Folder subtree filtering uses unsafe prefix matching | 2 | Retrieval + File/Folder | CLOSED — IMPLEMENTED | Shared `folder_match_or_filters`: exact or `root/%`. | Sibling-prefix unit + integration tests. |
| CHAT-01 | History selects oldest rather than latest messages | 3 | Conversation | CLOSED — IMPLEMENTED | Latest N via `window_latest_messages`; tool groups kept intact; summary injected when truncated. | 100-message latest-context unit + integration tests. Browser E2E → Phase 7. |
| CHAT-02 | Concurrent message sequence allocation races | 3 | Conversation | CLOSED — IMPLEMENTED | Conversation row `FOR UPDATE` allocator; `turn_id` on messages; unique `(conversation, sequence)` index. | `TestConversationHistory.test_100_concurrent_sends_preserve_order_and_uniqueness`: 100 independent Frappe worker connections, 100 committed messages, sequences 1–100 unique; passed in hosted Server run `32394651654`. |
| CHAT-03 | Existing conversation state is not synchronized | 3 | Conversation + Frontend | CLOSED — IMPLEMENTED | Route-options/path/query/hash parser; open restores agent/model/KB/focused document; config persisted per turn. | Route-state JS tests + backend config tests. Desk deep-link smoke → Phase 7. |
| CHAT-04 | Focus document, fallback, weights, citations are disconnected | 3 | Conversation + Retrieval | CLOSED — IMPLEMENTED | Focused document merged into authorized retrieve scope; strict-grounding fallback; footnote instructions; weights already in RET-04. | Per-control integration tests. |
| CHAT-05 | End-user conversation actions are missing | 3 | Conversation + Frontend | CLOSED — IMPLEMENTED | Service-layer rename/pin/archive/restore/export/pagination with v17 `limit`/`offset`; Assistant menus + archived filter. | Permission and pagination tests. Browser workflow → Phase 7. |
| CHAT-06 | Negative feedback lacks reason/correction | 3 | Learning + Frontend | CLOSED — IMPLEMENTED | Dialog persists reason/correction; learning-disabled still records the rating. | Persistence/reason tests. Browser dialog → Phase 7. |
| CHAT-07 | Turn cancellation/reconnect is absent | 3 | Conversation + Jobs | CLOSED — IMPLEMENTED | Same `turn_id` as streaming; cache cancel flag; Streaming placeholder; engine stream abort; `get_turn_status`; Stop button. | Cancel-by-id, isolation, stream-abort, reconnect tests. Browser Stop → Phase 7. |
| CHAT-08 | Attachment identity is inconsistent | 3 | Conversation + File/Folder | CLOSED — IMPLEMENTED | Uploader passes `file.name` as `file_record`; pending chips; restore pending on send failure. | Source contract + FILE-02 resolver tests. Browser upload → Phase 7. |
| ING-01 | Folder source is non-functional | 0 | Ingestion + File/Folder | CLOSED — REMOVED | Select option removed; server rejects; native File remains sole folder authority. | Metadata/controller tests and legacy-row preservation statement. |
| ING-02 | Scanned-PDF OCR is advertised but absent | 0 | Ingestion | CLOSED — SCOPED | PDF text extraction and image OCR retained; scanned-PDF OCR promise/guidance removed. | Reader-warning and documentation contract tests. |
| ING-03 | `.msg` is not real Outlook parsing | 0 | Readers | CLOSED — REMOVED | `.msg` registry/UI claim removed; `.eml` remains supported. | Reader registry and supported-format API tests. |
| ING-04 | OpenDocument archive bomb validation is missing | 4 | Readers + Security | CLOSED — IMPLEMENTED | ZIP-bomb guard (member 500, 50 MB, ratio 100, traversal) on DOCX/XLSX/PPTX/ODT/ODS before parsing. | Hostile archive fixture tests. |
| ING-05 | Reader warnings are not durable | 4 | Ingestion + Frontend | CLOSED — IMPLEMENTED | StructuredWarning contract + coerce_warnings; AI Document.extraction_warnings (Code JSON) persisted by canonical reader manager via ingestion; API get_document_warnings; UI empty/partial/failure/loading/permission/retry/realtime+p polling. | Unit coercion + bench reload + partial/malformed/archive/nested/permission/retry/background tests. |
| ING-06 | Processing lacks progress/cancel/recovery UX | 4 | Ingestion + Jobs + Frontend | IN PROGRESS | Durable progress, heartbeat, cancellation, native realtime updates, and explicit requeue recovery exist; worker-failure/browser reconnect evidence remains Phase 7. | Worker failure/cancel/reconnect/browser tests. |
| INT-01 | Extraction target DocType is unused | 0 | Intelligence | CLOSED — REMOVED | Dormant field hidden/read-only; extraction described as JSON-only. | Metadata and documentation contract tests; existing values preserved. |
| INT-02 | Structured output is not fully validated locally | 4 | Intelligence | CLOSED — IMPLEMENTED | Canonical validator ai/validation.py before persistence; Valid/malformed/missing/type/extra/nested/invalid-not-persisted/retry/worker/API/provider distinguish/provenance/permission/bounded tests. |
| INT-03 | Long summarization reduction is lossy | 4 | Intelligence | CLOSED — IMPLEMENTED | Hierarchical coverage-preserving reduction (pack+recurse, Section provenance, explicit HierarchicalReductionError >10, no silent truncation, budget invariant, 11-test suite + bench verify_int03 tail/provenance/persistence). | Bench: 11/11 + 4/4 tail/overwrite/API PASS, no overwrite on failure. |
| INT-04 | Compare/classify/extract only inspect leading text | 4 | Intelligence | CLOSED — IMPLEMENTED | Define whole-document strategy and report coverage. | Tail-only evidence and long-document coverage tests. |
| TRN-01 | Translation memory identity ignores policy | 1 | Translation + Security | CLOSED — IMPLEMENTED | KB plus glossary/tone/domain policy identity in every memory fingerprint. | Cross-policy and cross-KB non-reuse tests. |
| TRN-02 | Translation index-output default is ignored | 4 | Translation | CLOSED — IMPLEMENTED | Platform translation_index_output applied when index_output is None; explicit True/False wins. | Default/override/form/API tests. |
| TRN-03 | Worker user switching is not restored | 4 | Translation + Security | CLOSED — IMPLEMENTED | Canonical `utils.authority.as_user`; disabled requester rejected. | Restoration-after-failure and disabled-requester tests. |
| TRN-04 | Translation lacks progress/cancel/refresh | 4 | Translation + Jobs + Frontend | IN PROGRESS | Durable cancel/progress fields, `cancel_translation`, realtime `ai_translation_progress`; browser Stop remains Phase 7. | Cancel persistence test; browser → Phase 7. |
| TRN-05 | Back-translation accounting/issues are incomplete | 4 | Translation | IN PROGRESS | Back-translation tokens persist in verification and add to total_tokens. Timeout/partial matrix remains. | Sampling accounting in outcome; remaining timeout tests. |
| TRN-06 | Output is not original-format reconstruction | 0 | Translation | CLOSED — SCOPED | Control/docs now promise extracted-text structure only. | Metadata label/help and documentation contract tests. |
| TRN-07 | Glossary permissions lack KB parity | 4 | Translation + Security | CLOSED — IMPLEMENTED | `glossary_query` + `has_document_permission`; `load_glossary`/`get_glossaries` require KB access. | Unauthorized glossary use raises PermissionError. |
| PAT-01 | Zero-result pattern scan state is not durable | 4 | Patterns + Jobs | CLOSED — IMPLEMENTED | `AI Document.pattern_scan_checksum` persisted on every scan including empty; scheduler skips matching checksum. | Zero-result durable no-rescan integration test. |
| PAT-02 | Tail offsets are not original-document offsets | 4 | Patterns | CLOSED — IMPLEMENTED | Head/tail scan window maps offsets back to original document positions. | Tail identifier `first_offset` maps onto original text. |
| PAT-03 | Regex shape is insufficient for high precision | 4 | Patterns | CLOSED — IMPLEMENTED | Semantic validators reject invalid IPv4 octets and impossible calendar dates; money must parse as a bounded amount. | Invalid IP/date unit test. |
| PAT-04 | Pattern UI is document-local only | 4 | Patterns + Frontend | CLOSED — IMPLEMENTED | `explore_pattern_entities` + Desk page `pattern-explorer` with filters/pagination; get_list permission. Browser workflow → Phase 7. | Pagination/filter integration test. |
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
| FILE-01 | Canonical move can fall back to native mutation | 1 | File/Folder + Security | CLOSED — IMPLEMENTED | Desk paste override fails closed with the canonical service error; native fallback removed. | Code path has no native fallback; Desk smoke deferred to Phase 7. |
| FILE-02 | URL-only upload resolution is ambiguous | 1 | File/Folder + Security | CLOSED — IMPLEMENTED | One canonical resolver (folders.resolve_file_identity): stable record first, URL-only fails closed on ambiguity; upload facade accepts `file_name`. | Stable-identity, duplicate-URL, and missing-identity tests. |
| FILE-03 | “My Uploads” is shared | 1 | File/Folder + Frontend | CLOSED — SCOPED | Renamed to “Shared Uploads” in defaults, install seeding, and migration (legacy folder renamed, data preserved). | Default-folder and migration coverage; browser verification in Phase 7. |
| FILE-04 | Storage-folder setting shape/default is wrong | 1 | File/Folder | CLOSED — IMPLEMENTED | `storage_folder` is now a File Link (folder-filtered), validated server-side, applied only with write access. | Validation, non-folder, and unwritable-folder default tests. |
| FILE-05 | Folder picker is eager, bounded, and shallow | 1 | File/Folder + Frontend | CLOSED — IMPLEMENTED | Uploader uses the native lazy Link picker (folder-filtered, server-side search/pagination); eager depth-6 tree removed. | Browser deep-tree verification in Phase 7. |
| FILE-06 | “Shared with me” actually means public | 1 | File/Folder + Frontend | CLOSED — SCOPED | Tab renamed “Public” with the is_private=0 query unchanged; label contract asserted in tests. | Browser verification in Phase 7. |
| FILE-07 | Global Desk monkey patches are fragile | 1 | Frontend + File/Folder | IN PROGRESS | All patches version-gated to Frappe v17; core-bundle failures surface a rebuild diagnostic instead of silent suppression. | Exact-v17 browser smoke remains for Phase 7. |
| OPS-01 | GitHub Actions and branch protection are not a gate | 0 | Repository Owner / Release Engineering | BLOCKED — OWNER ACTION | Hosted workflows execute and are green, but `GET /repos/Oscar-Hyde/ai_fr_hg/branches/main/protection` returns HTTP 403 (`Resource not accessible by integration`); repository metadata shows the installed GitHub App has no administration permission. Owner must grant the installation repository `administration:write` permission and require Server, Linter, Frontend static, and Dependency audit on `main`. Until then, manual compensating control requires all four checks green in the PR UI before merge. | Owner action plus GitHub API showing `main.protected: true` and required contexts. |
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
