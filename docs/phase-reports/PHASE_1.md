# Phase 1 — Isolation, Permission, and API Safety

**Objective:** close security and data-isolation weaknesses before retrieval or feature work.

**Opened:** 2026-08-20
**Phase owner:** Security + Platform
**Status:** COMPLETE — Server, Linter, Frontend static, and Dependency audit all green on the pinned Frappe v17 bench (final code SHA `dcd5c12`)

## Phase inventory

| ID | Finding | Current State | Required State | Files | Tests | Migration | Frontend | Status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| SEC-01 | Translation memory unscoped | Lookup ran without KB | No scope ⇒ no lookup; document/tool pass authorized KB | `ai/translation.py`, `ai/tools/builtin.py`, `api/translation.py` | Integration isolation suite | None | None | COMPLETE |
| TRN-01 | Memory identity ignores policy | Fingerprint was language pair only | KB + glossary/tone/domain policy identity | `ai/translation_utils.py`, persist path | Unit + integration | None (new fingerprints only) | N/A | COMPLETE |
| SEC-02 | Generic count bypasses row ACL | `frappe.db.count` | Central permission-aware query/count | `ai/tools/query.py`, `ai/tools/builtin.py`, `ai/tools/__init__.py` | Row-permission parity tests | None | N/A | COMPLETE |
| SEC-03 | Generic tools leak fields | `as_dict` / arbitrary fields | Field-level + sensitive deny centrally | `ai/tools/query.py`, DocType schema (AI Tool, Platform Settings) | Field/sensitive exfiltration tests | None | Allowlist control | COMPLETE |
| SEC-04 | Provider networking | `requests` + env proxies | Guarded transport: pinned dial, no proxies, no redirects, peer revalidation | `utils/netguard.py`, `utils/network.py`, `ai/providers/base.py` | 17 runtime tests vs real loopback HTTP/TLS | None | N/A | COMPLETE |
| SEC-06 | Folder settings list too broad | Empty query for users | List/get parity through native File permission hooks | `utils/permissions.py` | Role parity tests | None | N/A | COMPLETE |
| SEC-07 | Search telemetry unredacted | Raw query/results stored | Canonical redaction, bounded snippets, policy control | `ai/knowledge.py`, Platform Settings schema | Redaction/disable/retention tests | None | Setting | COMPLETE |
| FILE-01 | Native move fallback | Catch-all paste fallback | Fail closed for AI files | `public/js/file_list.js` | Code-path regression | None | Desk paste | COMPLETE |
| FILE-02 | URL-only File resolve | Ambiguous `file_url` | Canonical resolver; require stable identity | `ai/folders.py`, `ai/ingestion.py`, `api/folders.py` | Identity/ambiguity tests | None | Uploaders | COMPLETE |
| FILE-03 | My Uploads shared | Shared Home path | Renamed "Shared Uploads" truthfully | `ai/folders.py`, `install.py` | Default-folder tests | `v0_0_15` rename | Folder UI | COMPLETE — SCOPED |
| FILE-04 | Storage folder shape | Data default | File Link + validation + write-access gate | Platform Settings schema/controller/js, `ai/folders.py` | Validation/default tests | `v0_0_15` normalize | Settings | COMPLETE |
| FILE-05 | Folder picker shallow | Depth-6 eager fetch | Native lazy Link picker | `public/js/file_folder.js` | Contract | None | Picker | COMPLETE |
| FILE-06 | Shared with me = public | `is_private=0` | Renamed "Public" | `ai/folders.py` | Label contract test | None | Tabs | COMPLETE — SCOPED |
| FILE-07 | Desk monkey patches | Global replacements | Version-gated; core-bundle diagnostic | `desk_guard.js`, `file.js`, `file_list.js`, `file_folder.js` | Static | None | Desk | COMPLETE (browser smoke → Phase 7) |
| SEC-05 | Encryption | Hidden in Phase 0 | Hidden; regression exists | Phase 0 artifacts | Phase 0 contracts | Done | Hidden | CLOSED — REMOVED |

## Contracts (summary)

### SEC-01 / TRN-01 — translation memory

