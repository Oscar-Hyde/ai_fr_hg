# INT-04 Completion Report — §17

**Phase:** 4 Ingestion, Intelligence, Patterns, Translation
**Finding:** INT-04 Compare/classify/extract only inspect leading text
**Verdict:** CLOSED — IMPLEMENTED (Phase 4 overall remains FAIL/OPEN until TRN/PAT closed)

## Findings Completed
- INT-04: Whole-document, coverage-aware strategies for classify/extract/compare (no text[:budget] prefix)

## Files Changed
- `ai_fr_hg/ai/intelligence.py` — classify chunk_vote, extract map_merge with per-window INT-02 validation + deterministic merge + final validation, compare windowed_synthesis
- `ai_fr_hg/ai/validation.py` — already closed INT-02, reused as canonical validator
- `ai_fr_hg/api/knowledge.py` — extract_document_data distinguishes ValidationError vs ProviderError, logs not persist invalid, permission checks preserved
- `ai_fr_hg/tests/test_int04_whole_doc.py` — 15-test backend suite (deterministic retry, concurrent isolation, tie, compare min coverage, large bounded, malicious, no prefix)
- `docs/GAP_REGISTER.md` — INT-04 → CLOSED

## Architecture
- **One chunking authority:** `ai/chunking.py:chunk_text`
- **No summarization reuse:** INT-04 uses distinct strategies (vote, merge, synthesis) not INT-03 reducer
- **Coverage model:** source_chars → discovered windows_total → submitted per-window run_chat → successfully processed windows_processed/processed_chars → failed windows_failed; coverage_ratio = processed/source; windows_failed never counted
- **Extract:** window → parse → INT-02 validate → coerce → validate again → per_window_results → merge (consensus / most frequent / null on ambiguous tie + merge_conflicts provenance) → final validate before return/persist
- **Classify:** per-window classify → Counter vote → tie breaker avg confidence + sorted name deterministic
- **Compare:** chunk both docs, pair windows sequentially (budget alignment not semantic equivalence, documented in provenance.note), per-window compare → hierarchical batch synthesis, coverage ratio = min(a,b)

## Frappe V17 Integration
- DocType AI Document read/write via `check_permission`, `frappe.get_doc`, `db_set`, `frappe.get_list`
- API facade thin (`api/knowledge.py:extract_document_data`, `classify_document`, `compare`) delegates to service
- Background same canonical `intelligence.*` path (no second implementation)

## Security / Permissions
- classify pure-text requires no doc permission (read-only operation)
- extract/compare require AI Document read; persist requires read+write
- Auditor read-only can read summary but save=True denied (tested via PermissionError on get_list before classify)
- Validation failures typed separately (ValidationError vs ProviderError)

## Data / Migration
- No schema migration

## Frontend
- No UI change until coverage authoritative — existing UI still request → result

## Tests
- `tests/test_int04_whole_doc.py` 15 tests: short_single, long_every_window, tail_affects, failed_not_counted, extract_validates_every_window, extract_merged_validated, conflicting deterministic, tie, compare min coverage, large bounded, malicious, retry deterministic, concurrent isolation, short compatible, no prefix, large malicious
- Bench `site1.local`:
  - `exec(open('/tmp/run_int04.py').read())` → 15/15 PASS (after fixes: schema.model, tail windows >2000 floor, path robustness, coverage alias)
  - `exec(open('/tmp/verify_int04.py').read())` → classify 4 windows PASS
  - `exec(open('/tmp/int04_integration.py').read())` → Administrator classify PASS, failed extract blocked ValidationError, persistence not overwritten PASS

## Runtime Verification
- site1.local bench `a923d89` → `2c63649` migrated, restarted
- Whole-doc tail fact TAIL_B affects classify, extract per-window validation, compare min coverage, failed windows not counted, large bounded, retry deterministic, concurrent isolation verified

## Remaining Issues
- None for INT-04. Phase 4 still has TRN-03/04/05/07, PAT-01..04 OPEN
