# AI Fr HG — comprehensive development audit and completion plan

**Audit date:** 2026-08-19
**Audited revision:** `main` / `0d8848eb5178afeee2dd64b15c30c54dea6b899d`
**Planning branch:** `arena/01a01a6c-ai-fr-hg`
**Audience:** product owner, technical lead, Frappe developers, reviewers, operators, and security reviewers

> This file is the audit baseline and required-state backlog. Current ownership,
> phase assignment, and closure evidence are controlled in
> [`GAP_REGISTER.md`](GAP_REGISTER.md); accepted support-boundary choices are in
> [`ARCHITECTURE_DECISIONS.md`](ARCHITECTURE_DECISIONS.md). Historical observations
> below remain unchanged so a finding is never erased after closure.

---

## 1. Executive assessment

AI Fr HG has unusually broad implementation coverage for an early Frappe application. It already contains a coherent service layer, 47 DocTypes, 4 custom Desk pages, 5 workspaces, 117 whitelisted methods, local provider adapters, document ingestion, retrieval, agents, tools, pipelines, learning, translation, folder organization, monitoring, and 392 automated Python tests. The latest real-bench run passed all 392 tests, with one intentional skip.

That breadth must not be confused with production completeness. The current repository describes most capabilities as **READY** or **IMPLEMENTED**, but a code-level audit shows four different states hidden behind those labels:

1. **Complete core paths** — tested paths that are internally coherent, such as document-tree locking, translation segmentation, embedding response validation, candidate promotion, and nested-pipeline cycle guards.
2. **Main-path implementations** — features that work for small data sets and expected inputs but are incomplete at scale, under concurrency, or in failure recovery. Retrieval and operations reporting are the clearest examples.
3. **Partially connected features** — fields, settings, and backend functions exist but are not connected to an end-user workflow. Examples include knowledge-base weights, conversation-focused documents, extraction target DocTypes, model versions, and the learning overview API.
4. **Declared but non-functional behavior** — options appear in the data model or UI but are not enforced or implemented. Examples include document-text encryption, resource/provider/model concurrency limits, provider rate limits, AI Task `Compare` behavior, AI Task due dates, and `AI Document` source type `Folder`.

### Revised product status

The appropriate current label is:

> **Feature-rich technical beta with a strong tested core, but not yet production-complete.**

The immediate objective should not be to add more top-level features. It should be to make existing claims true, remove or hide misleading controls, close security and data-isolation gaps, make retrieval correct beyond tiny corpora, and add browser/worker/upgrade test coverage.

### Highest-priority conclusions

The following items should be completed before calling the platform production-ready:

- Scope translation memory on every path; inline translation currently has no knowledge-base scope and can query memory across all translations.
- Replace bounded, unordered semantic and keyword candidate selection with a correct retrieval strategy, including per-knowledge-base embedding models.
- Enforce all declared governance limits, especially concurrent requests, provider/model concurrency, and provider rate limits.
- Complete or remove the non-functional AI Task, `Folder` source, encryption, reranker, model-version, and extraction-target controls.
- Eliminate unsafe or misleading global File/Desk fallbacks that can bypass the canonical folder service or hide missing core bundles.
- Bound and validate all public API inputs and paginate unbounded conversation/chunk/list responses.
- Restore GitHub Actions as a real merge gate. The current jobs fail before any steps because the GitHub account has a billing/spending-limit problem.
- Add frontend unit, browser end-to-end, optional-dependency, real-runtime smoke, concurrency, load, and migration tests.

---

## 2. Audit scope and evidence

This plan is based on a repository-wide review of:

- `ai_fr_hg/ai/`: engine, agents, ingestion, retrieval, intelligence, translation, patterns, folders, document tree, learning, governance, tools, pipelines, automation, monitoring, providers, and readers.
- `ai_fr_hg/api/`: all public chat, knowledge, translation, learning, administration, folder, and document-tree facades.
- All 47 DocType schemas and their Python/JavaScript controllers.
- All custom pages, workspaces, reports, global Desk assets, and File uploader/list extensions.
- Installation, migrations, patches, scheduled jobs, uninstall behavior, hooks, permissions, and fixtures.
- README and all files under `docs/`.
- GitHub issues, pull requests, checks, and workflow configuration.
- Static searches for stubs, unused settings, unreferenced fields, broad exception handling, deprecated Frappe query arguments, raw SQL, and frontend RPC wiring.

### Current measurable baseline

| Measure | Current value |
| --- | ---: |
| Source/schema lines (`.py`, `.js`, `.scss`, `.json`) | ~49,900 |
| DocTypes | 47 |
| Custom Desk pages | 4 |
| Workspaces | 5 |
| Reports | 3 |
| Whitelisted methods | 117 |
| Python test files | 23 |
| Python test classes | 74 |
| Python test methods | 392 |
| Production JavaScript files | 52 |
| JavaScript unit/E2E test files | 0 |
| Production `limit_page_length` uses | 86 |
| Production `limit_start` uses | 9 |
| Production raw SQL call sites | 35 |
| Production broad `except Exception` catches | 115 |

### Verification completed during this audit

- `python -m compileall -q ai_fr_hg` — passed.
- All repository JSON files parsed — passed.
- `node --check` over all JavaScript files — passed.
- `git diff --check` — passed before documentation changes.
- The supplied real-bench run passed 194 unit, 179 integration, and 19 other tests: 392 total, one skipped.
- GitHub Actions runs were inspected. The jobs did not execute any steps; their annotation states that recent account payments failed or the Actions spending limit must be increased.

### What this audit did not prove

- It did not run a browser against a live Frappe Desk in this sandbox.
- It did not run real Ollama/vLLM models or OCR/Office optional dependencies here.
- It did not load-test a large knowledge base.
- It did not perform an independent penetration test.
- It did not prove PostgreSQL compatibility; the current SQL is predominantly MariaDB-specific.

Findings below are therefore classified as code-confirmed gaps, architectural risks, or validation requirements. Security-sensitive findings require dedicated regression tests before closure.

---

## 3. Status legend used by this revision

| Status | Meaning |
| --- | --- |
| **READY** | Main path, permission path, failure path, UI path, and automated coverage are present. |
| **PARTIAL** | Main path exists, but a material function, scale path, UX path, or failure path remains incomplete. |
| **DISCONNECTED** | Schema/API/backend building block exists but is not connected to the intended workflow. |
| **DECLARED ONLY** | A field/setting/option exists but has no effective implementation. |
| **HARDENING REQUIRED** | Works, but must be made safe/reliable before production use. |
| **REMOVE OR IMPLEMENT** | Keeping the control visible is misleading unless implementation is completed. |

---

## 4. Revised capability matrix

