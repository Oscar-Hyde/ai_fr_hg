# Phase 2 — Retrieval Correctness and Scale

**Objective:** make retrieval correct before optimizing or expanding the search UI.

**Opened:** 2026-08-20
**Phase owner:** Retrieval
**Status:** COMPLETE — backend contracts implemented; hosted Frappe v17 bench green on PR #33 (`a798663`) plus 2026-08-20 verification wiring (agent folder/weights/packed citations, KB∩folder, facets API)

## Phase inventory

| ID | Finding | Current State | Required State | Files | Tests | Migration | Frontend | Status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| RET-01 | Semantic ranking truncates corpus | Unordered 200-row cap | Page every eligible vector; ceiling is a latency flag only | `ai/retrieval.py`, `ai/vector.py` | Unit score-pairs + integration beyond-200 | `v0_0_16` embedding index | Diagnostics corpus size | COMPLETE |
| RET-02 | Keyword ranking truncates candidates | LIKE cap 500 | FULLTEXT when eligible; otherwise complete LIKE scan | `ai/retrieval.py`, patch | Integration beyond-500 | FULLTEXT `content_fts` | Keyword backend in diagnostics | COMPLETE |
| RET-03 | Mixed embedding models compared | One query vector | Group by model/dimensions; skip stale/incompatible | `ai/retrieval.py` | Mixed-model integration | None | Embedding models list | COMPLETE |
| RET-04 | KB top-k/threshold/weights ignored | Platform/request only | Request override → KB policy → platform default; weights in fusion | `ai/retrieval.py`, `ai/agent.py` | Policy + weight tests | None | Diagnostics thresholds/weights | COMPLETE |
| RET-05 | Reranker declared | Removed in Phase 0 | Keep absent (ADR-004 reaffirmed) | diagnostics `reranker=unsupported` | Source contract + Phase 0 metadata | None | Not shown as a control | COMPLETE — REMOVED |
| RET-06 | Oversized first context block | Empty context | Truncate first block; dedupe overlap; packed citations | `ai/retrieval_utils.py`, `ai/retrieval.py`, `ai/agent.py` | Unit packing + integration | None | N/A (prompt packing) | COMPLETE |
| RET-07 | Folder prefix LIKE | `Home/A` matches `Home/AB` | Shared exact-or-descendant helper | `ai/folders.py`, retrieval, ask API | Unit + integration sibling prefix | None | Folder Link filter | COMPLETE |
| Diagnostics | Missing | Corpus, candidates, models, strategy, thresholds, weights, fallback, degraded | `RetrievalDiagnostics` on search API | Integration search API | Ceiling setting | Explorer banner + manager panel | COMPLETE |
| Explorer UX | No pagination/filters | Pagination, folder, entity, URL hash, retry | `knowledge_explorer.js`, SCSS | Static parse | None | Page | COMPLETE (browser E2E → Phase 7) |

## Contracts (summary)

### RET-01 — semantic completeness

- **Inputs:** query, authorized KBs, optional documents/folder, optional embedding model.
- **Behavior:** keyset-page every embedded chunk whose stored model/dimensions match the KB group. Score in batches of 128. Never drop a comparable vector because it sat beyond row 200.
- **Ceiling:** `retrieval_brute_force_ceiling` (default 10 000) sets `degraded` when exceeded. Scanning remains complete.
- **Failure:** embedding failure on one group degrades hybrid to keyword; semantic-only raises.

### RET-02 — keyword completeness

- **FULLTEXT path:** MariaDB `MATCH … AGAINST` when the `content_fts` index exists and every token is latin length ≥ 3.
- **LIKE path:** page every match, score in Python (Arabic/Hebrew/identifiers). No 500-row cap.
- **Tokenization:** Unicode words, identifiers (`INV-2024`), dotted paths.

### RET-03 — mixed models

- Group KBs by `(embedding_model, dimensions)`.
- Embed the query once per group.
- Skip stale (wrong stored model/dim) and incompatible (decode/width mismatch) chunks; count them in diagnostics.

### RET-04 — KB policy precedence

1. Explicit `retrieve(top_k=…, similarity_threshold=…)` override.
2. Per-KB `top_k` / `similarity_threshold` before fusion.
3. Platform default.

