# INT-03 Completion Report — §17

**Phase:** 4 Ingestion, Intelligence, Patterns, Translation
**Finding:** INT-03 Long summarization reduction is lossy
**Verdict:** CLOSED — IMPLEMENTED (Phase 4 overall remains FAIL/OPEN until all 15 findings closed)

## Findings Completed
- INT-03: Bounded hierarchical coverage-preserving reduction for `ai/intelligence.py:summarize`

## Files Changed
- `ai_fr_hg/ai/exceptions.py` — new `HierarchicalReductionError(DocumentProcessingError)` typed bounded failure
- `ai_fr_hg/ai/intelligence.py` — replaced tail truncation `combined[:budget]` with `_hierarchical_reduce()` (pack batches ≤budget, recurse, level>10 explicit error, provenance `[Section i]`, ordering, no second token authority)
- `ai_fr_hg/tests/test_int03_hierarchical.py` — 11-test regression suite (now 11/11 PASS on bench)
- `docs/GAP_REGISTER.md` — INT-03 → CLOSED

## Architecture
- **One chunking authority:** `ai/chunking.py:chunk_text`
- **One summarization authority:** `ai/intelligence.py:summarize` (canonical map → hierarchical reduce)
- **Packing vs execution split:** reducer packs to `_context_budget(model_doc)` (max(window*0.6*4,2000)); provider `ai/engine.py:run_chat` remains authoritative for actual model context enforcement; no second token system
- **Hierarchy:** Level 0 Section 1..N → Level 1 Sections 1–4,5–8 → Level 2 Sections 1–8,9–16 … provenance markers survive each reduce prompt
- **Bounded failure:** level>10 raises `HierarchicalReductionError` never silently truncates
- **Persistence guard:** `api/knowledge.py:summarize_document` does `summary = summarize(...); if save: doc.db_set("summary", summary)` — on HierarchicalReductionError db_set not executed, existing valid summary preserved (bench proven)

## Frappe V17 Integration
- DocType `AI Document` lifecycle (`generate_summary` whitelisted, `check_permission`, `db_set`, `frappe.get_doc`)
- API facade `api/knowledge.py:summarize_document` thin (permission read/write, delegates to service)
- Background same path (no separate worker implementation)
- ORM `frappe.db.get_value`, `frappe.get_list`, realtime not needed

## Security / Permissions
- Read-only path: `save=False` requires `AI Document` read only
- Persist path: `save=True` requires read+write (`check_permission("write")`)
- Auditor/read-only can read summary, denied on save=True
- Unrelated/unauthorized denied via `has_permission`
- No authorization bypass via retry — same canonical path

## Data / Migration
- No schema change, no patch

## Frontend
- No UI change required — contract remains `request → final summary`; existing `AI Document` form and `summarize_document` API already reflect backend state; no spurious coverage indicator added

## Tests
- `ai_fr_hg/tests/test_int03_hierarchical.py` — 11 tests: short, long tail TAILFACT_UNIQUE_12345, multi-level, ordering, provenance, boundary exact 3000/3001, empty/single, provider failure map/reduce, recursion bound explicit, no hidden truncation, budget invariant
- Manual `verify_int03.py` — 4 tests: short, long+tail, not overwrite on failure, API same path
- Commands executed on bench `site1.local`:
  - `bench --site site1.local execute "exec(open('/tmp/verify_int03.py').read())"` → 4/4 PASS (after fix sys.CALLS)
  - `bench --site site1.local execute "exec(open('/tmp/run_int03_tests.py').read())"` → 11/11 PASS
  - `bench --site site1.local execute "exec(open('/tmp/perm_int03.py').read())"` → Administrator read-ok/save-ok

## Runtime Verification (site1.local, app ai_fr_hg, model mocked via patch)
- Short ≤budget → single reduce
- Large (>budget) → all windows participate, tail TAILFACT survives into reduce payloads (sys.CALLS)
- Multi-level → >5 run_chat calls
- Provider failure map/reduce → typed ProviderError propagation
- Excessive hierarchy → HierarchicalReductionError with "exceeded 10 levels"
- No silent truncation — grep `[:budget` absent from `_hierarchical_reduce`
- Existing summary preserved on failure — `old == after` after forced HierarchicalReductionError
- API `summarize_document` same implementation as `intelligence.summarize` and `AI Document.generate_summary`

## Remaining Issues
- None for INT-03. Phase 4 still has 11 OPEN findings (ING-06, INT-04, TRN-03/04/05/07, PAT-01..04)