| Capability | Revised status | Main reason |
| --- | --- | --- |
| Local provider adapters | **PARTIAL / HARDENING REQUIRED** | Ollama and OpenAI-compatible paths exist; declared rate/concurrency controls and model capabilities are not enforced. Failover does not map equivalent models across providers. |
| Strict local-only networking | **PARTIAL / HARDENING REQUIRED** | URL checks exist, but provider requests use environment-aware `requests.request`, and DNS is validated separately from connection establishment. |
| Model discovery/management | **PARTIAL** | Discovery, pull, test, and defaults work; pull progress, delete/unload, capability synchronization, version history, and stale-model lifecycle are incomplete. |
| Document ingestion | **PARTIAL** | Main file/text/URL/DocType paths are strong; `Folder` source is non-functional, scanned-PDF OCR is not implemented, `.msg` is over-advertised, and progress/cancellation are absent. |
| Document tree | **READY / HARDENING REQUIRED** | Strong locking, identity, and tests; UI and deep-tree scale still need browser/load coverage. |
| Native File folder integration | **PARTIAL / HARDENING REQUIRED** | Rich service exists; global prototype patches, native fallback on canonical move failure, ambiguous URL fallback, deep-tree selection, and shared “My Uploads” behavior need revision. |
| Hybrid retrieval | **PARTIAL — correctness blocker** | Semantic ranking considers only an unordered bounded subset; keyword ranking is capped similarly; mixed embedding models and KB-specific settings are not handled. |
| Knowledge Explorer | **PARTIAL** | Core search/ask works; no pagination, folder selector, diagnostics, saved queries, entity facets, or large-result behavior. |
| Document intelligence | **PARTIAL** | Summarize/classify/extract/compare work; extraction target mapping is unused, long-document reduction is lossy at the final stage, and output schema verification is limited. |
| Pattern extraction | **PARTIAL** | Deterministic core and tests are strong; durable zero-result scan state, aggregate explorer UI, original offsets for tail samples, and value validation remain. |
| Translation | **PARTIAL — isolation blocker** | Strong segmentation/quality/review core; memory scope, memory policy identity, progress/cancel, default-index setting, user restoration, and format-preserving output remain. |
| Chat/agent runtime | **PARTIAL** | Core turn/tool/citation path works; history window is oldest-first, conversation route state and focused document are not honored, cancellation/pinning/negative-feedback UX are missing. |
| Tools and approval | **PARTIAL / HARDENING REQUIRED** | Validation/audit/approval are good; generic count/field visibility, defaults, pending expiry, async approved execution, and pipeline resume need completion. |
| Pipelines | **PARTIAL** | Execution state machine and nested-cycle protection are strong; visual configuration, API/document-ingest triggers, scheduled-run claiming, resumable approval, and per-step typed validation are incomplete. |
| Automation rules | **PARTIAL** | Event dispatch exists; `on_trash` cannot reload the deleted document in the queued job, source-field validation and atomic counters are missing, and runtime tests are sparse. |
| AI Tasks | **DECLARED ONLY / PARTIAL** | Question/summarize/classify/extract/pipeline paths partly work; Compare and Custom do not, due date/priority/execution log are unused, and approval/status UI is mismatched. |
| Governance/quotas | **PARTIAL — enforcement blocker** | Request/token/document and capabilities exist; concurrent request limits, provider/model limits, rate limits, and race-safe reservations do not. |
| Learning loop | **PARTIAL** | Governed promotion and feedback core are strong; embeddings are not used in recall, skills are injected without relevance ranking, reports are misconfigured, and no learning dashboard consumes the APIs. |
| Operations dashboard | **PARTIAL** | Useful snapshot exists; usage page/API is not exposed, active/stuck job detail is limited, timer cleanup is missing, and alerts/SLOs are incomplete. |
| Backup/export/import | **PARTIAL — restore blocker** | Basic JSON export/import works for text; exported embeddings are ignored on import, large exports load into memory, and retention/version/integrity/restore tests are absent. |
| Encryption | **DECLARED ONLY** | `encrypt_documents` is visible but stored document/chunk/translation text remains plaintext. |
| Audit/traceability | **PARTIAL** | Major calls/actions are logged; message/task execution links and queue time are unused, failed tracebacks can be empty, and long-running “Running” records lack reconciliation. |
| Frontend quality | **PARTIAL** | Functional custom pages exist; no JS/browser tests, route-state gaps, no cancellation/progress UX, limited mobile/accessibility validation, and heavy global monkey-patching. |
| CI/release engineering | **BLOCKED** | Local tests pass, but GitHub Actions currently does not start; version remains `0.0.1`; no release/upgrade matrix. |

---

## 5. Confirmed high-priority findings

## 5.1 Data isolation and security

### SEC-01 — Translation memory is not scoped when no knowledge base is supplied

**Evidence:** `ai.translation._memory_lookup()` adds a parent/knowledge-base filter only when `knowledge_base` is truthy. Inline translation and the `translate_content` tool call `translate_text()` without a knowledge base for raw text, and the document tool also fails to pass the resolved document’s knowledge base.

**Impact:** An identical segment can be reused from another corpus. Even exact-segment reuse is a cross-scope data disclosure and violates the documented promise that translation memory is knowledge-base scoped.

**Required revision:**

- Make “no scope” mean “no translation memory,” never “all memory.”
- Pass the authorized knowledge base for document-backed inline translation.
- Add an explicit global memory scope only if administrators deliberately create one.
- Include glossary/version, tone/register, domain, normalization version, and review state in memory eligibility or rescore cached output against the current policy.
- Add multi-user, multi-KB regression tests and a migration for any future memory index.

### SEC-02 — Generic tool count bypasses row-level query permissions

**Evidence:** `ai.tools.builtin.count_documents()` checks DocType-level read permission, then uses `frappe.db.count()`. That bypasses permission query conditions for shared AI records and potentially for other DocTypes.

**Impact:** The model can reveal aggregate counts for records the requester cannot list.

**Required revision:** Use a permission-aware aggregate (`frappe.get_list` with dict aggregate syntax), apply field-level and row-level permission tests, and centralize safe generic query behavior so the built-in and configurable DocType Query tools cannot drift.

### SEC-03 — Generic document tools need field-level output control

**Evidence:** `get_document()` can return `doc.as_dict()` and accepts arbitrary requested fields. DocType read permission is checked, but sensitive field-level/permlevel behavior is not explicitly enforced by this service.

**Impact:** A model-facing tool can expose fields that are not intended for the user or for AI processing.

**Required revision:**

- Compute readable fields from metadata and the current user’s permission levels.
- Deny Password, Attach private metadata, authentication, token, secret, and configured sensitive fields by default.
- Add per-tool field allowlists.
- Redact tool arguments/results before audit/display where configured.
- Add fixtures with permlevel-restricted fields.

### SEC-04 — Local-only networking needs connection-level enforcement

**Evidence:** Hostname resolution is checked before a separate `requests` call. Provider requests use global `requests.request`, which can honor environment proxy variables. `.local` and `.internal` names are accepted by suffix before validating the resolved addresses.

**Impact:** DNS rebinding or an unexpected proxy configuration can weaken the “no prompt leaves the network” guarantee.

**Required revision:**

- Use a dedicated session with `trust_env=False` for provider calls, as URL ingestion already does.
- Resolve and connect to validated addresses without a second uncontrolled DNS decision, while preserving the original Host/SNI safely.
- Validate every redirect (provider calls should normally reject redirects).
- Require all resolved addresses to be private unless the host is explicitly allowed.
- Treat hostname suffixes as hints, not unconditional trust.
- Add IPv4/IPv6, DNS-rebinding, proxy-env, redirect, and allowlist tests.

### SEC-05 — “Encrypt Stored Document Text” is not implemented

**Evidence:** `AI Platform Settings.encrypt_documents` is never read by production code. `AI Document.content`, chunks, translations, messages, and memories remain plaintext Long Text fields.

**Impact:** The UI makes a security promise that the backend does not honor.

**Required revision:** Choose one of two explicit paths:

1. **Implement:** envelope encryption with a site-managed key, key rotation, searchable-derived-data policy, backup/restore handling, and clear limitations; or
2. **Remove/hide:** migrate the field out of the form and document that database/filesystem encryption at rest is an infrastructure responsibility.

Do not leave the current checkbox visible.

### SEC-06 — Folder settings list visibility is broader than its direct-document check

**Evidence:** `folder_settings_query()` returns an empty condition for non-managers and relies on `has_document_permission` to check the linked File. Frappe list queries do not generally run per-row `has_permission` hooks.

**Impact:** AI Users may list metadata for folder settings they cannot open directly.

**Required revision:** Restrict the DocType to managers until a permission-aware list condition is implemented, or derive visibility using a supported File permission query. Add list-vs-direct permission parity tests.

### SEC-07 — Search telemetry stores query and result content without the logging redaction path

**Evidence:** `_log_search_job()` stores the raw query and serialized top results, including chunk content. It does not call `redact()` and has no independent telemetry-content setting.

**Impact:** Sensitive queries/content can persist even when prompt/response logging expectations differ.

**Required revision:** Add telemetry policy controls, redact before persistence, store identifiers/scores and bounded snippets rather than full content, and document retention/access clearly.

---

## 5.2 Retrieval correctness and scale

### RET-01 — Semantic search ranks only a small unordered subset

**Evidence:** `semantic_search()` fetches `max(top_k * 20, 200)` embedded chunk rows with no deterministic or relevance-bearing order, then ranks only those rows.

**Impact:** Once a knowledge base exceeds the bounded subset, relevant chunks outside it are invisible to semantic search. Passing small integration tests does not validate corpus-level correctness.

**Required revision:**