- **Inputs:** source text, language pair, optional `knowledge_base`, glossary, tone, domain.
- **Behavior:** reuse only when the fingerprint *and* the parent translation share the authorized KB and policy identity (KB + glossary + tone + domain). No scope ⇒ no lookup, never "all memory".
- **Enforcement paths:** `authorized_memory_scope` gates the inline API (throws `PermissionError` on an unauthorized KB), document translation passes `doc.knowledge_base`, the `translate_content` tool passes the resolved document's KB (new), and `_memory_lookup` re-checks access. `_persist_outcome` writes policy-aware fingerprints for new rows; legacy fingerprints simply miss (safe).
- **Tests:** no-scope no-lookup; cross-KB isolation; policy-change non-reuse; tool uses only the source document's KB; positive same-KB tool reuse; inline API rejection of an unauthorized KB; cross-user isolation via a private KB; worker authority restoration.

### SEC-02 / SEC-03 — generic document tools

- **One authority:** `ai_fr_hg.ai.tools.query` — used by `get_document`, `list_documents`, `count_documents`, and the configurable DocType Query/Action tools.
- **Enforcement:** DocType read rule; row-level permission query conditions and `has_permission` hooks via `frappe.get_list`/`get_doc` (counting runs through listing, never `frappe.db.count`); field-level projection via `frappe.model.get_permitted_fields` (permlevel-aware); deny layer for Password field types, credential-named fields, and the operator-configured `tool_sensitive_fields` list; filters on unreadable/denied fields are dropped, never probed; hard caps on rows (100), fields (25), filter keys/values, and the exact-count scan (5000, flagged as bounded beyond).
- **Write side:** DocType Action tools strip model-supplied values to writable, non-denied fields before `doc.update`.

### SEC-04 — provider transport

- **Guarantees per call:** `trust_env=False` (environment proxies ignored); one validated DNS resolution (all addresses must be private unless the host is explicitly allowlisted; `.local`/`.internal` are hints, not trust; documentation ranges are not "private"); pinned dial to the validated address while preserving Host/SNI (DNS rebinding cannot move an approved connection); redirects refused; established peer socket re-validated before the response is trusted.
- **Evidence:** 17 runtime tests against a real loopback HTTP and TLS server in `test_netguard_units.py`.

### SEC-06 — folder settings parity

- The list query composes the same permission query conditions Frappe itself registers for `File` (via the `permission_query_conditions` hooks) inside a subquery on the settings row's linked folder, so list and direct-document access agree for every role.

### SEC-07 — search telemetry

- Query text and bounded result snippets (≤200 chars) pass the canonical `redact()` patterns before persistence; full chunk content is never stored; gated by the new `log_search_queries` setting; retention remains Frappe-native (`default_log_clearing_doctypes`, 30 days).

### FILE-01/02 — canonical File authority

- The Desk paste override fails closed on canonical service failure (no native fallback). File identity resolves through one canonical resolver (`folders.resolve_file_identity`): stable `file_record` first, exact document attachment backfill, URL-only ambiguous requests fail closed. Ingestion and the upload facade share it.

### FILE-03/04/05/06 — truthful folder UX

- "Shared Uploads" naming (defaults, seeding, migration rename preserving data); `storage_folder` File Link validated server-side and applied only with write access; native lazy Link picker replaces the eager depth-6 tree; "Public" tab label matches its `is_private=0` query.

### FILE-07 — Desk patch safety

- Every global patch (desk guard, File list, File form, uploader) is version-gated to Frappe v17 and refuses to run elsewhere; missing core Desk bundles show a rebuild diagnostic instead of silent suppression.

### API validation (plan §1.5)

- `ai_fr_hg.utils.api_validation` centralizes text/identifier/enum/list/pagination/payload/idempotency bounds and is applied to the chat, ask, search, chunks/entities, folders, translation, admin, learning, and document-tree endpoints with the published caps.

## Frappe v17-native integration

