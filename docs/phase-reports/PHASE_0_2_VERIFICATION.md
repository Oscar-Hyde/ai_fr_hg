# Phases 0–2 closed-phase verification

**Date:** 2026-08-20
**Branch:** `arena/01a01de6-ai-fr-hg`
**Head at inspection:** `a798663`
**Purpose:** re-inspect Phases 0, 1, and 2 even though their gap-register rows were already closed. A closed row is not permission to weaken the audit.

## Verdicts after verification

| Phase | Original verdict | After verification |
| --- | --- | --- |
| 0 — Truthful baseline and quality gate | `FAIL` (branch protection only) | **Unchanged `FAIL`.** OPS-01 still BLOCKED: `main.protected = false`, GitHub App HTTP 403. The four required checks execute and pass. |
| 1 — Isolation, permission, and API safety | `PASS WITH DOCUMENTED NON-BLOCKING LIMITATION` | **Confirmed.** FILE-07 browser smoke remains Phase 7. |
| 2 — Retrieval correctness and scale | `PASS WITH DOCUMENTED NON-BLOCKING LIMITATION` | **Confirmed after maintenance.** Hosted Server already green on PR #33. Verification found wiring gaps in the agent/Explorer path and closed them on this branch. |

Phase 3 was **not** started.

## Inspection method

- Gap register vs `DEVELOPMENT_PLAN.md` audit IDs (79/79).
- Phase 0 metadata contracts vs DocType JSON and controllers.
- Phase 1 security authorities: translation memory, `ai.tools.query`, `utils/netguard.py`, File resolver, API validation.
- Phase 2 retrieval owner `ai.retrieval` / `ai.retrieval_utils`, agent orchestration, Knowledge Explorer, patches `v0_0_14`–`v0_0_16`.
- Hosted CI: PR [#33](https://github.com/Oscar-Hyde/ai_fr_hg/pull/33) all four required checks SUCCESS at SHA `a798663`.
- Product guides vs closed findings (P0-13 truthfulness).

## Phase 0 — confirmed

| ID | Evidence |
| --- | --- |
| SEC-05 | `encrypt_documents` hidden, read-only, default 0, label contains Unsupported; controller rejects enablement. |
| ING-01 | `source_type` options File/Text/URL/DocType Record only; controller rejects others. |
| RET-05 | Model types Chat/Completion/Embedding/Vision; diagnostics `reranker: unsupported`. |
| ING-03 | Reader registry has `eml`, not `msg`. |
| ING-02 | PDF reader warning “Scanned-PDF OCR is not supported”. |
| INT-01 | `target_doctype` hidden/read-only, JSON-only description. |
| TRN-06 | Label “Preserve Extracted-Text Structure”. |
| ADR-001–007 | Present; ADR-004 reaffirmed in Phase 2. |
| OPS-01 | Workflows pin Frappe `d7000da3…` / 17.0.0-dev. Checks green. **Branch protection still off.** |

## Phase 1 — confirmed

| ID | Evidence |
| --- | --- |
| SEC-01 / TRN-01 | `authorized_memory_scope`: no KB ⇒ no lookup; inline API throws on unauthorized KB; document and `translate_content` pass document KB; fingerprints include glossary/tone/domain. |
| SEC-02 / SEC-03 | Single authority `ai.tools.query`; count via `get_list`; field projection + deny list. |
| SEC-04 | `GuardedSession.trust_env = False`; pinned dial; redirect refusal; peer revalidation; 17 loopback tests. |
| SEC-06 / FILE-01–06 | Folder settings compose File permission hooks; paste fails closed; `resolve_file_identity`; Shared Uploads / Public labels; storage folder File Link. |
| SEC-07 | Redacted search telemetry, `log_search_queries`, native 30-day retention. |
| FILE-07 | Version-gated to Frappe v17; browser smoke still Phase 7. |

## Phase 2 — confirmed, then hardened

Already proven on hosted Server (221-chunk semantic needle, 521-chunk keyword needle, mixed models, KB policy, sibling folder, diagnostics).

Verification found these **closed-finding gaps** and corrected them:

1. **RET-07 agent path.** `ask(..., folder=)` called `run_agent_turn(..., folder=)`, but `run_agent_turn` never passed `folder` to `retrieve`. Folder-only asks also skipped retrieval when `use_knowledge` was off.
2. **RET-04 agent path.** `get_agent_knowledge_base_weights` existed and was unused.
3. **RET-06 citation mapping.** `build_context` numbered candidates before packing. Agent citations used the pre-pack list. Packed list now owns `[n]`; agent persists packed citations.
4. **Folder ∩ knowledge base.** Folder scope replaced the requested KB list, so an Explorer KB chip plus folder could return another accessible KB in that folder. Scopes now intersect.
5. **Missing `get_search_facets`.** Knowledge Explorer called a whitelist that did not exist (caught and ignored). Facets now live in `ai.retrieval.search_facets` with a thin API facade.
6. **Semantic-only embedding failure.** `semantic_search` swallowed every group failure and returned `{}`. Semantic-only now re-raises; hybrid still degrades to keyword.
7. **Entity document 5000-row cap.** Replaced with keyset paging (same completeness rule as RET-01).
8. **Product-truth drift (P0-13).** README / PROJECT_STATUS / TRANSLATION / ARCHITECTURE / FILE_TO_ANSWER still described SEC-01, SEC-04, retrieval weights, and CI billing as open.

Non-blocking residuals (unchanged):

- Ranked FULLTEXT pool of 1000 (not an unordered cap); unique/non-latin terms use complete LIKE; empty FULLTEXT falls back to LIKE.
- Browser E2E and 100k-chunk load → Phase 7.
- MariaDB VECTOR not introduced; reranker unsupported (ADR-004).
- CHAT-01…08 remain Phase 3 (latest-N history, concurrent sequencing, cancel/reconnect, conversation UX).

## Commands (this verification)

Local sandbox has no MariaDB/bench. Executed:

- `python3 -m unittest discover -s ai_fr_hg/tests -p 'test_retrieval_units.py' -v`
- `python3 -m unittest discover -s ai_fr_hg/tests -p 'test_folder_units.py' -v`
- `python3 -m unittest discover -s ai_fr_hg/tests -p 'test_phase_0_contracts.py' -v`
- `python3 -m compileall -q ai_fr_hg`

Hosted evidence already on PR #33: Server, Linter, Frontend static, Dependency audit SUCCESS at `a798663`.

## Do not start Phase 3 until

1. This verification commit is on the session branch and the four hosted checks are green.
2. Phase 0 branch protection remains an owner action (documented, non-blocking for Phase 3 sequencing **only** because later phases already proceeded under the same documented limitation).
3. CHAT-01 is the first Phase 3 finding: latest-N history, not more retrieval work.
