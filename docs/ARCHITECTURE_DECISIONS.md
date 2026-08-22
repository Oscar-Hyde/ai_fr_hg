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

## ADR-015 — Verification is tiered, and the tiers are not interchangeable

**Date.** 2026-08-21 (Part 2 §28, §29; written after the CLOSED-claim re-audit).

**Context.** The Part 2 review found that 29 of 38 "tests" backing recently
closed rows were `assertIn("field", source_text)` assertions. They pass when a
value is written to the wrong row, when it is `None`, and when the function
raises before reaching it; they fail only when a local variable is renamed.
Worse, two rows (SEC-07, PIPE-04) were closed citing tests that had never been
written at all, and one security claim (SEC-02) had no test referencing its
function anywhere in the repository.

The common failure is not laziness. It is that "there is a test" was treated as
a single, uniform kind of evidence, so a weak form could be substituted for a
strong one without anyone noticing. This ADR fixes the vocabulary so a claim
must state what kind of evidence it rests on.

**Frappe V17 capabilities evaluated.**

- `bench run-tests` / `FrappeTestCase` — the native runner, and the correct
  tool for the runtime tier. It requires a live site, MariaDB and Redis, none
  of which can be installed in this environment (HTTP egress is filtered, so
  `apt-get` cannot reach the Debian mirrors; no pip route exists for either
  server). It remains the target for Phase 7, not a substitute for it.
- `frappe.tests.utils` helpers — same dependency on a real site.
- Frappe's own `validate_fields` in `core/doctype/doctype/doctype.py` — used
  directly as the source of truth for the framework-validation tier rather
  than reimplementing schema rules from memory.

**Decision.** Five tiers, ordered by what they can prove. Every register row
states its tier.

| Tier | Proves | Cannot prove |
| --- | --- | --- |
| Static / AST | Structural invariants: wiring, reachability, one-route-per-operation, register honesty. | That the code behaves correctly when it runs. |
| Framework validation | Conformance to real Frappe v17 rules, cross-checked against a pinned `frappe/develop` checkout. | Anything needing a database or a request. |
| fakebench behaviour | Observable application logic — filters, ordering, permission scoping, persisted values — against a modelled bench. | SQL correctness, InnoDB isolation, index behaviour, concurrency, migrations. |
| Mutation | That the suites above actually fail when the behaviour breaks. | Anything no mutation targets. |
| Real runtime | MariaDB, Redis, workers, queues, `bench migrate`, browser Desk. | — |

Two rules follow. A row may not claim a tier above the evidence that exists
for it. And a row may not cite a symbol that no test references — enforced by
`TestRegisterEvidenceIsReal`, which fails the build.

**Consequences.** Closing a row is more expensive and states less. "CLOSED —
IMPLEMENTED (tier: fakebench behaviour)" is a narrower claim than "CLOSED"
was, and deliberately so: it says the logic is verified and the database
behaviour is not. Nothing in this repository currently rests on the runtime
tier, so no row may imply it.

---

## ADR-016 — fakebench: an in-memory Frappe substitute, and what it must never be trusted for

**Date.** 2026-08-21 (Part 2 §28).

**Context.** Behavioural testing needs a bench. No real one can exist here, and
the alternative that had taken hold — asserting against source text — produced
evidence that could not fail. A substitute was required that runs real
application code and lets a test observe resulting state.

**Frappe V17 capabilities evaluated.**

- `FrappeTestCase`, `bench run-tests` — the right answer, unavailable here
  (see ADR-015). The eight bench-only suites remain excluded from the offline
  batch and are the Phase 7 entry point.
- `unittest.mock` patching of `frappe` — rejected. Mocks assert that a call was
  made, which is the same tautology as asserting on source text: it verifies
  the code's shape, not its effect. fakebench instead *stores* the write so the
  test can assert on the resulting row.
- PyPI `frappe` — a 1.3 kB 0.0.1 stub, unrelated to the framework.

**Decision.** `ai_fr_hg/tests/fakebench.py` models Frappe's observable
semantics: documents and controllers, `get_all`/`get_list` with filters,
`or_filters`, ordering, paging and `pluck`, singles, permission hooks,
permitted-field policy, realtime and enqueue capture, commits and forced write
failures. It mimics Frappe's *forgiving* behaviour deliberately — unset fields
read as `None`, `frappe.db.delete` accepts a name string, `frappe.whitelist`
works bare — because strict-Python fidelity would fail code the real framework
accepts.

Fidelity is itself a liability, so each gap found is recorded rather than
patched silently. Four were found while re-auditing SEC-02 and RET-07, and
three of them would have made a *permission* test pass vacuously:

- `get_list` applied the row-permission hook after `pluck`/`fields`
  projection, handing the hook a bare string, so every row passed — the exact
  bypass SEC-02 describes, reproduced inside the harness.
- `or_filters` was swallowed by `**kwargs`, so folder-subtree scoping was
  tested against an unfiltered result set.
- `LIKE` ignored MariaDB's backslash escaping, so `\_` and `\%` behaved as
  wildcards.
- `get_meta` had no `.fields` and `frappe.model.get_permitted_fields` did not
  exist, so field-level policy could not run at all.

Later rounds of the re-audit found five more, each of which had silently
prevented an entire area from being tested:

- `frappe.cache()` was a stub answering `None`, so every path in
  `ai/limits.py` took its "backend unavailable" branch and admission control
  (GOV-01/02/03) could not run at all. Now backed by `fakeredis[lua]`, which
  executes the real Lua scripts; the suites skip rather than pass when it is
  absent.
- `frappe.get_roles` did not exist although `bench.roles` did, so any code
  branching on roles raised `AttributeError`.
- `frappe.clear_document_cache` did not exist, making the whole task module
  unreachable.
- Filter comparisons used raw Python operators, so a datetime filter against
  a string-stored column raised `TypeError` — the harness being *stricter*
  than MariaDB and rejecting valid code.
- `get_value` silently dropped `for_update`. It still ignores it, but now
  records `(doctype, for_update)` so a test can assert the claim path asked
  for the lock.

That last one is the shape of the boundary. A test can prove the lock was
requested, and prove the code handles losing the race; it cannot prove two
workers will not both win it.

**Background execution: what the split looks like in practice.** The
cancellation audit made the boundary unusually concrete, so it is worth
recording as a worked example rather than a rule.

Cancelling a pipeline run does two things: it commits `status = Cancelled`,
and it asks RQ to stop the job. The first is fully testable here — the
transition, the authority check, the audit record, the refusal of terminal
states. The second needs a live Redis and a live worker, and is not claimed.

That split is the application's own design, not a testing compromise: the code
treats `send_stop_job_command` as best-effort *because* committed state
prevents a queued job from starting and the cooperative checkpoints
(`_assert_not_cancelled`, `_is_cancelled`) stop a running one. Those
checkpoints are therefore load-bearing, which is why they now have tests and
mutations of their own — if Redis signalling silently stopped working, they
are the only thing left.

Stale-worker recovery sits on the same line. `reap_stale_in_flight_documents`
is fully testable in both failure directions (reaping a live worker, never
reaping a dead one); whether a worker *actually* died, and whether its lease
truly expired under contention, is runtime-tier.

**Non-claims.** fakebench proves nothing about SQL correctness, transactions,
InnoDB isolation levels, index behaviour, deadlocks, concurrency, `bench
migrate`, RQ job control, or browser behaviour. CHAT-09 is the standing illustration: the
sequence-allocation defect is a REPEATABLE READ interaction that only a real
database exhibits, and it is recorded as OPEN precisely because this tier
cannot reach it.

**Consequences.** A green fakebench suite is necessary and not sufficient. Any
row whose risk is database behaviour must stay open until the runtime tier
exists, however complete its logic looks here.

---

## ADR-017 — A mutation gate, because a test that cannot fail is not evidence

**Date.** 2026-08-21 (Part 2 §28, §29).

**Context.** Rewriting tautological tests raised an obvious question: how do we
know the *replacements* are any better? Counting tests measures nothing —
that metric is what produced 29 tautologies. The only direct evidence that a
suite verifies behaviour is that it fails when the behaviour breaks.

**Frappe V17 capabilities evaluated.** None applies; this is a CI concern, not
a framework one. `mutmut` and `cosmic-ray` were considered and rejected: both
mutate indiscriminately, producing mostly-equivalent mutants and long runtimes,
and neither expresses *which product guarantee* a mutation is meant to break.