- Immediate correctness path: page through every eligible vector for a bounded corpus and maintain a top-K heap or matrix batches.
- Define a configurable brute-force ceiling and surface a readiness warning when exceeded.
- Add a scale tier for larger corpora: MariaDB/PostgreSQL-supported vector indexing if available, or an optional local vector backend with permission-preserving IDs and rebuild semantics.
- Record candidate count, corpus count, embedding model, and retrieval strategy in diagnostics.
- Add a test where the only relevant vector is beyond row 200.

### RET-02 — Keyword search has the same bounded-candidate problem

**Evidence:** keyword candidates are limited to 500 rows after a broad `LIKE` query, without a database relevance order.

**Impact:** Results are incomplete and nondeterministic on large corpora.

**Required revision:** Add a database-supported full-text index/search strategy where available, with a portable fallback that pages all bounded matches. Define tokenization behavior for Arabic, Hebrew, identifiers, and punctuation.

### RET-03 — Multiple knowledge bases can use incompatible embedding models

**Evidence:** a retrieval call creates one query embedding, while knowledge bases can independently select embedding models and dimensions. All selected vectors are then compared to the one query vector.

**Impact:** Mixed-dimension vectors score zero or invalidly; a platform-default query model may not match a knowledge base’s stored model.

**Required revision:** Group target knowledge bases by effective embedding model, embed the query once per model, rank within each compatible group, normalize/fuse group ranks, and reject stale/mixed chunks explicitly. Add mixed-model tests.

### RET-04 — Knowledge-base retrieval controls are not honored

**Evidence:** `AI Knowledge Base.top_k` and `similarity_threshold` exist and are validated, but general retrieval primarily uses agent/platform/call values. Agent knowledge-base child `weight` is not used.

**Impact:** Administrators configure knobs that do not affect results as described.

**Required revision:** Define precedence and semantics:

1. explicit API/agent override;
2. knowledge-base policy per result group;
3. platform default.

Apply per-KB thresholds before fusion and KB weights during fusion. Display the effective policy in search diagnostics.

### RET-05 — Reranker is declared but not implemented

**Evidence:** model type `Reranker` and execution-log operation `Rerank` exist; discovery can classify rerankers, but no reranking engine path consumes them.

**Required revision:** Either implement `run_rerank()` and a second-stage retrieval option or remove/hide the model type and operation until scheduled.

### RET-06 — Context packing can return no context when the first block is too large

**Evidence:** `build_context()` breaks when the next complete block exceeds the budget; it does not truncate or pack part of the first block.

**Required revision:** Token-aware packing should always include at least a bounded excerpt, deduplicate overlapping chunks, preserve citation mapping, and reserve generation tokens based on the actual selected model.

### RET-07 — Folder subtree matching uses an unsafe prefix

**Evidence:** folder-scoped retrieval uses `LIKE "<normalized>%"`. `Home/A` can match `Home/AB`.

**Required revision:** Match `folder = root OR folder LIKE root + '/%'`, use one shared descendant helper, and add similarly-prefixed-folder tests.

---

## 5.3 Conversation and agent behavior

### CHAT-01 — History uses the oldest messages, not the latest

**Evidence:** `get_conversation_history()` orders ascending and applies `limit` directly.

**Impact:** After 20 eligible messages, later turns continue seeing the beginning of the conversation and lose recent context.

**Required revision:** Query the latest N in descending order, reverse in memory before sending, keep tool-call groups intact, and optionally use the stored conversation summary for older context.

### CHAT-02 — Concurrent message sequence allocation is race-prone

**Evidence:** `save_message()` computes `max(sequence) + 1` without locking the conversation or an atomic counter.

**Impact:** Concurrent sends can create duplicate sequence values and interleaved turns.

**Required revision:** Lock the conversation row, reserve sequence ranges per turn, add a turn identifier to messages, and serialize or explicitly support concurrent turns.

### CHAT-03 — Existing-conversation state is not synchronized in the Assistant

**Evidence:** “Open in Assistant” passes a route option, but the page does not consume it. Opening a conversation does not set agent/model/knowledge selectors from the stored conversation.

**Impact:** A user can continue a conversation using UI defaults that do not match the stored conversation, while tool execution requires the invocation agent to match the conversation agent.

**Required revision:** Implement route-state parsing, selector synchronization/locking, conversation-aware model policy, and deep-link tests.

### CHAT-04 — Focused document, fallback answer, KB weights, and footnote mode are disconnected

- `AI Conversation.context_document` is never used.
- `AI Agent.fallback_answer` is never used.
- `AI Agent Knowledge Base.weight` is never used.
- `citation_mode=Footnote` has no behavior distinct from Inline.

**Required revision:** Implement each field with tests or remove it. The focused document should feed the same authorized `documents` scope as attach/ask. Fallback should apply only under explicit strict-grounding/no-context conditions.

### CHAT-05 — Missing end-user conversation actions

Backend rename/archive exists and pinned state is listed, but the Assistant has no rename, pin/unpin, archived view, restore, pagination, or export action.

**Required revision:** Add compact conversation menus, optimistic UI updates, archived filter, cursor pagination, and keyboard actions.

### CHAT-06 — Negative feedback omits correction/reason UI

The API and learning service support a reason and corrected answer, but thumbs-down sends only `Negative`.

**Required revision:** Open a non-blocking dialog for reason/correction, show candidate outcome, and allow a rating without teaching when policy denies learning.

### CHAT-07 — No turn cancellation or robust reconnect state

Streaming can run indefinitely when Max Turn Duration is zero. There is no stop action, server cancellation token, or turn-status endpoint.

**Required revision:** Create a durable turn/run identifier, cooperative cancellation in engine/provider loops, a Stop button, disconnect recovery, and a final `Cancelled` message/log state.

### CHAT-08 — Attachment identity is inconsistent

The Assistant upload call omits `file_record`, while other upload surfaces pass the exact File identity. Pending documents are cleared before the send succeeds and are not restored on failure.

**Required revision:** Always pass `file.name`, preserve pending attachments until a successful response, show removable attachment chips, and support multiple files explicitly.

### CHAT-09 — Sequence allocation under a stale row snapshot is not transparent

*Added 2026-08-21 as an amendment to this baseline, from the CHAT-02 reopening. Pre-existing behaviour, not introduced by that fix.*

`allocate_sequence` locks and increments a counter on the `AI Conversation` row. Under MariaDB's default REPEATABLE READ isolation, a transaction that has already performed a consistent read of that row — which every agent turn does when it loads the conversation — cannot subsequently lock or update it once another transaction has committed a change. MariaDB raises `1020 Record has changed since last read; try restarting transaction`, which Frappe surfaces as `QueryDeadlockError`.

**Impact:** correctness is preserved — the caller receives a retryable error and never a duplicate sequence — but a concurrent send can fail a request that a retry would have completed.

**Required revision:** Make the allocation transparent to the caller, by either (a) moving the counter onto a dedicated allocator record that no request path ever reads, so the consistent-read conflict cannot arise, or (b) adding a bounded transaction-level retry around the turn. Measure the real contention rate first; this belongs with Phase 7 concurrency and chaos testing, where the retry budget can be set from observed behaviour rather than guessed.

---

## 5.4 Ingestion and document intelligence

### ING-01 — `AI Document` source type `Folder` is non-functional

The schema offers `Folder`, and the controller contains a small folder branch, but `validate_source_access()` rejects unsupported source types and no recursive folder ingestion exists.

**Decision:** Implement a folder ingestion manifest with bounded recursive discovery, permissions, deduplication, progress, and rescan behavior, or remove `Folder` from the Select options.

### ING-02 — Scanned-PDF OCR is advertised but not implemented

`PDFReader` warns that OCR should be enabled when no text layer exists, but OCR is only attempted by `ImageReader`. Empty PDF text then fails ingestion.

**Required revision:** Add optional PDF rasterization plus per-page OCR, mixed text/OCR merging, language configuration, page markers, worker limits, and clear dependency checks (`tesseract`, PDF renderer). Until then, revise claims to “image OCR only.”

### ING-03 — `.msg` support is not real Outlook MSG parsing

`.msg` maps to the standard email parser intended for RFC822/MIME messages. Binary Outlook MSG needs a dedicated optional reader.

**Decision:** Add `extract-msg` support and fixtures, or stop advertising `.msg`.

