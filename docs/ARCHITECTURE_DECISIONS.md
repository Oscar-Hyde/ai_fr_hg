# Architecture decisions

These decisions make the supported product boundary explicit. They remain authoritative until superseded by a dated decision in this file. A hidden compatibility field is not a supported capability.

## Decision summary

| ID | Decision | Status | Owner | Revisit |
| --- | --- | --- | --- | --- |
| ADR-001 | Support MariaDB only | Accepted | Architecture | Before adding a PostgreSQL CI matrix |
| ADR-002 | Do not provide application-level document encryption | Accepted | Security | Only with an approved key-management threat model |
| ADR-003 | Remove `AI Document` Folder as an ingestion source | Accepted | Knowledge/Ingestion | Only with a manifest/job contract that retains native File authority |
| ADR-004 | Remove reranker exposure until execution exists | Accepted | Retrieval | Phase 2 |
| ADR-005 | Hide model versions until provider lifecycle exists | Accepted | Provider Operations | Phase 6 |
| ADR-006 | Preserve extracted-text structure, not original binary format | Accepted | Translation | Phase 4 product review |
| ADR-007 | Pin the Frappe v17 development revision until stable v17 exists | Accepted with pre-release limitation | Release Engineering | When upstream publishes stable v17 |
| ADR-008 | File intelligence is custom extraction on native File bytes; advertise only real parsers | Accepted | Knowledge/Ingestion | When a real parser exists for a currently unsupported family |
| ADR-009 | Runtime admission control is Redis/Lua on `frappe.cache()`, and fails open when Redis is down | Accepted | Governance | If a non-Redis deployment target is ever supported |
| ADR-010 | Enforced model capability defaults to the adapter's transport capability | Accepted | Provider + Governance | When a capability probe protocol exists for every supported runtime |
| ADR-011 | Semantic entities are model-inferred and mechanically grounded | Accepted | Knowledge/Intelligence | When a local NER runtime is approved |
| ADR-012 | Legacy binary Office formats (DOC/XLS/PPT) remain unsupported | Accepted | Knowledge/Ingestion | When an OLE2 parser or sandboxed converter is approved |
| ADR-013 | A user archive is one document, not a synthetic folder tree | Accepted | Knowledge/Ingestion | If per-member citation granularity is required |
| ADR-014 | Retention deletion is batched on native primitives, not a custom queue | Accepted | Operations | If retention volume outgrows a scheduled batch drain |

## ADR-001 — MariaDB-only application support

**Context.** The application contains MariaDB-specific SQL, while a few comments and helpers imply cross-database behavior. The existing CI service is MariaDB. Claiming PostgreSQL support without a PostgreSQL suite would be misleading.

**Frappe v17 capability evaluated.** Frappe v17 has database abstractions, ORM, query builder, and PostgreSQL support. Those facilities can make new queries portable, but they do not automatically make existing raw SQL, reports, and patches portable.

**Decision.** AI Fr HG supports **MariaDB 11.8 only** for the current Frappe v17 development baseline. New application queries should still prefer the Frappe ORM/query builder. PostgreSQL-specific branches may remain where they improve future portability, but they are unqualified and unsupported until a full install/migrate/test/browser matrix passes on PostgreSQL.

**Consequences.** CI and production documentation identify MariaDB 11.8. PostgreSQL defects are not release blockers under this decision. Removing a misleading dual-database promise is preferable to creating a custom database abstraction beside Frappe.

## ADR-002 — No application-level stored-document encryption

**Context.** `AI Platform Settings.encrypt_documents` was visible but no document, chunk, translation, index, backup, or search path encrypted/decrypted content. Implementing only selected text fields would leave searchable copies, logs, files, and backups outside the boundary.

**Frappe v17 capability evaluated.** Frappe Password fields protect individual secrets; they are not transparent encryption for searchable document corpora. Frappe site/database backups and the deployment platform can use encrypted storage, database encryption, filesystem encryption, and encrypted backup destinations.

**Decision.** Application-level document encryption is unsupported. The compatibility field is hidden, read-only, reset to `0` on migration, and rejected server-side if a caller attempts to enable it. Operators must use deployment-layer encryption at rest and access controls.

**Why no custom mechanism.** A custom field cipher would duplicate storage responsibility, break native querying/indexing, require a key hierarchy and rotation/recovery design, and still fail to cover native File attachments and backups. It will not be introduced without a complete threat model and lifecycle design.

