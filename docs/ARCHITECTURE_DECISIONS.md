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