### ING-04 — OpenDocument zip-bomb validation is missing

Archive validation covers DOCX/XLSX/XLSM/PPTX but not ODT/ODS, which are also ZIP containers.

**Required revision:** Include all ZIP-based readers, validate path traversal/member count/uncompressed size/compression ratio, and add hostile archive fixtures.

### ING-05 — Reader warnings are returned but not durably presented

Warnings are returned from processing but not stored in a dedicated document field or prominently rendered later.

**Required revision:** Store structured extraction warnings, expose them on the document form/list, and distinguish partial success from failure.

### ING-06 — No document processing progress/cancel UX

Statuses are coarse and realtime publishes only terminal completion.

**Required revision:** Publish stage progress, current/total pages/chunks, worker heartbeat, estimated next action, and cancellation. Add a stuck-run reconciler.

### INT-01 — Extraction Schema `target_doctype` is unused

The schema states that extracted data can be mapped onto a target DocType, but no mapping or governed create/update flow exists.

**Required revision:** Add an explicit field-mapping child table, preview/diff, target permission checks, validation, and approval for writes. Never infer arbitrary target writes from field names alone.

### INT-02 — Structured output is not fully validated locally

The model response is parsed and loosely coerced, but required fields, enums, nested object/array schemas, and additional properties are not validated after generation.

**Required revision:** Validate against the generated JSON Schema locally, return field-level issues, optionally repair once, and persist raw/validated outputs separately.

### INT-03 — Long-document summarization reduction is lossy

Map summaries are concatenated and truncated to one context budget before a single reduce call. Tail summaries can be discarded.

**Required revision:** Implement recursive/hierarchical reduction until all partials fit, with maximum call budgets, cancellation, and provenance from summary statements to source chunks.

### INT-04 — Compare/classify/extract use only leading text for large documents

This is acceptable only if disclosed. For enterprise documents, key clauses near the end are silently ignored.

**Required revision:** Add configurable whole-document strategies: chunk-and-vote classification, map/merge extraction with conflict handling, and section-aware comparison. Return coverage metrics.

---

## 5.5 Translation completion

### TRN-01 — Memory identity ignores translation policy

An exact source/language fingerprint can be reused under a different glossary, tone, domain, or normalization policy and is assigned quality 100 without rescoring.

**Required revision:** Version the memory key and include policy identity, or locally rescore every hit with current glossary/quality rules and reject incompatible hits. Prefer only human-reviewed segments when configured.

### TRN-02 — Default index-output setting is not applied

`translation_index_output` is loaded but `create_translation()` converts `None` to false rather than applying the configured default.

**Required revision:** Apply the setting only when the caller does not explicitly choose a value. Add controller/API tests.

### TRN-03 — Background user switching is not restored

`run_translation()` can call `frappe.set_user(user)` without a `finally` restoration context.

**Required revision:** Use the same `_as_user` pattern as ingestion/pipeline and revalidate authority after switching.

### TRN-04 — No progress, cancellation, or automatic form refresh

The core accepts a progress callback, but stored translation does not use it. The form opens a queued record and requires manual refresh.

**Required revision:** Add translated/total segments, current stage, heartbeat, realtime progress, cancel/retry, and terminal notifications.

### TRN-05 — Back-translation accounting and issue integration are incomplete

Back-translation token usage is discarded, embedding usage is not reflected in translation totals, and verification issues are not represented by a stable issue code/score policy.

**Required revision:** Track all calls, add stable verification issue codes, persist sampled back-translations if logging policy allows, and expose costs separately.

### TRN-06 — Output preserves text structure, not original file format

The current “Download as Text” output is useful, but it does not create a translated DOCX/PDF/XLSX/PPTX.

**Roadmap choice:** Keep the claim explicitly text-structure-preserving, or implement format reconstruction as a separate advanced workstream with per-format acceptance tests.

### TRN-07 — Glossary permissions need knowledge-base parity

AI Users can read all glossary records by role. The optional KB link does not drive row-level permission hooks.

**Required revision:** Global glossaries must be explicit; KB-scoped glossaries should follow KB access, with manager-only mutation and permission tests.

---

## 5.6 Patterns and entities

### PAT-01 — Zero-result scan state is not durable

The scheduler decides that a document needs scanning when no current-checksum pattern row exists. A legitimate zero-result scan creates no row; only a Redis cache suppresses repeat work.

**Required revision:** Store durable scan checksum/status/count on the document or a dedicated scan record, including zero results and extractor version.

### PAT-02 — Tail-sample offsets are not original-document offsets

For large text, head and tail are concatenated into one sampled string; `first_offset` is relative to that sample.

**Required revision:** Preserve sample spans and translate match offsets back to original text offsets. Add a provenance anchor/version.

### PAT-03 — Regex shape is not enough for “high precision”

IP and date regexes accept invalid ranges/dates. Money/date locale ambiguity is resolved by a fixed heuristic.

**Required revision:** Add post-match semantic validators, preserve raw value separately, and make locale/date ambiguity explicit rather than silently canonicalizing invalid values.

### PAT-04 — Entity UI is document-local only

The platform has a list and document modal, but no cross-document facet, trend, relationship, or export workflow.

**Frontend completion:** Add a Pattern Explorer page with type/value/KB/folder/document filters, occurrence totals, provenance preview, and permission-aware export.

---

## 5.7 Automation, pipelines, and AI Tasks

### AUTO-01 — `on_trash` rules cannot run from the deleted document

The hook queues execution after commit, then `execute_rule()` reloads the record by name. After deletion, that record no longer exists.

**Required revision:** Capture a permission-checked immutable event snapshot before deletion and pass it to an action type that supports delete events. Disallow target-field writes for delete events. Add integration tests.

### AUTO-02 — Automation source fields are not validated

Target field validation exists; source field validation does not.

**Required revision:** Validate source existence/type at rule save and again at runtime. Define handling for child tables and sensitive fields.

### AUTO-03 — Rule counters are race-prone

Success/failure counters use read-modify-write rather than atomic increments.

**Required revision:** Use atomic SQL/query-builder updates and test concurrent executions.

### AUTO-04 — Event deduplication can drop meaningful updates

The job ID is rule + DocType + document name. Multiple updates while a job is queued/running collapse into one without an event revision/checksum.

**Required revision:** Persist an Automation Run/Event record with source modified timestamp, dedupe exact revisions, and make coalescing an explicit rule option.

### PIPE-01 — Trigger types `Document Ingest` and `API` are not wired

Only manual calls, rules/tools/tasks, and the scheduled job call `run_pipeline()`. The trigger type is mostly descriptive.

**Required revision:**

- Add a permission-checked public pipeline-run API with idempotency key.
- Wire document-ingest pipelines after successful extraction/indexing with explicit input context.
- Validate trigger-specific required fields.

### PIPE-02 — Scheduled runs are not atomically claimed

`last_run_on` updates after terminal execution. A run lasting longer than the scheduler interval can be started again.

**Required revision:** Persist `next_run_on`, atomically claim due schedules under a lock, create a schedule execution identity, and support misfire/coalescing policy and timezone.

### PIPE-03 — Approval stops rather than resumably pausing a pipeline

A write-tool approval can be executed later, but the originating pipeline run remains failed and does not resume after the approved step.

**Required revision:** Add `Waiting Approval` run/step states, checkpoint the context and step index, then resume exactly once after approval/rejection.

### PIPE-04 — Pipeline configuration UI is too raw

Users edit a child table and JSON config without typed step forms, connection validation, input/output previews, or graph visibility.

**Frontend completion:** Build a native Frappe pipeline builder page/form section with a linear/graph view, typed config controls, schema hints, test-run input, and step output inspection.

### TASK-01 — AI Task task types are incomplete

- `Compare` falls through to the generic agent path instead of comparing documents.
- `Custom` has no custom method contract.
- `Question` and any unknown type use the same fallback.
- `execution_log` is never populated.
- `due_date` is never scheduled.
- `priority` does not influence queue or ordering.

**Required revision:** Define and implement every type or remove it. Add explicit payload schemas and controller validation per task type.

### TASK-02 — Task approval and status model is not governed

AI Users have write access and can set status to Approved. `on_update` uses that transition to enqueue work. `run_now()` does not enforce the intended state machine.