**Data impact.** Existing text is unchanged. No data was previously encrypted by this flag. Resetting the flag removes a false security signal rather than decrypting or altering content.

## ADR-003 — Native File folders, no synthetic Folder ingestion source

**Context.** `AI Document.source_type = Folder` was selectable, but ingestion rejected it. The application already creates and tracks file-backed AI Documents through canonical File hooks/services.

**Frappe v17 capability evaluated.** Frappe File is the native folder tree, attachment, privacy, and file-identity authority. Its document events and background enqueueing are the correct integration points for processing files placed in folders.

**Decision.** Remove Folder from selectable AI Document sources and reject it server-side. Users ingest individual Files; Frappe File remains the only folder authority. Existing legacy Folder-source rows are retained for audit and are not destructively converted.

**Why no custom mechanism.** A second recursive folder manifest would compete with File lifecycle events, permissions, moves, and deletes. If a future bulk-folder action is needed, it must enumerate permission-checked File identities and enqueue canonical per-file ingestion; it must not make a folder itself an AI Document source.

## ADR-004 — No reranker exposure without an execution contract

**Context.** `Reranker` appeared as a model type and name inference could create it, but retrieval never called a reranking provider or adapter capability.

**Frappe v17 capability evaluated.** Frappe provides background jobs, DocTypes, configuration, and provider integration primitives, but reranking is application-domain behavior with no native Frappe implementation.

**Decision.** Remove `Reranker` from model choices and automatic creation. Known reranker names are reported as unsupported. Legacy rows are retained and disabled. **Phase 2 (2026-08-20) reaffirms this decision:** retrieval diagnostics report `reranker: unsupported`. No rerank execution path is introduced until a timeout, failure, and test contract exists.

**Why no placeholder.** A field or provider record without retrieval execution, timeout/failure behavior, diagnostics, and tests is not a feature.

## ADR-005 — Hide model versions until provider lifecycle integration exists

**Context.** The AI Model Versions child table was visible but not populated or connected to pull, activate, unload, delete, rollback, or compatibility behavior.

**Frappe v17 capability evaluated.** Frappe Version tracks changes to a Frappe document. It does not track external model binaries, provider tags/digests, downloads, activation, or rollback. Background jobs and realtime events are suitable primitives for a future lifecycle.

**Decision.** Preserve but hide/read-only the child table. Do not represent Frappe document history as external model version lifecycle. Phase 6 owns provider-backed model lifecycle, progress, and retention.

## ADR-006 — Extracted-text structure translation only

**Context.** Translation segments and reassembles extracted text. It does not reconstruct source PDF, DOCX, XLSX, PPTX, ODT, ODS, email, or image binaries. The label `Preserve Formatting` overstated that behavior.

**Frappe v17 capability evaluated.** Frappe File Manager stores originals and generated attachments but has no generic cross-format translation/reconstruction engine. Native File remains the correct owner of the original attachment.

**Decision.** Rename the control to **Preserve Extracted-Text Structure** and state that it preserves blocks, separators, and spacing in text output only. Original files remain unchanged. Download output is text; indexing creates a text AI Document.

**Why no custom mechanism now.** Format reconstruction requires a separate adapter and validation contract for every binary type, including tables, styles, drawings, formulas, pagination, macros, signatures, and malformed inputs. A generic claim would be unsafe.

## ADR-007 — Frappe v17 development pin

**Context.** On 2026-08-19, upstream `frappe/frappe` has no stable v17 tag and no `version-17` branch. Upstream `develop` identifies as `17.0.0-dev`, requires Python `>=3.14,<3.15`, requires Node `>=24`, and tests against MariaDB 11.8.

**Frappe v17 capability evaluated.** Bench supports selecting a Frappe branch, and Git supports checking out an immutable revision. Referencing a nonexistent `version-17` branch cannot form a quality gate; following moving `develop` cannot form a reproducible gate.

**Decision.** CI initializes from `develop` and then checks out immutable Frappe revision `d7000da3d5862087d3df08e009fe76518ea649c4`. The supported pre-release toolchain is Python 3.14, Node 24, and MariaDB 11.8.

**Consequences.** This is a reproducible v17 development baseline, not proof of compatibility with a future stable v17 release. Phase 7 must replace the pin with an approved stable v17 ref, rerun the full qualification matrix, and document supported v17 minors before a production verdict.

## ADR-008 — File intelligence: native File + custom extraction, truthful formats

