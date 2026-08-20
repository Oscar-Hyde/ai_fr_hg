# Phase 1 — Isolation, Permission, and API Safety

**Objective:** close security and data-isolation weaknesses before retrieval or feature work.

**Opened:** 2026-08-20
**Status:** OPEN

Phase 0 hosted Server/Linter/Frontend static/Dependency audit are green. Branch protection on `main` remains an owner-only GitHub administration action (HTTP 403 for this integration) and is recorded as a documented operational limitation. Product isolation work proceeds with SEC-01.

## Phase inventory

| ID | Finding | Current State | Required State | Files | Tests | Migration | Frontend | Status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| SEC-01 | Translation memory unscoped | Lookup ran without KB | No scope ⇒ no lookup; document/tool pass authorized KB | `ai/translation.py`, `api/translation.py`, `ai/tools/builtin.py` | Unit + integration isolation | None | No new controls | IN PROGRESS |
| TRN-01 | Memory identity ignores policy | Fingerprint is language pair only | Include KB + glossary/tone/domain | `ai/translation_utils.py`, persist path | Unit + integration | None (new fingerprints only) | N/A | IN PROGRESS |
| SEC-02 | Generic count bypasses row ACL | `frappe.db.count` | Central permission-aware query | TBD | TBD | None | N/A | OPEN |
| SEC-03 | Generic tools leak fields | `as_dict` / arbitrary fields | Field-level + sensitive deny | TBD | TBD | None | N/A | OPEN |
| SEC-04 | Provider networking | `requests` + env proxies | Session `trust_env=False`, DNS/redirect/allowlist | TBD | TBD | None | N/A | OPEN |
| SEC-06 | Folder settings list too broad | Empty query for users | List/get parity | TBD | TBD | None | N/A | OPEN |
| SEC-07 | Search telemetry unredacted | Raw query/results stored | Canonical redaction | TBD | TBD | None | N/A | OPEN |
| FILE-01 | Native move fallback | Catch-all paste fallback | Fail closed for AI files | TBD | TBD | None | Desk patch | OPEN |
| FILE-02 | URL-only File resolve | Ambiguous `file_url` | Require File name | TBD | TBD | None | Uploaders | OPEN |
| FILE-03 | My Uploads shared | Shared Home path | Per-user or rename | TBD | TBD | Possible | Folder UI | OPEN |
| FILE-04 | Storage folder shape | Data default | File Link + default | TBD | TBD | Possible | Settings | OPEN |
| FILE-05 | Folder picker shallow | Depth-6 eager fetch | Lazy tree | TBD | TBD | None | Picker | OPEN |
| FILE-06 | Shared with me = public | `is_private=0` | Share or rename | TBD | TBD | None | Tabs | OPEN |
| FILE-07 | Desk monkey patches | Global replacements | Version-gate / hooks | TBD | TBD | None | Desk | OPEN |
| SEC-05 | Encryption | Hidden in Phase 0 | Keep hidden; regression exists | Phase 0 | Phase 0 contracts | Done | Hidden | CLOSED — REMOVED |

## SEC-01 / TRN-01 contract

- **Inputs:** source text, language pair, optional authorized `knowledge_base`, glossary, tone, domain.
- **Outputs:** reused translations only when fingerprint and parent translation share that authorized KB and policy.
- **Permissions:** memory lookup requires `_knowledge_base_access(kb, user, write=False)`. Managers retain existing KB access. Unauthorized or empty scope returns `{}`.
- **Failure:** inline API with a KB the user cannot read throws `PermissionError`. Tool raw text never accepts a model-supplied foreign KB.
- **Concurrency/idempotency:** lookup is read-only; persist writes policy-aware fingerprints for new rows only. Legacy fingerprints simply miss until retranslated (safe, no cross-KB hit).
- **Jobs:** document translation already passes `doc.knowledge_base` into `translate_text`.
- **Frontend:** no new UI; existing translate actions inherit backend enforcement.
- **Performance:** parent list still capped at 500 recent translations in the authorized KB.

## Frappe v17 evaluation

Evaluated `frappe.get_list` / DocType permissions / `has_permission` for memory rows. Segment child rows are not independently permissioned; scoping via parent `AI Translation.knowledge_base` plus `_knowledge_base_access` is required because Frappe has no child-table ACL for this case. No second memory store was introduced.