**Impact:** The displayed approval states are not an authorization boundary.

**Required revision:** Server-authored transitions, manager-only approve/reject, requester cancel/retry, capability checks, row lock, audit entries, and immutable requester attribution.

### TASK-03 — AI Task frontend is mismatched to its schema

The JavaScript color map uses `Pending`, `Scheduled`, and `Running`, while the DocType uses `Open`, `Pending Approval`, `Approved`, and `In Progress`. There are no action buttons.

**Frontend completion:** Implement state-correct indicators, Submit/Approve/Reject/Run/Cancel/Retry actions, result rendering, provenance links, and realtime progress.

---

## 5.8 Governance, providers, and models

### GOV-01 — Concurrent request limits are declared but not enforced

`AI Resource Policy.max_concurrent_requests`, `AI Provider.max_concurrent_requests`, and `AI Model.max_concurrent_requests` are stored but never used.

**Required revision:** Add Redis-backed leases/semaphores with TTL/heartbeat and safe release. Enforce user, provider, and model limits before runtime calls; expose queue/rejection metrics.

### GOV-02 — Provider rate limits are declared but not enforced

`rate_limit_per_minute` is unused.

**Required revision:** Add a distributed token bucket/sliding window. Local providers may use it to protect memory/CPU even without vendor limits.

### GOV-03 — Quota checks are not reservation-based

Request/token checks count existing logs, so concurrent requests can all pass before their usage is recorded. Streaming approximates completion tokens and records no prompt tokens.

**Required revision:** Reserve request and maximum token budget atomically, reconcile actual use at completion, expire abandoned reservations, and distinguish chat, embedding, translation, tool, and background/system budgets.

### GOV-04 — Explicit model resolution does not validate model type

`resolve_model(model, model_type)` validates enabled state but not the explicit model’s type.

**Required revision:** Enforce compatible types for chat, embedding, vision, translation, and rerank operations. Add API override tests.

### PROV-01 — Failover does not select an equivalent model on the target provider

The engine switches provider adapters but keeps the original provider’s `model_name` and logs against the original AI Model record.

**Required revision:** Introduce explicit failover groups/aliases or resolve a compatible enabled model on each provider. Log the actual provider and actual AI Model used for each attempt.

### PROV-02 — Model capability fields are cosmetic

`supports_tools`, `supports_streaming`, `supports_vision`, and `supports_json_mode` are not consistently discovered or enforced; provider class flags dominate some paths.

**Required revision:** Define effective capability = provider capability + model capability + successful probe. Disable unavailable UI/actions and fail clearly before making unsupported requests.

### PROV-03 — Provider/model operational fields are disconnected

- `AI Provider.model_prefix` is unused.
- `AI Model.versions` and most AI Model Version fields are unused.
- Model pull has only terminal notification, not progress.
- There is no managed delete/unload workflow in the Desk even though Ollama adapter supports delete.

**Required revision:** Implement or remove fields. If retained, create version records on digest change/pull, add pull progress, cancellation, delete confirmation/reference checks, and stale-version cleanup.

---

## 5.9 Folder and File facade

### FILE-01 — Canonical move failure can fall back to native mutation

The File paste override catches canonical service failure and calls the original native paste.

**Impact:** The fallback can bypass AI Document provenance synchronization—the exact invariant the canonical service exists to protect.

**Required revision:** Fail closed for AI-managed/linked files. If a safe native fallback is retained for unrelated files, classify the selected set first and never mix paths in one operation.

### FILE-02 — URL-only post-upload resolution is ambiguous

`upload_file_with_folder()` resolves a File by `file_url` when no stable name is supplied. Duplicate File rows can share a URL.

**Required revision:** Require `file_record`/name from all supported uploader callbacks. Reject ambiguous legacy URL requests using the same resolver as ingestion.

### FILE-03 — “My Uploads” is shared, not per-user

The default path is `Home/My Uploads`; it is not user-specific despite comments describing it as per-user.

**Required revision:** Decide between a shared uploads folder and actual per-user folders. If per-user, use stable user identity in the path/metadata, enforce permission inheritance, and migrate existing files deliberately.

### FILE-04 — Storage folder setting has the wrong shape/default behavior

The setting is a Data field defaulting to `AI Platform`, while actual folders are full File names such as `Home/AI Platform`. Default selection also does not verify write permission before returning the configured folder.

**Required revision:** Make it a Link to File filtered to folders, seed a canonical path, validate write/access behavior, and define whether it is global or only a manager/system output location.

### FILE-05 — Folder picker is bounded, eager, and cannot reach deep trees

The uploader fetches and flattens the tree to depth 6. Large trees cause N+1 server work and deep folders are absent.

**Required revision:** Use a lazy Link/tree picker with search, breadcrumbs, favorites/recents, permission-aware pagination, and no global full-tree fetch.

### FILE-06 — “Shared with me” is actually “public files”

The tab query is `is_private = 0`, not Frappe sharing membership.

**Required revision:** Rename it to Public or implement true shared-with-me semantics.

### FILE-07 — Global Desk monkey patches are fragile

The app replaces `frappe.ui.FileUploader`, `frappe.file_manager.paste`, `FileView.prototype.file_menu_items`, `frappe.require`, socket initialization, and realtime methods.

**Required revision:** Minimize patches, prefer supported hooks/subclass extension points, version-gate any unavoidable patch, and add browser smoke tests against the exact Frappe v17 version. The Desk guard should not suppress missing core bundle failures; deployment should fix asset hashes, and the UI should show a recoverable diagnostic.

---

## 5.10 Operations, backup, and audit

### OPS-01 — GitHub Actions is not currently a gate

Every recent workflow run fails before executing steps. The check annotation says the account has failed payments or needs a higher spending limit.

**Required revision:** Fix GitHub billing/spending limits, rerun CI, then require Server, Linter, and dependency checks in branch protection. Do not merge on local test evidence alone after this point.

### OPS-02 — Health-check interval logic is approximate

The five-minute cron uses minute modulo arithmetic. Intervals not divisible by five do not run at the configured cadence.

**Required revision:** Compare `last_health_check` to a timestamp threshold and atomically claim provider checks.

### OPS-03 — Operations UI lifecycle and drill-down are incomplete

The page starts a 30-second interval and has no page-hide cleanup. Usage-report API exists but no usage chart/page consumes it. Queue summary has counts only.

**Frontend/backend completion:** Cleanup timers, add time-range charts, provider/model/user/operation filters, queue/job/stuck-run drill-down, and export. Add SLO cards and alert history.

### OPS-04 — Export/import is not a full restore path

- Export can include embeddings, but import ignores exported chunks/embeddings.
- Import discards much metadata, tags, folder placement, extraction results, patterns, translations, and corpus policy.
- Large exports are assembled entirely in memory.
- There is no export format version or integrity checksum.
- Scheduled backups have no retention or restore verification.

**Required revision:** Design a versioned manifest, stream large exports, include selectable components, verify checksums, restore embeddings only when model/dimension identity matches, add retention, and run automated restore drills.

### OPS-05 — Audit/execution trace links are incomplete

- `AI Message.execution_log` is never populated.
- `AI Task.execution_log` is never populated.
- `AI Execution Log.queue_time_ms` is never populated.
- Failed log traceback capture can occur outside the active exception context.
- Stale Running logs are not reconciled after worker/process death.

**Required revision:** Return execution identity from engine calls, link it to messages/tasks/steps, capture tracebacks inside exceptions, add queue timestamps/heartbeats, and reconcile stale states.

### OPS-06 — Backup and cleanup jobs need bounded batches

Several jobs use broad deletes or whole-corpus exports. They need batching, continuation state, per-item savepoints, metrics, and failure summaries to remain safe on large sites.

---

## 5.11 Learning facade and reports

### LEARN-01 — Learning report filters are effectively disconnected

The report JSON declares `Query Report` and contains static SQL, while `.py` files implement filter-aware `execute()` functions. Script execution is not used for Query Reports. The Memory Usage script also uses `NULLS LAST`, which is not MariaDB syntax.

**Required revision:** Convert to Script Reports and use query builder/portable SQL, or put validated filter placeholders in Query Report SQL. Add report execution/filter tests.