**Context.** Wave 4 requires a file-intelligence pipeline: detect, resolve, extract, normalize, persist evidence, and fail visibly. A product that claims audio, video, database, OLE `.doc`/`.xls`/`.ppt`, Outlook `.msg`, scanned-PDF OCR, or generic archive-as-document support without a real parser would be a false capability.

**Frappe v17 capability evaluated.** Frappe File owns bytes, folders, privacy, attachments, and file identity. It does not detect formats, parse Office/PDF/email, preserve extraction provenance, or enforce ZIP-bomb policy. Frappe has no substitute for a document-reader registry.

**Decision.** Keep Frappe File as the only byte/folder/privacy authority. Own format detection, reader resolution, ZIP-container policy, and extraction evidence in `ai.extraction` + `ai.readers` + `ai.readers.archive`. Ingestion persists the outcome; it does not re-implement detection or archive policy. Advertised formats must have a real parser. The current boundary is:

- **Implemented:** text-layer PDF, DOCX, XLSX/XLSM, PPTX, ODT, ODS, ODP, CSV/TSV, EML, HTML, XML, JSON, Markdown, text/code, images (vision or optional image OCR).
- **Not implemented and not advertised:** audio, video, database files, OLE `.doc`/`.xls`/`.ppt`, Outlook `.msg`, scanned-PDF OCR, generic `zip`/`tar` as a document source.

**Why no custom File replacement.** A second file store would duplicate privacy, attachments, and identity. Extraction evidence is JSON on `AI Document`; it is not a parallel File table.

**Consequences.** 37 registered extensions. Extension/magic mismatches warn and prefer a real magic reader when one exists. ZIP bombs, traversal, and encryption fail closed through one archive guard. Extraction failures remain visible on the document (`error_message`, structured warnings, evidence). Browser E2E of the evidence UI remains Phase 7.

## ADR-009 — Redis-backed admission control, degrading open

**Context.** GOV-01, GOV-02 and GOV-03 require concurrency limits, provider
rate limits, and quota reservations to be enforced across every bench process:
web workers, background workers, and the scheduler. The decision has to be
atomic (a check that does not claim cannot stop a race), it has to expire on
its own (a killed worker must not hold a slot), and it sits on the hot path of
every model call.

**Frappe v17 capability evaluated.** Frappe provides `frappe.cache()` (a Redis
handle every process already shares), database row locks (`for_update`),
`frappe.enqueue` deduplication, and document locking. Row locks and document
locks are the right tool for *claiming a record* — which is why PIPE-02 and
OPS-02 use them — but they are the wrong tool for a shared counter: they
require a transaction per call, leave orphaned state behind a killed worker,
and would put write traffic inside a transaction the caller may roll back.
`frappe.rate_limiter` exists but is request-scoped for web requests, not a
distributed semaphore over providers and models.

**Decision.** Concurrency leases, rate windows and quota reservations are Redis
sorted sets manipulated by single Lua scripts through `frappe.cache()`. Each
decision — reap expired entries, compare against the limit, claim — happens
inside one server-side script, so it cannot interleave. Every entry carries an
expiry, which is what releases a dead worker's slot.

**Degradation.** If Redis is unreachable, admission control logs once per hour
and **allows** the call, marking the decision `degraded`. Quota falls back to
the committed-usage comparison, which still refuses a user already over the
limit. Failing closed was rejected: Frappe's own sessions, queues and realtime
already depend on Redis, so a Redis outage is a platform outage, and turning it
into a total AI outage adds no safety while removing every diagnostic path.

**Why not a new DocType.** A counter table would be a second scheduling and
locking subsystem beside Frappe's, would need its own sweeper for abandoned
rows, and would duplicate a responsibility Redis already owns for this app.

## ADR-010 — Enforced capability defaults to adapter transport

**Context.** PROV-02 makes `AI Model.supports_tools`, `supports_streaming`,
`supports_json_mode` and `supports_vision` real controls. Those fields were
cosmetic: nothing read them, and model discovery never populated them, so on
every existing installation they are `0`. Enforcing them literally would have
disabled tool calling, JSON mode and streaming everywhere on upgrade.

**Decision.** Effective capability is *adapter transport* AND *model
declaration* AND *last runtime probe*. Discovery seeds a new model's flags from
what its adapter can transport, and patch `v0_0_21_phase_6_governance`
backfills existing rows the same way. That is precisely the behaviour that
applied before enforcement, so nothing regresses; the migration only ever
raises a flag, never lowers one an operator set. Clearing a flag is now the
supported way to impose a restriction. Vision is seeded conservatively: only
for Vision-typed models or recognised vision model names.

