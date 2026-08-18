# AI Document Tree

This document describes the native mixed `AI Document` Tree View implemented in AI Fr Hg. It is the operational contract for organization, permissions, identity, copy/move/delete behavior, pagination, concurrency, and migration.

## Status

As of **2026-08-18**, the implementation is present in the application and covered by focused pure-Python tests plus Bench/Frappe integration tests in the repository. The pure suites can run without a Bench site; the integration suite, browser automation, asset compilation, and MariaDB/PostgreSQL verification must be run in a Frappe v17 Bench before a production release. See [Validation status](#validation-status).

## Native Frappe architecture

The tree is registered as the Tree representation of `AI Document`:

```text
AI Document Tree View
  -> ai_fr_hg.api.document_tree (whitelisted facade)
    -> ai_fr_hg.ai.document_tree (authorization and transaction service)
      -> AI Document + Frappe File + existing processing/index services
```

- `hooks.py` registers `doctype_tree_js` for `AI Document`.
- `public/js/ai_document_tree.js` defines `frappe.treeview_settings["AI Document"]` and uses Frappe's native Tree View lifecycle.
- `api/document_tree.py` only validates/normalizes RPC payloads and delegates.
- `ai/document_tree.py` owns all business rules, permission checks, locking, stale-state validation, audit, and mutations.
- Native `File` folders are the canonical hierarchy. No browser-only hierarchy, second folder DocType, or duplicate document store exists.
- `AI Document` remains a normal processing record rather than being converted into a folder/Nested Set record.
- Frappe maintains native `File.folder` relationships and folder path identities. The app never writes `lft`/`rgt` or maintains a parallel Nested Set.

The visible root is **AI Documents** and maps to canonical `File` folder `Home`. Both folders and AI Documents may be direct root children.

## Canonical data model

### Folders

Folders are native `File` rows with `is_folder = 1`:

- `name` is the canonical path identity, for example `Home/Policies/2026`;
- `folder` is the direct parent;
- `file_name` is the display name;
- `Home` is the root.

Optional metadata remains in the existing `AI Folder Settings` DocType. Recursive copy reads this metadata through a Frappe permission-aware list query only after every source folder has passed explicit read authorization. Missing or inaccessible settings are not inferred or exposed.

### Documents

`AI Document` has these organization fields:

| Field | Meaning |
|---|---|
| `folder` | Canonical parent `File` folder; `Home` means root. Indexed. |
| `source_folder` | Denormalized location provenance kept synchronized with `folder`. |
| `organization_name` | Display identity within the parent folder. |
| `organization_name_key` | SHA-256 of NFC-normalized, case-folded UTF-8 display name. |
| `organization_revision` | Monotonic stale-state revision for organization changes. |
| `source_file_record` | Stable Link to the authoritative physical `File` row. |
| `source_file` | Existing attachment URL; content location, not stable record identity. |
| `copied_from`, `copied_on` | Copy provenance for a newly created document identity. |

The migration creates/backfills these fields and enforces a unique location identity on `(folder, organization_name_key)`. The digest makes collision behavior independent of database collation and safely bounds the unique index width.

## Identity and duplicate rules

Three concepts are deliberately separate:

1. **Document identity** — immutable `AI Document.name`.
2. **Organization identity** — normalized display name scoped to one parent folder.
3. **Content identity** — bytes, hash, or URL, which may legitimately be shared.

Consequences:

- Moving a document retains its one `AI Document` identity.
- Copying creates a new `AI Document` identity and, for a file-backed source, a new `File` identity.
- Equal content hashes or URLs do not merge document records.
- The same display name is allowed in different folders.
- Case/Unicode-equivalent names cannot coexist in the same folder.
- Explicit rename/copy names that collide are rejected.
- An omitted copy name is resolved deterministically as `Name (Copy)`, `Name (Copy 2)`, and so on while preserving an extension.
- No move or copy overwrites an existing document or physical File.

`source_file_record` is authoritative. URL-only legacy resolution succeeds only for one exact attachment to the AI Document or one globally unique URL. Duplicate candidates fail closed; the migration and runtime never choose an arbitrary oldest File.

## Lazy retrieval and search

`get_children` returns one native Tree View page. It never materializes the complete repository.

- Default page size: `100`.
- Accepted page size: minimum `10`, maximum `250`.
- Folders are ordered first by display name, followed by documents ordered by organization name.
- A server-generated `Load more…` node carries an opaque, parent-bound continuation token.
- Continuation tokens are validated before use and cannot be moved to another parent or search mode.
- Ordinary expansion reads direct children only.
- Knowledge Base filtering is applied server-side.
- Search treats `%`, `_`, and backslash literally rather than as SQL wildcards.
- Global document search intersects permission-visible AI Documents with permission-visible parent folders.
- Global search uses a keyset cursor, scans at most 1,000 permission-filtered document candidates per request, and emits continuation only after finding a visible look-ahead row. It truncates fail-closed instead of exposing hidden-row density.

The UI intentionally offers **Expand Loaded** rather than recursively expanding an unbounded repository. Collapse, refresh, search/filter, breadcrumbs, and navigation use the native tree instance.

## Node values and payloads

Mixed node values are server-controlled:

- folder: canonical `File.name`, such as `Home/Policies`;
- document: `document::<AI Document.name>`;
- root: `AI Documents`;
- page: an opaque `__ai_document_page__:` token.

Nodes include server-derived capabilities (`can_read`, `can_write`, `can_copy`, `can_delete`, and creation capabilities). These capabilities control presentation only. Every API operation repeats authoritative permission checks.

## Operations

### Create and open

- Add Folder inserts a native `File` folder after checking destination write and File create permission.
- Add AI Document routes to the ordinary AI Document form with canonical folder defaults.
- Open routes folders to the native File form and documents to the ordinary AI Document form.

### Rename

Document rename changes `title`/`organization_name`, preserves document identity, checks the scoped collision key, increments the organization revision, and records audit.

Folder rename delegates to the canonical File-folder service. All folders, Files, and AI Documents in the subtree are discovered, authorized, deterministically locked, and revalidated before path changes. Denormalized document folder provenance is synchronized after the framework updates folder links.

### Move

A move requires write authority over the object, its current parent/source, and the destination. Folder moves reject root, self, descendant, circular, missing, and colliding destinations.

A document move retains `AI Document.name`. A request whose destination is already the current folder is a permission- and stale-checked no-op; it does not resolve or mutate shared-source ownership. A uniquely owned physical File moves with the document. If another stable `source_file_record` shares the physical source, the original File remains for those identities and a new File identity is created in the destination for the moving document. Native attachment ownership transfers to the deterministic locked stable owner only when the original File is attached to the moving identity; only that real ownership change requires write permission on the replacement owner. A remaining URL-only legacy reference cannot safely receive ownership and fails closed until backfilled. No processing derivative is duplicated merely because a document moved.

A recursive folder move preserves all descendants. Synchronous operations are one savepoint-backed transaction. Large operations use a queued snapshot and re-check the initiating user and subtree fingerprint before any worker mutation.

### Copy

A document copy:

- leaves the source unchanged;
- creates a new `AI Document.name` owned by the initiating user;
- records `copied_from` and `copied_on`;
- copies source configuration and tags through the existing DocType copy behavior;
- creates an independent native `File` row for a file-backed source while allowing Frappe's byte storage deduplication;
- preserves source File privacy;
- resets status to `Draft`;
- clears summary, extraction, counts, processing jobs/errors, retry state, indexed timestamp, and other processing derivatives;
- does not clone chunks, embeddings, shares, unrelated attachments, or source timestamps;
- suppresses automatic ingestion only inside an unforgeable process-local service context while the atomic copy is assembled.

Recursive folder copy preflights every folder, document, and physical source File before creating anything. It recreates the folder structure, copies only settings readable by the caller, and copies each document under its mapped destination. Explicit names collide by rejection; default names use deterministic copy suffixes.

### Delete

Document deletion delegates to normal Frappe deletion so existing DocType lifecycle, retention, and link validation remain authoritative. The service locks and revalidates the document's current folder, source File, and attachment membership first. Before native attachment cleanup, the controller transfers a shared source attachment only to a locked, write-authorized AI Document with the same stable `source_file_record`; this preserves the File while allowing the selected identity to be deleted. A URL-only possible owner fails closed until its exact File identity is backfilled.

Folder deletion:

- rejects deletion of `Home`;
- requires explicit recursive confirmation for non-empty folders;
- discovers and authorizes the complete subtree without leaking a denied descendant;
- rejects unmanaged Files that the mixed tree does not represent;
- rejects source Files still referenced by an AI Document outside the subtree;
- deletes AI Documents with normal lifecycle hooks before deleting their unlocked attachment owners;
- deletes Files and folders through normal Frappe operations;
- recalculates affected Knowledge Base statistics;
- rolls back all database and audit changes if any step fails.

### Bulk operations

Bulk move and bulk delete accept at most 500 node IDs. Duplicate and nested selections are normalized only after every explicitly selected node has been authorized. The service preflights the complete affected set, locks in deterministic order, re-discovers late descendants, checks a state fingerprint, and then applies the operation atomically.

The default background threshold is 100 affected folder/document/File rows. A queued result contains `status = "Queued"` and `job_id`; a synchronous result contains `status = "Completed"`. Background jobs run on the long queue with a two-hour timeout.

## Permissions and non-disclosure

The tree never treats JavaScript state as authority.

- Frappe DocType roles, user permissions, shares, File permissions, and Knowledge Base grants remain authoritative.
- List/search retrieval uses `frappe.get_list` and then enforces the mixed condition that a visible AI Document must also have a visible parent folder.
- Recursive operations use internal discovery so a hidden descendant cannot be skipped, then explicitly authorize every affected File/folder/document. Denial aborts without identifying the hidden row.
- Source and destination permissions are checked separately.
- Background workers restore the initiating user and re-run permission checks; scheduler/Administrator authority is not inherited.
- Direct File uploads and moves check and synchronize their linked AI Documents in the same transaction.
- Capability flags only hide impossible UI actions; the server rejects forged calls.
- Audit writes are fail-closed and share the operation transaction.

## Transactions, locks, and stale state

Public mutations do not commit. Each operation creates a named savepoint, rolls back to it on every exception (including PostgreSQL's aborted-transaction behavior), and lets the Frappe request transaction commit normally.

The canonical lock order is:

1. parent/destination folder `File` rows;
2. affected folder/source `File` rows in sorted batches;
3. affected `AI Document` rows in sorted batches.

Direct File hooks follow the same parent-before-File-before-document order. Single-row locks use Frappe's `get_value(..., for_update=True)` and bounded bulk locks use Query Builder `for_update()`, preserving one query per batch with database-native quoting on MariaDB and PostgreSQL rather than degrading to per-row N+1 locking. Operations re-query and re-authorize after locks are acquired. Client `expected_modified` values and queued subtree fingerprints convert stale actions into `TimestampMismatchError` instead of silently applying them to changed state.

Only background worker entry points commit after the complete service call succeeds. Framework job failure handling rolls back exceptions.

## Audit and provenance

All organization mutations write existing AI Audit Log records. Queue and completion events are distinct. Audit details include stable identities, source/destination, copy provenance, affected counts, derivative/share behavior, and job IDs where relevant.

The tree does not create a second audit system. Deleted dynamic-link targets are retained as immutable detail values rather than broken links.

## Direct File lifecycle integration

Existing File hooks preserve consistency outside the Tree View:

- inserts resolve and authorize a canonical parent before insertion;
- an AI Document attachment records both `source_file` and stable `source_file_record`;
- direct File moves lock parents and synchronize authorized AI Document folder provenance in the same transaction;
- stable links are preferred over URLs;
- globally unique legacy URLs may be backfilled for every matching legacy document;
- with duplicate File URLs, only a singleton exact AI Document attachment may be claimed;
- ambiguity is left unresolved rather than guessed;
- direct deletion remains subject to Frappe link validation and existing retention behavior.

## Migration and rollout

Patch `v0_0_9_ai_document_tree_organization`:

1. adds organization/source/copy fields when absent;
2. creates/backfills `Home` placement and normalized identity keys in pages;
3. resolves stable File identities only when unambiguous;
4. caps candidate tracking at two rows per key so pathological duplicate URLs do not grow memory;
5. repairs deterministic same-folder display collisions;
6. installs indexed location fields and the scoped unique constraint using Frappe database APIs.

The patch is retry-safe. Run it through ordinary `bench migrate`; do not run it concurrently from multiple sites or manually edit organization fields around migration.

After migration:

1. verify long-queue workers are running;
2. rebuild assets so the tree JavaScript/SCSS bundle is available;
3. verify AI Manager and AI User role assignments;
4. inspect unresolved legacy rows where `source_type = "File"` and `source_file_record` is empty;
5. resolve ambiguous source identity by attaching/selecting the correct exact File record, not by URL guessing;
6. execute the validation matrix below on the deployment's database engines.

## Public API summary

The facade is `ai_fr_hg.api.document_tree`:

| Method | Purpose |
|---|---|
| `get_children` | Root discovery, lazy children, global search, filtering, continuation. |
| `create_folder` | Create a native folder under a parent. |
| `rename_node` | Rename a mixed folder/document node. |
| `move_node` | Move one mixed node. |
| `copy_node` | Copy one document or recursive folder subtree. |
| `delete_node` | Delete a node, optionally confirming recursive folder deletion. |
| `bulk_move_nodes` | Atomically move up to 500 selected nodes. |
| `bulk_delete_nodes` | Atomically delete up to 500 selected nodes. |

These methods are not an alternate document API. Callers should continue using existing AI Document processing, search, indexing, reading, and retrieval APIs after organization changes.

## Extension rules

New tree behavior must preserve the boundary:

```text
Tree UI -> whitelisted facade -> document_tree service -> Frappe DocTypes/services
```

Do not:

- write folder/document relationships from client JavaScript;
- call private service bypasses from a whitelisted endpoint;
- parse display labels to infer stable identities;
- treat URLs or hashes as unique File identities;
- add commits inside request-path services;
- skip hidden descendants in recursive mutation;
- add a second folder, attachment, retrieval, indexing, or audit store;
- recursively fetch the entire tree to implement UI convenience actions.

When adding an operation, include permission, rollback, stale-state, collision, late-descendant, audit, and processing/index regression tests.

## Validation status

Repository coverage includes:

- pure folder/provenance/permission tests in `ai_fr_hg/tests/test_folder_units.py`;
- pure tree identity, pagination, search escaping, migration ambiguity, stale-state, and bulk tests in `ai_fr_hg/tests/test_document_tree_units.py`;
- Frappe integration and lifecycle tests in `ai_fr_hg/ai_knowledge/doctype/ai_document/test_ai_document_tree.py`;
- UI source in `ai_fr_hg/public/js/ai_document_tree.js` registered through `hooks.py`.

Latest validation in the implementation environment:

- folder pure suite: **23 passed**;
- document-tree pure suite: **28 passed**;
- targeted Python compilation: passed.

Not available in that shell and therefore still required before production release:

- live Frappe v17/Bench integration suite;
- MariaDB and PostgreSQL migration/transaction runs;
- browser/UI automation for menus, filters, selection, refresh, and routing;
- JavaScript lint/build and SCSS bundle compilation;
- full existing reader, processing, indexing, retrieval, and search regression suite;
- load/concurrency testing with production-scale subtrees and workers.

Treat this as an implemented feature awaiting environment-specific release verification, not as evidence that those unavailable checks passed.