### LEARN-02 — Learning overview/list APIs have no first-class frontend

The workspace links to raw DocType lists/reports; no page consumes `overview()`, `list_candidates()`, `list_memories()`, or `list_skills()`.

**Frontend completion:** Build a Learning Dashboard with pending-review queue, conflicts, approval actions, memory health, skill usage, scope filters, and feedback trends.

### LEARN-03 — Memory embeddings are stored but not used in recall

Promotion can persist embeddings, but recall ranks via lexical coverage/Jaccard only.

**Required revision:** Add hybrid semantic + lexical memory recall grouped by embedding model, with a lexical fallback and tests for paraphrases.

### LEARN-04 — Skills are not relevance-ranked

All enabled applicable skills are candidates for prompt injection, bounded only by character output.

**Required revision:** Add trigger descriptions/tags/embeddings and rank skills against the current request. Define conflict/priority/version behavior.

### LEARN-05 — Lifecycle maintenance is incomplete

No automated stale/low-quality review policy, merge workflow, replacement/supersession relation, or memory re-embedding job exists.

**Required revision:** Add merge/supersede/archive flows, quality thresholds, model-change re-embedding, and manager review queues.

---

## 6. Disconnected and declared-only schema inventory

Every field below needs an explicit **implement, repurpose, hide, or remove** decision. Keeping inert controls damages operator trust.

| Entity/field | Current state | Recommended decision |
| --- | --- | --- |
| AI Agent `fallback_answer` | Unused | Implement strict-grounding fallback or remove. |
| AI Agent Knowledge Base `weight` | Unused | Apply in retrieval fusion. |
| AI Conversation `context_document` | Unused | Wire to focused-document retrieval and UI. |
| AI Conversation `pinned` | Backend/list only | Add pin/unpin Assistant action. |
| AI Message `execution_log` | Unused | Link to the final/associated execution(s), possibly via a child relation for tool loops. |
| AI Task `due_date` | Unused | Implement scheduler claim or remove. |
| AI Task `priority` | Display only | Map to queue/ordering or state that it is informational. |
| AI Task `execution_log` | Unused | Populate. |
| AI Execution Log `queue_time_ms` | Unused | Populate from enqueue/start timestamps. |
| AI Folder Settings `knowledge_tag` | Unused | Implement folder retrieval/tag filtering or remove. |
| AI Folder Settings `access_policy` | Informational only | Label clearly or integrate with governance without replacing Frappe permissions. |
| AI Folder Settings `is_archived` | Unused | Implement archive semantics or remove. |
| AI Model `supports_json_mode` | Unused | Enforce effective capabilities. |
| AI Model `max_concurrent_requests` | Unused | Enforce lease. |
| AI Model `versions` | Unused | Populate/version lifecycle or remove child table. |
| AI Provider `max_concurrent_requests` | Unused | Enforce lease. |
| AI Provider `rate_limit_per_minute` | Unused | Enforce distributed limiter. |
| AI Provider `model_prefix` | Unused | Implement adapter mapping or remove. |
| Platform `default_context_window` | Unused | Use as fallback when model metadata is absent. |
| Platform `encrypt_documents` | Unimplemented | Remove/hide or implement real encryption. |
| Platform `translation_index_output` | Loaded, not applied | Apply as default. |
| Prompt Variable `variable_type` | Unused | Coerce/validate preview and execution inputs. |
| Knowledge Base `top_k` | Mostly ignored | Apply precedence per KB. |
| Knowledge Base `similarity_threshold` | Mostly ignored | Apply per KB before fusion. |
| Extraction Schema `target_doctype` | Unused | Add governed mapping flow or remove. |
| AI Usage Snapshot `document_count` | Unused | Populate from ingestion or remove. |
| AI Document source `Folder` | Unsupported | Implement recursive manifest or remove option. |
| AI Document `is_private` | Not an access authority | Clarify/remove; KB permissions govern extracted content. |
| Model type `Reranker` | No execution path | Implement or hide. |

---

## 7. Backend facade revision

The backend should be revised around explicit contracts rather than more direct DocType/API coupling.

## 7.1 Public API boundary

Create shared input helpers or typed request objects for:

- JSON/list coercion with exact accepted types.
- Required non-empty text.
- Name/title length limits.
- Bounded list count and payload size.
- Bounded `limit`, `offset`, `top_k`, days, segment counts, and document counts.
- Enum validation (`search_type`, statuses, language, tone, task type).
- Idempotency keys for mutations and job starts.
- Stable File identity requirements.

Recommended default caps:

| Input | Proposed default/hard cap |
| --- | --- |
| Chat message | 32k chars default; configurable hard cap |
| Documents attached per turn | 10 default, 25 hard |
| Knowledge bases per request | 25 hard |
| Search `top_k` | 20 default, 100 hard |
| Conversation list page | 50 default, 200 hard |
| Conversation message page | 100 default, cursor-paginated |
| Chunk/entity list | 100 default, 500 hard |
| Translation list | 50 default, 200 hard |
| Learning list | cursor/offset paginated, 200 hard |
| Admin usage range | 1–366 days |
| Model test prompt | 8k chars hard |

Use a consistent error envelope with a machine code, safe message, field errors, correlation ID, and retryability.

## 7.2 Service-layer authorization

Every public service entry point should enforce its own authority, even when current API wrappers already do. This protects calls from DocType methods, hooks, jobs, and extension apps.

Required invariants:

- No `get_all` on user-visible data unless authorization has already reduced IDs to an allowed set.
- List and direct-document permissions must have parity tests.
- Worker jobs must persist requester identity and revalidate it at execution.
- System/background authority must be explicit, not an accidental Administrator session.
- Generic tools must honor row and field permissions.

## 7.3 Durable job contract

Standardize ingestion, translation, pipelines, tasks, model pulls, backups, and scans around a common job lifecycle:

`Draft → Queued → Running → Waiting Approval/Retrying → Completed/Partial/Failed/Cancelled`

Each job-like record should have:

- stable job/idempotency key;
- requested by/on;
- queued/start/heartbeat/finish timestamps;
- progress current/total/stage/message;
- attempt/max attempts/next retry;
- cancel requested/on/by;
- error code, safe message, traceback reference;
- worker/job identity;
- realtime event and polling endpoint;
- stale-run reconciliation.

Do not duplicate all fields if a shared child/linked `AI Job Run` DocType is more maintainable.

## 7.4 Query and storage portability

Make an explicit product decision:

- **MariaDB only:** document and enforce it, remove misleading portability assumptions, and test supported versions; or
- **MariaDB + PostgreSQL:** replace backticks, `date_sub`, `sha2`, and database-specific SQL with Frappe query builder or dialect helpers, including reports and migrations.

The recommendation is to use query builder for new work and incrementally migrate current raw SQL, prioritizing permission-sensitive and frequently executed paths.

## 7.5 Schema evolution

Do not edit already-executed patch behavior destructively. Add new patches for:

1. safety defaults and hidden misleading fields;
2. translation memory scope/version;
3. durable scan/job state;
4. task state machine and transitions;
5. retrieval embedding-model reconciliation;
6. report type conversion;
7. indexes and uniqueness constraints;
8. versioned backup manifest.

Every patch must be idempotent and tested from a representative old schema/data snapshot.

---

## 8. Frontend facade revision

## 8.1 Shared frontend architecture

The four custom pages currently duplicate utilities and directly construct large HTML strings. Introduce a small app UI layer:

- RPC wrapper with normalized errors, cancellation, loading state, and correlation IDs.
- Reusable status pill, empty state, error state, progress bar, pagination, entity chip, citation, and confirmation components.
- One relative-time and compact-number utility.
- Route-state parser/serializer.
- Realtime subscription lifecycle helper that subscribes on show and unsubscribes on hide/destroy.
- Accessible dialog/form helpers.
- CSS classes instead of repeated inline styles.

Stay Frappe-native; a full SPA framework migration is unnecessary unless the product scope expands substantially.

## 8.2 AI Assistant completion

Backend and UI should jointly add:

- route/deep-link conversation opening;
- synchronized agent/model/KB/focused-document state;
- attachment chips with exact File identity and upload/processing progress;
- Stop generation and Retry turn;
- reconnect recovery using turn status;
- rename, pin, archive/restore, delete, export;
- archived and paginated conversation lists;
- negative-feedback reason/correction dialog;
- approval cards inline for pending tools, with manager/user-appropriate actions;
- citation context synchronized to the selected answer, not only the latest rendered answer;
- mobile layout and keyboard navigation;
- screen-reader labels and focus management.

## 8.3 Knowledge Explorer completion

Add:

- URL-persisted query, mode, KB, folder, and page state;
- lazy folder scope selector;
- pagination/cursor and “load more”;
- search diagnostics visible to managers (strategy, corpus candidates, models, thresholds, fallback reason);
- query history/saved searches where desired;
- entity/pattern facets;
- document language/type/date/status filters;
- explicit degraded-mode banner when semantic embedding fails and Hybrid falls back to keyword;
- secure result export;
- upload queue/progress instead of a fixed three-second refresh.

## 8.4 Translation review completion

Replace the modal-only review experience with a dedicated page or robust form section:

- virtualized segment list for large documents;
- source/target synchronized scrolling;
- flagged filters and issue facets;
- editable target text with rescore/review audit;
- per-segment reviewer notes;
- progress/cancel/retry;
- glossary term highlight;
- keyboard review workflow;
- reviewer identity/timestamps;
- text and optional reconstructed-format export.

## 8.5 Pipeline/automation/task frontend

- Typed pipeline builder and test runner.
- Automation event/action wizard with source/target field pickers and condition preview.
- Task action toolbar matching the real state machine.
- Run timeline with step logs, outputs, retries, approval pauses, and cancellation.
- Schedule next-run/misfire visibility.

## 8.6 Operations and Learning dashboards

Operations:

- time-series usage/failure/latency charts;
- provider/model saturation and concurrency;
- queue/stuck jobs;
- recent alerts and audit events;
- backup status and restore drill;
- filterable approval queue;
- readiness checks for optional dependencies and CI/release version.

Learning:

- overview counters;
- pending/conflict review inbox;
- candidate comparison/merge;
- memory relevance and feedback health;
- skill usage/version/scope;
- audit/provenance preview.

## 8.7 Remove risky global recovery behavior

`desk_guard.js` should be reduced to app-specific graceful degradation. Missing `desk.bundle`, `form.bundle`, or other core assets must not be silently suppressed. Those are deployment errors that should produce a clear reload/rebuild diagnostic, not a partially functioning Desk.

---

## 9. Test and quality strategy

## 9.1 Keep the current Python suite, but close its blind spots

The current 392 tests are valuable and fast. Add targeted suites for every finding above rather than replacing them.

### New backend unit/integration categories

- Retrieval completeness beyond 200/500 rows.
- Mixed KB embedding models/dimensions.
- KB threshold/top-K/weight precedence.
- Translation memory isolation and policy-version compatibility.
- Generic tool row/field permission parity.
- Conversation latest-history and concurrent sequence allocation.
- Task state authorization and every task type.
- Automation delete-event snapshot.
- Scheduled-pipeline atomic claim/misfire.
- Provider/user/model concurrency leases and quota reservations.
- URL/DNS/proxy/redirect SSRF cases.
- ODT/ODS archive bombs and parser resource caps.
- PDF OCR and MSG fixtures when optional extras are installed.
- Export/import round-trip for every selected component.
- Stale job/log reconciliation.
- Upgrade patches from prior releases.

## 9.2 Frontend tests

Introduce:

- **Unit:** Jest/Vitest or the Frappe-supported frontend test setup for route parsing, RPC state, rendering helpers, and formatters.
- **Browser E2E:** Playwright/Cypress against a real bench for Assistant, upload→ask, Knowledge Explorer, translation review, folder operations, pipeline run/cancel, approvals, and role visibility.
- **Accessibility:** automated axe checks plus keyboard smoke tests.
- **Responsive:** desktop/tablet/mobile snapshots for the four custom pages.
- **Compatibility:** exact supported Frappe v17 minor versions.

## 9.3 Real-runtime and optional-dependency tests

Maintain a non-blocking nightly/manual matrix:

- Ollama small chat model.
- Ollama embedding model.
- One OpenAI-compatible local runtime.
- PDF/DOCX/XLSX/PPTX/ODT/ODS.
- Tesseract image OCR and PDF OCR.
- Streaming, tool calls, JSON schema output, timeout, offline, and OOM simulation.

No GPU should be required for pull-request CI, but a real-runtime smoke suite is required before release.

## 9.4 Performance and chaos tests

Define representative sizes:

- 1k, 10k, and 100k chunks;
- 100 concurrent chat/search requests within configured limits;
- deep folder tree and 500-node mutations;
- 1,000-segment translation;
- long pipeline with cancellation and worker death;
- Redis/provider outage during each job type;
- large export/restore.

Record p50/p95/p99 latency, memory, DB queries, worker time, and correctness—not only whether calls complete.

## 9.5 Static quality gates

- Restore Actions execution and branch protection.
- Run Ruff lint/format with a documented baseline.
- Add type checking for service/API contracts where Frappe typing permits.
- Run ESLint/Prettier plus frontend tests.
- Run Semgrep and dependency audit.
- Add JSON/schema/workspace/report validation.
- Add docs link and API inventory checks.
- Measure coverage by backend domain and frontend page; do not use one aggregate number as the only gate.

Replace 95 deprecated `limit_page_length`/`limit_start` usages with v17 `limit`/`offset` outside immutable historical patches, then update mocks/tests accordingly.

---

## 10. Documentation and product-claim revision

Before the next release:

1. Replace “complete” and absolute “never leaves” claims with precise, testable statements until security hardening is complete.
2. Document the supported database(s), exact Frappe/Python/Node versions, optional system packages, and maximum tested corpus sizes.
3. Distinguish image OCR from scanned-PDF OCR until the latter exists.
4. Stop advertising `.msg`, Folder source, reranking, encryption, model versions, or target-DocType extraction unless implemented.
5. Explain that current translation output preserves extracted-text structure, not the original binary file format.
6. Version the REST API and publish pagination/error/idempotency rules.
7. Add operator runbooks for runtime outage, worker outage, stuck jobs, failed migrations, backup restore, model replacement, embedding migration, and key rotation if encryption is implemented.
8. Keep `PROJECT_STATUS.md` branch-neutral and generated/current; do not embed an old PR/branch snapshot as permanent project truth.

---

## 11. Prioritized implementation roadmap

The estimates below are engineering effort, not guaranteed calendar duration. A single senior engineer working sequentially should expect roughly **20–30 engineer-weeks** for production hardening, excluding original-format document translation and a large-corpus external/vector backend. A small team can parallelize frontend, retrieval, and operations after Phase 1.

## Phase 0 — Truthful baseline and functioning quality gate

**Priority:** P0
**Estimated effort:** 2–4 days plus GitHub account remediation

### Work

- Fix GitHub Actions billing/spending limit and rerun all workflows.
- Require CI checks before merge.
- Update status/README claims and add this roadmap.
- Add a tracked gap register with owner, target release, and acceptance test.
- Choose MariaDB-only versus MariaDB/PostgreSQL support.
- Choose implement/remove decisions for encryption, Folder source, reranker, model versions, and original-format translation.

### Exit criteria

- Server, lint, and dependency workflows actually execute and pass.
- Branch protection requires them.
- Product documentation no longer labels declared-only functions as complete.
- Every remove-or-implement item has an explicit decision.

## Phase 1 — Isolation, permission, and API safety

**Priority:** P0
**Estimated effort:** 2–3 weeks

### Backend

- Fix translation memory scope and policy identity.
- Harden generic tool row/field permissions.
- Harden provider network transport against proxy/DNS/redirect issues.
- Fix folder settings list permission parity.
- Remove canonical folder mutation fallbacks.
- Require stable File IDs.
- Centralize API validation/caps/pagination.
- Enforce explicit model type.
- Redact/minimize search telemetry.
- Hide/remove the encryption control unless implementation is funded now.

### Tests

- Multi-user/multi-KB isolation suite.
- Field-permission tool suite.
- SSRF/proxy/DNS suite.
- API abuse/bounds suite.
- File identity/fallback regression suite.

### Exit criteria