**Probe.** When a runtime refuses a capability outright, the refusal is cached
against that model for an hour and the capability is treated as unavailable.
An unrecognised error never marks a capability missing.

**Consequence.** These flags are honest defaults, not verified facts. A model
that declares tool support and cannot deliver it fails at the runtime once, and
is then refused locally until the probe expires. A full pre-flight capability
probe per model is Phase 7 real-runtime matrix work.

## ADR-011 — Semantic entities are model-inferred, mechanically grounded

**Date.** 2026-08-21 (Part 1 §11).

**Context.** §11 requires people, organizations, locations, concepts, and
relationships. `ai.patterns` extracts only what a regex can prove, and
deliberately refuses lexical guesses. None of the five required categories has
a deterministic surface form, so the existing engine cannot satisfy §11 by
extension.

**Frappe V17 capability evaluated.** Frappe provides DocTypes, background jobs,
scheduling, permissions, caching, and the query builder — all used here. Frappe
has **no** native named-entity recognition or relationship inference. A domain
implementation is therefore required and duplicates no framework
responsibility.

**Options considered.**

1. *Local NER package* (spaCy or similar). Rejected for now: a heavyweight
   dependency plus model-weight distribution in a local-first application, and
   it would still need its own governance, failure, and versioning contract.
2. *Model layer via `engine.run_chat`.* Chosen. It reuses quota reservation
   (GOV-03), rate limiting (GOV-02), concurrency leases (GOV-01), failover
   (PROV-01), execution logging, and the INT-02 structured-output validator.
   No new governance surface is created.

**Decision.** Semantic extraction runs through the governed model path and is
constrained by three mechanical rules enforced in `ai.semantic`, not by prompt
instructions:

1. **Grounding.** Every entity value, relationship subject, relationship
   object, and evidence span must be locatable verbatim in the source text.
   Anything that cannot be located is discarded and counted as `ungrounded`.
2. **Confidence.** Every semantic row carries a model-reported confidence and
   is filtered against `semantic_confidence_floor` (default 50).
3. **Evidence.** Entities record a true character offset and a context quote;
   relationships require a supporting verbatim span, enforced in the DocType
   controller.

**Determinism carve-out.** §8 requires deterministic output "where possible".
Model inference is not deterministic, so this layer is explicitly excluded from
that clause. The three rules above are what make it auditable instead. Rows are
separated by `extraction_method`, so a deterministic pattern row can never be
confused with an inferred one, and `persistable_pattern_type` refuses a
semantic type on a `pattern` row.

**Opt-in.** `semantic_entities_enabled` defaults to off and patch
`v0_0_23_part1_file_intelligence` never enables it, because each scan costs a
model call. Deterministic pattern extraction is unaffected and remains the
default behaviour.

**Consequence.** Semantic entities are *evidence-backed inferences*, not facts.
Confidence is populated only for `semantic` rows; a deterministic match is
exact and carries no confidence, because inventing one would be a false signal.

## ADR-012 — Legacy binary Office formats remain unsupported

**Date.** 2026-08-21 (Part 1 §6.2).

**Context.** §6.2 lists `DOC`, `XLS`, and `PPT` as example formats. None is
registered. They are OLE2 compound-binary containers, unrelated to the OOXML
and OpenDocument ZIP families the platform parses.

**Frappe V17 capability evaluated.** Frappe File stores and serves the bytes
but performs no document parsing; there is no native converter.

**Options considered.**

1. *Add an OLE2 parser stack* (`olefile` plus per-format decoders). Rejected
   for now: three separate binary formats, each needing its own decoder,
   fixtures, and fuzz coverage.
2. *Out-of-process conversion* (LibreOffice headless). Rejected for now: an
   external binary with its own sandboxing, timeout, resource, and supply-chain
   contract — a deployment change, not a reader.
3. *Register the extensions anyway.* Rejected outright: that is exactly the
   "declared only" pattern Phase 0 removed, and ADR-008 forbids it.

**Decision.** `.doc`, `.xls`, and `.ppt` remain unsupported. Ingestion reports
them as unsupported with an actionable message rather than failing obscurely.
Revisit when a real parser or a sandboxed converter is approved, with the
threat model that decision requires.

**Consequence.** The supported-format list stays truthful. Users converting a
legacy file to its modern equivalent get full support immediately.