**Decision.** `scripts/mutation_check.py` holds 44 hand-written mutations, each
naming the guarantee it violates ("the original SEC-02 bug: the aggregate
counts rows the caller cannot list"). Each is applied to real source, the
offline suite is rerun, and the source is restored in a `finally`. The script
exits non-zero if any mutation survives *or* is stale — a stale anchor means
the source moved and the mutation silently verifies nothing, which is how a
gate rots.

Mutations earn their place by having caught something. Three findings came out
of the campaign directly: VER-03 (a real archive bug, found because a mutation
survived and investigation showed the guard was harmful), the missing
`send()` facade assertion, and the untested `Password` fieldtype rule — where
the mutation survived because the only Password field was *also* named
`api_key` and caught by the substring policy, so a `vault_pin` field was added
to isolate the type rule.

**Consequences.** Adding a test is not enough; a corresponding mutation must
fail without it. The gate takes roughly four minutes and is not yet mandatory
in CI — the GitHub App cannot write `.github/workflows/`, so
`docs/phase-reports/APPLY_MUTATION_GATE.md` carries the YAML for a maintainer,
and VER-02 stays open on that wiring. Until then the campaign is run locally
before every commit that touches verification.

---

## ADR-018 — Validate DocTypes against Frappe's own source, not against our reading of it

**Date.** 2026-08-21 (Part 2 §28; Frappe V17 primacy).

**Context.** A whole class of failure lands at `bench migrate` or on first
DocType save and is invisible to any amount of application testing: a fieldname
that collides with a framework column, a `Link` whose target does not exist, a
field missing from `field_order`, a hidden+mandatory field with no default.
fakebench cannot see these — it never reads DocType JSON as the framework does.

**Frappe V17 capabilities evaluated.**

- `bench migrate` — the authority, and unavailable here.
- `frappe.model.meta` / `DocType.validate_fields` — the actual rules. Since
  HTTPS egress works, `frappe/develop` clones cleanly even though `apt` does
  not, so the real source is available to read.
- Reimplementing the rules from documentation — rejected. Transcribed rules
  drift from the framework, and a validator that is wrong in the same direction
  as the code proves nothing.

**Decision.** `test_doctype_schema_against_frappe.py` validates all 49 DocTypes
against rules taken from `core/doctype/doctype/doctype.py`, `database/schema.py`
and `model/__init__.py`. Critically, `TestFrappeSourceAlignment` compares the
transcribed constants (`data_fieldtypes`, `no_value_fields`, `default_fields`,
`SPECIAL_CHAR_PATTERN`) against a live checkout when `FRAPPE_SOURCE` points at
one, so the copies cannot silently diverge from the framework. Without the
checkout those two cross-checks skip and the 865 schema subtests still run.

It found two real defects immediately. Six fields were absent from
`field_order` and therefore unrenderable in Desk — including the operator
controls behind SEC-03 and SEC-07, both marked CLOSED at the time, and TRN-04's
Stop control. And `AI Document.organization_name_key` was `hidden` + `reqd`
with no default, which Frappe rejects with
`HiddenAndMandatoryWithoutDefaultError` outside `in_migrate`.

**Consequences.** Schema conformance is now checked every run, and the
`frappe/develop` clone is worth wiring into CI (the YAML in
`APPLY_MUTATION_GATE.md` includes it). This tier still cannot prove a migration
succeeds — only that the DocTypes satisfy the rules the framework applies. A
real `bench migrate` remains required for that.

---

## ADR-019 — Capability is not delivered until something can reach it

**Date.** 2026-08-21 (Part 2 §21, §29).

**Context.** VER-05 showed a field that existed, was validated, was covered by
tests and was documented — and could not be seen in the UI, because Desk
renders only what `field_order` lists. Generalising that question to the RPC
surface found more of the same: a "Preview Prompt" button that faked its
server call in the browser, two domain functions published as a second
unvalidated route, and eleven endpoints left published with no caller.

The pattern is that each layer was correct in isolation and nothing checked
the joins. Every one of these passed CI.

**Frappe V17 capabilities evaluated.**

- `frappe.whitelist` and `frm.call` — resolve their targets as strings at call
  time, by design. Nothing fails at install; a typo becomes a 404 the user
  finds, or a silent failure inside a worker.
- Frappe's own hook resolution — equally lazy, for the same reason.
- Desk form rendering — `field_order` is authoritative; a field absent from it
  does not exist as far as the operator is concerned.

**Decision.** Reachability is asserted structurally, in both directions.
`test_rpc_contract_reachability.py` resolves every `ai_fr_hg.*` method string
across all 60 JS files — including `frm.call` bare names, helper indirection,
and `${PREFIX}.${method}` composition — against the whitelist, and inversely
requires every whitelisted method to have a caller, a hook, a JSON action, a
documented contract in `API.md`, or an explicit justified allow-list entry.
`test_hook_targets_resolve.py` does the same for `hooks.py`, parsing it as
Python so commented boilerplate is excluded by construction. Editable and
settings fields must appear in `field_order`.

An endpoint published without a caller is not waved through: it must still
prove it scopes to the session user and refuses a hardcoded identity, because
no UI test can demonstrate the safety of something no UI exercises.

**Consequences.** "The backend exists" no longer closes a row. SEC-03, SEC-07
and TRN-04 were reopened on exactly this basis — implementation present,
acceptance criteria not met — and FILE-08 stays OPEN pending a real
disposition (build the UI or delete the endpoints) rather than being deferred.
Browser-level confirmation that a rendered control works is still Phase 7; this
tier proves only that the wiring exists.