- No known cross-KB memory path.
- Generic tools cannot reveal rows/counts/fields outside user authority.
- Every list endpoint has a hard cap or pagination.
- Strict-local transport tests cover DNS, IPv4/IPv6, proxy variables, redirects, and allowlists.

## Phase 2 — Retrieval correctness and corpus scale

**Priority:** P0/P1
**Estimated effort:** 3–5 weeks

### Backend

- Correct full-corpus candidate evaluation for the supported scale.
- Group query embeddings by KB embedding model.
- Apply KB thresholds/top-K/weights.
- Fix folder descendant matching.
- Improve context packing/deduplication.
- Add retrieval diagnostics.
- Decide/implement reranker path.
- Add indexing/retrieval indexes and corpus-size readiness checks.

### Frontend

- Search diagnostics/degraded-mode banner.
- Pagination and persistent filters.
- Folder and entity filters.

### Exit criteria

- Relevant results beyond old 200/500 boundaries are found.
- Mixed-model KB search is deterministic and tested.
- Effective retrieval settings shown in diagnostics match configuration.
- Published performance envelope exists for 1k/10k/target maximum chunks.

## Phase 3 — Conversation and agent completion

**Priority:** P1
**Estimated effort:** 2–4 weeks

### Backend

- Latest-N history and summary compaction.
- Turn IDs and atomic message sequencing.
- Conversation state/model/agent rules.
- Focused document, fallback answer, weights, and citation modes.
- Turn cancellation/status.
- Pin/restore/export and paginated message API.

### Frontend

- Deep-link route handling.
- Conversation menus, archived view, pagination.
- Attachments and processing state.
- Stop/retry/reconnect.
- Full negative-feedback dialog.
- Accessibility/mobile pass.

### Exit criteria

- A 100-message conversation uses the latest context correctly.
- Concurrent sends cannot corrupt order.
- Reload/disconnect preserves accurate turn state.
- Every conversation field exposed in the UI has functioning behavior.

## Phase 4 — Ingestion, intelligence, patterns, and translation completion

**Priority:** P1
**Estimated effort:** 4–6 weeks

### Ingestion/intelligence

- Implement/remove Folder source and MSG.
- Add scanned-PDF OCR if retained in scope.
- Extend archive security.
- Persist reader warnings/progress.
- Add hierarchical summaries and whole-document classification/extraction/compare strategies.
- Implement governed extraction-to-DocType mapping.

### Translation/patterns

- Apply translation defaults and restore worker user safely.
- Progress/cancel/retry and realtime review updates.
- Version/rescore memory and glossary permissions.
- Integrate back-translation metrics/issues.
- Add durable pattern scan state and correct offsets/validators.
- Build Pattern Explorer.

### Exit criteria

- Every advertised file format has a real fixture test.
- Large documents report coverage and do not silently drop tail content.
- Translation jobs are observable/cancellable and memory-safe.
- Pattern zero-result documents are not rescanned indefinitely.

## Phase 5 — Automation, pipelines, tasks, and approvals

**Priority:** P1
**Estimated effort:** 3–5 weeks

### Backend

- Event snapshot and automation run records.
- Atomic rule counters/revision dedupe.
- API/document-ingest pipeline triggers.
- Atomic schedule claim/misfire policy.
- Waiting Approval/resume pipeline state.
- Complete AI Task types and secure state transitions.
- Common job lifecycle/heartbeat where practical.

### Frontend

- Pipeline builder/test runner.
- Automation rule wizard.
- Task actions and progress.
- Run timeline and approval resume UX.

### Exit criteria

- Every trigger type has an integration test.
- Delete-event rules work from immutable snapshots.
- Scheduled pipelines cannot duplicate under overlap.
- AI Users cannot self-approve tasks.
- Approved pipeline tools resume exactly once.

## Phase 6 — Governance, operations, learning, and backup

**Priority:** P1/P2
**Estimated effort:** 3–5 weeks

### Governance/providers

- Distributed user/provider/model leases.
- Provider rate limiter.
- Reservation-based quota accounting.
- Correct failover model groups and actual-model logs.
- Model capability/version/pull/delete lifecycle.

### Operations/learning/backup

- Usage/latency/failure charts and job drill-down.
- Alert/SLO policies and stale-run reconciliation.
- Learning Dashboard and fixed reports.
- Semantic memory/skill relevance.
- Versioned streaming backup and verified restore.
- Backup retention and restore drills.

### Exit criteria

- Declared policy fields demonstrably affect execution.
- Saturation and quota behavior is race-safe under load.
- Backup restores selected data and compatible embeddings in automated tests.
- Learning reports filter correctly and the dashboard is permission-safe.

## Phase 7 — Production qualification and release

**Priority:** P1
**Estimated effort:** 2–4 weeks

### Work

- Browser/E2E matrix.
- Real-runtime/optional-dependency matrix.
- Load and chaos test target deployment.
- Upgrade from last public schema and rollback rehearsal.
- Security review and remediation.
- Accessibility review.
- Operator runbooks and support bundle.
- Semantic version bump, changelog, release notes, signed/tagged release.

### Exit criteria

- No open P0/P1 defects.
- CI and nightly matrices green.
- Published performance/security/support envelope.
- Successful backup/restore and upgrade rehearsal on a production-like site.
- Release candidate observed under normal workload before general availability.

---

## 12. Recommended issue/epic structure

Create GitHub epics with child issues rather than one unbounded “finish project” issue:

1. **Security and isolation hardening** — SEC-01 through SEC-07.
2. **Retrieval correctness and scale** — RET-01 through RET-07.
3. **Assistant and conversation completion** — CHAT-01 through CHAT-08.
4. **Ingestion and intelligence completeness** — ING/INT findings.
5. **Translation and pattern workflow** — TRN/PAT findings.
6. **Automation/pipeline/task state machines** — AUTO/PIPE/TASK findings.
7. **Governance/provider/model enforcement** — GOV/PROV findings.
8. **File/folder integration hardening** — FILE findings.
9. **Operations, backup, and audit** — OPS findings.
10. **Learning UX and reports** — LEARN findings.
11. **Frontend architecture, accessibility, and E2E**.
12. **Release engineering and production qualification**.

Every issue should include:

- observed current behavior;
- intended contract;
- backend and frontend impact;
- permission/security considerations;
- migration/data impact;
- acceptance criteria;
- unit/integration/E2E tests;
- documentation updates;
- rollback strategy.

---

## 13. Definition of done for any feature

A feature is not **READY** until all applicable items are true:

### Contract

- The behavior is documented with limits, states, errors, and permissions.
- Every visible setting changes behavior, or is explicitly informational.
- All API inputs are typed, validated, and bounded.

### Backend

- Authorization is enforced in the service, not only the UI/API wrapper.
- Concurrency and idempotency are defined.
- Background authority and recovery are defined.
- Audit/metrics do not silently disclose sensitive content.
- Large inputs have bounded resource behavior.

### Frontend

- Loading, empty, partial, failed, offline, cancelled, and success states exist.
- Realtime behavior has polling/reconnect fallback.
- Route state and deep links work.
- Keyboard/mobile/accessibility behavior is tested.

### Data/migrations

- Schema constraints and indexes exist.
- Upgrade patch is idempotent.
- Old records receive a safe default or explicit review state.
- Export/backup impact is addressed.

### Tests and operations

- Unit and integration tests cover success and failure.
- Browser test covers the primary user path.
- Permission tests use at least manager, normal user, auditor, and unrelated user.
- Load/real-runtime tests exist when relevant.
- CI runs and passes.
- Monitoring and runbook changes are included.

---

## 14. Recommended next action

Start with **Phase 0 and Phase 1**, not a new feature:

1. Fix GitHub Actions account billing/spending limits and rerun the checks.
2. Open the Security and Isolation epic.
3. Fix translation-memory scoping first, with a regression test.
4. Fix generic tool count/field permissions.
5. Remove the canonical File fallback and require stable File identity.
6. Introduce shared API bounds/validation.
7. Hide the non-functional encryption control until a real design is approved.
8. Then begin retrieval correctness work before adding any more knowledge/search UI.

This sequence closes the highest-risk gaps while preserving the existing tested architecture. It also creates a trustworthy baseline from which frontend polish, automation completeness, and scale work can proceed in parallel.