Agent child `weight` multiplies fused scores (non-positive excludes). Attached-document retrieval still skips the similarity threshold.

### RET-06 — context packing

- First block is truncated to the budget rather than skipped.
- Same-document near-duplicates (containment or Jaccard ≥ 0.85) are dropped.
- Packed results are the citation list.
- Optional model `context_window` minus generation reserve further caps characters.

### RET-07 — folder descendants

`folder_match_or_filters` is the single helper: `field = root OR field LIKE escaped(root)/%`. Used by retrieval and `ask` (via `run_agent_turn(..., folder=)`).

## Frappe v17-native integration

- ORM `get_all` / `get_list` with keyset pagination (`name > cursor`).
- Permission-aware `get_list` for folder-scoped AI Documents and pattern-entity facets.
- Native File Link picker on Knowledge Explorer.
- Idempotent patch for MariaDB FULLTEXT + composite embedding index.
- Platform Settings DocType field for the ceiling.
- No second vector database. MariaDB VECTOR was evaluated and rejected (not a Frappe field type; would duplicate Long Text embeddings).

## Security

- Folder/entity scoping uses `get_list` (row permissions).
- Knowledge-base targets still pass `get_accessible_knowledge_bases`.
- Search telemetry remains the Phase 1 redacted path (`knowledge._log_search_job`).
- LIKE terms are escaped so query wildcards cannot widen matches.

## Data/migration

Patch `v0_0_16_retrieval_indexes`:

- `FULLTEXT content_fts (content)` on `tabAI Document Chunk` when missing.
- Composite index `(knowledge_base, embedding_model, embedding_dimensions)`.
- Idempotent; skipped on PostgreSQL (unsupported). Failures are logged rather than aborting migrate.

## Frontend

Knowledge Explorer now has:

- loading, empty, error, permission-denied, retry;
- degraded-mode banner;
- manager diagnostics (strategy, corpus, candidates, models);
- folder Link (is_folder) and entity-type facet;
- offset pagination;
- URL hash persistence (`q`, `type`, `kb`, `folder`, `entity`, `offset`).

Browser/Desk verification remains Phase 7.

## Tests

Executed in this workspace:

- `python3 -m unittest discover -s ai_fr_hg/tests -p 'test_retrieval_units.py' -v` — **PASS, 16**.
- `python3 -m unittest discover -s ai_fr_hg/tests -p 'test_folder_units.py' -v` — **PASS, 17**.
- `python3 -m unittest discover -s ai_fr_hg/tests -p 'test_phase_0_contracts.py' -v` — **PASS, 13**.
- `python3 -m compileall -q ai_fr_hg` — **PASS**.
- `node --check` on Knowledge Explorer JS — **PASS**.

Hosted bench integration (221-chunk semantic needle, 521-chunk keyword needle, mixed models, KB policy, folder sibling, search diagnostics) is in `test_ai_knowledge_base.py` and passed on the pinned Frappe v17 Server job.

## Runtime verification

Hosted Server on PR #33 SHA `a798663` passed in 2m27s (`bench run-tests --app ai_fr_hg`). Linter, Frontend static, and Dependency audit also passed. A 2026-08-20 closed-phase verification found and corrected remaining wiring gaps (see `PHASE_0_2_VERIFICATION.md`).

## Remaining issues

1. 100k-chunk latency profile and browser E2E are Phase 7.
2. MariaDB VECTOR index is intentionally not introduced.
3. Reranker remains unsupported (ADR-004).
4. Branch protection on `main` remains owner-only (Phase 0 / OPS-01).
5. Ranked MariaDB FULLTEXT uses a result pool (`FULLTEXT_POOL`, default 1000) rather than an unordered cap. Unique identifiers and non-latin terms take the complete LIKE path. Empty FULLTEXT falls back to LIKE.

## Phase verdict

`PASS WITH DOCUMENTED NON-BLOCKING LIMITATION` — required Phase 2 correctness, policy, packing, folder, diagnostics, and Explorer wiring are implemented. Hosted Frappe v17 integration passed on PR #33. Browser E2E and 100k-chunk load remain Phase 7.