- Row/field permission via native hooks: `permission_query_conditions`, `has_permission`, `frappe.get_list`/`get_doc`, `frappe.model.get_permitted_fields`.
- `get_list` v17 `limit`/`offset` pagination; `default_log_clearing_doctypes` retention; DocType JSON metadata for new settings (Link field, defaults, descriptions).
- Native File permission hooks composed for folder-settings parity; File Link picker for folders.
- Idempotent Frappe patch `v0_0_15_folder_truthfulness` for upgrade normalization.
- Pinned Frappe v17 revision (`d7000da3...`, `17.0.0-dev`) verified against the checked-out upstream source while building the transport guard and permission composition.

## Security review

- Cross-KB/cross-user translation memory paths are closed and tested.
- Generic tools cannot reveal rows, counts, or fields outside the caller's authority.
- Provider traffic cannot leave via proxies, rebinding, or redirects; peer identity is re-checked.
- Folder settings list parity, canonical File mutation ownership, stable File identity, and redacted telemetry hold server-side independent of the UI.

## Data/migration

- Patch `v0_0_15_folder_truthfulness`: renames legacy `Home/My Uploads` → `Home/Shared Uploads` through the canonical service (descendant paths updated by the service, data preserved), normalizes legacy storage-folder values to the canonical `Home/AI Platform` File identity, creating it when missing. Idempotent by construction.
- No destructive changes; translation memory fingerprints are additive for new rows only.

## Tests

Executed in this workspace (no MariaDB/bench available locally, as in Phase 0):

- `python3 -m unittest ai_fr_hg.tests.test_netguard_units ai_fr_hg.tests.test_translation_units ai_fr_hg.tests.test_folder_units ai_fr_hg.tests.test_phase_0_contracts ai_fr_hg.tests.test_document_tree_units` — **PASS, 114 tests** (netguard 17, translation 48, folder 16, phase-0 13, document tree 20).
- API validation unit tests — **PASS, 8/8** (run via a local frappe stub; bench-native module committed).
- `python3 -m ruff check` (pinned v0.14.10) — **PASS**; `ruff format --check` — **clean**.
- `python3 -m compileall ai_fr_hg` — **PASS**; `node --check` over all 52 production JS files — **PASS**; repository JSON parse — **PASS**.
- New bench integration suites committed for hosted execution: `test_tool_query_security.py` (SEC-02/03), `test_ai_search_query.py` (SEC-07), `test_ai_folder_settings.py` additions (SEC-06/FILE-02), `test_ai_platform_settings.py` additions (FILE-04), `test_ai_translation.py` additions (SEC-01/TRN-01).

## Runtime verification

Hosted CI on the pinned Frappe v17 bench (`17.0.0-dev` at `d7000da3...`) is green on the final code SHA (`dcd5c12`): **Server** (real bench: install-app → migrate → `bench build --app ai_fr_hg` → full `run-tests --app ai_fr_hg`), **Linter** (pre-commit suite + pinned Frappe Semgrep ruleset), **Frontend static**, and **Dependency audit** all pass.

CI-driven debugging this phase fixed, with bench evidence: (1) `get_document` without a field list raised when a DocType exposed more than the 25-field budget (AI Provider) — now returns a bounded deterministic prefix plus `_fields_truncated`; (2) the legacy `storage_folder` default `AI Platform` (a bare name) flowed into the Single DocType on first save during `after_install`, before any folder existed, so the new server-side validation aborted `install-app` — the File Link now has no short default and patch `v0_0_15` normalizes legacy rows; (3) Semgrep: 3 findings in the new provider/permission code, fixed (`str(exc)` formatting, reviewed nosemgrep annotation, type-safe `get_single_value`); (4) bench test fixtures aligned with the real runtime (status enums, native File URL normalization, real KB rows); (5) Prettier v2.7.1 and ESLint v8.44 (`--quiet`) pass on all JS.

Browser/Desk smoke for the gated patches and picker remains the Phase 7 browser matrix, as planned.

## Remaining issues

1. FILE-07 exact-v17 browser smoke and the Desk picker/browser workflows are deliberately Phase 7 deliverables.
2. Branch protection on `main` remains an owner-only GitHub administration action (recorded in Phase 0).

## Phase verdict

`PASS WITH DOCUMENTED NON-BLOCKING LIMITATION` — all four required CI checks are green on the real Frappe v17 bench; the only deferred items are browser E2E (scoped to Phase 7) and repository-owner branch protection on `main`.