## ADR-013 — User archives are one document, not a synthetic folder tree

**Date.** 2026-08-21 (Part 1 §6.2 "Archives").

**Context.** §6.2 requires safe extraction, recursive processing, file
relationship tracking, and protection against unsafe input for archives.
ADR-003 already establishes that native Frappe `File` is the only folder
authority, and that no synthetic folder source may compete with it.

**Frappe V17 capability evaluated.** Frappe File provides the folder tree,
attachments, privacy, and identity. It has no archive-expansion capability and
no archive safety policy — `ai.readers.archive` already exists precisely
because Frappe has no ZIP-bomb policy.

**Decision.** An archive is ingested as **one** `AI Document` whose text is the
concatenation of its readable members and whose `structure` records the full
containment tree (`path`, `parent`, `depth`, `reader`, `bytes`). Members do not
become folders, and do not become independent documents.

**Why not one document per member.** That would create a second recursive tree
competing with `File` for identity, permissions, moves, and deletion —
precisely what ADR-003 forbids. It would also raise unanswered questions about
member permissions and re-upload identity.

**Safety.** One cumulative `ArchiveBudget` governs an entire nested walk:
`MAX_ARCHIVE_DEPTH` (3), `MAX_TOTAL_MEMBERS` (1000), `MAX_TOTAL_EXTRACTED`
(200 MB), and `MAX_MEMBER_SIZE` (50 MB). Because the budget is cumulative,
nesting cannot multiply cost. Path traversal, absolute paths, drive letters,
symlinks, hardlinks, and encrypted members are refused; a per-member failure is
isolated and reported rather than failing the archive.

**Office containers are excluded.** A `.docx` is a single document that happens
to be a ZIP, so it keeps the stricter single-document Office policy
(`validate_zip_container`). `_needs_zip_guard` excludes user-archive extensions
so the 500-member Office cap cannot reject a legitimate bundle, while
`ArchiveReader` still enforces ratio, traversal, and encryption rules through
the same authority module.

**Consequence.** Members are searchable through the parent document, and the
containment tree is fully reconstructable from evidence. Per-member retrieval
citation granularity is a future enhancement and is not claimed today.

## ADR-014 — Batched retention on native primitives

**Date.** 2026-08-21 (Part 2 §20.4, §26, §27).

**Context.** `cleanup_logs` issued one unbounded `DELETE ... WHERE creation <
cutoff` per DocType. On a site with millions of execution-log rows that is a
single multi-minute transaction: unbounded memory, long lock hold, and total
loss of progress if the worker is killed. Part 2 requires bounded resource
behaviour, checkpoints, and recovery for long-running work.

**Frappe V17 capabilities evaluated.**

- `frappe.db.delete` — issues the unbounded statement; it is the problem, not
  the fix, but it is still the correct way to *execute* each bounded batch.
- `frappe.get_all` with `limit_page_length` / `order_by` — the native paging
  primitive; used to select each batch.
- Scheduler events — already provide the periodic trigger and the natural
  continuation point between runs.
- Background jobs / `frappe.enqueue` — evaluated and rejected: retention is
  already a scheduled task, and fanning it into child jobs would add queue
  pressure and failure modes without improving boundedness.
- `frappe.db.savepoint` — evaluated and rejected: savepoints bound a rollback
  inside one transaction, but the requirement is to *commit* completed work so
  it survives a killed worker. Per-batch commit is the correct primitive.

**Decision.** `delete_expired_rows` composes `frappe.get_all` (select a bounded
page of names, oldest first) with `frappe.db.delete` (delete exactly that page)
and commits per batch. Defaults: 500 rows per batch, 20,000 per DocType per
run. Ordering by `creation asc` makes progress monotonic; the per-run ceiling
means the scheduler drains a backlog over several runs instead of one long
transaction. A failing DocType is logged and skipped so one bad table cannot
block retention for the others.

**Why this is not duplicating the framework.** No Frappe API performs batched,
committed, resumable retention. This is ~30 lines composing two native calls;
it introduces no scheduler, no queue, no cursor table, and no state of its own.
The continuation point is simply "rows still older than the cutoff", which the
next scheduled run rediscovers with the same query.

**Consequence.** Retention is bounded in memory and lock duration, and survives
worker restarts. A run that hits its ceiling logs that fact rather than
appearing to have finished. `backup_knowledge` is *not* covered by this change
and remains unbounded (OPS-06 stays open for that half).
