# Wave 4 Completion Certificate

```
Wave:
Wave 4 — File Intelligence Foundation

Branch:
arena/01a02323-ai-fr-hg

Implementation commit:
f03173c Implement Wave 4 file intelligence: detection, ODP, evidence, archive guard.

Evidence commit:
0286d1a Record Wave 4 hosted Frappe v17 CI evidence.

PR:
https://github.com/Oscar-Hyde/ai_fr_hg/pull/41

Status:
PASS — IMPLEMENTATION COMPLETE
WITH DEFERRED RUNTIME QUALIFICATION
```

This certificate is the permanent close-out artifact for Wave 4. It does not start Wave 5. It records what is real, what is intentionally unsupported, and what remains deferred.

---

## Architectural law (evaluated Frappe v17)

Frappe File owns bytes, folders, privacy, attachments, and file identity. It does not detect formats, parse Office/PDF/email, or preserve extraction provenance. A custom extraction owner is required and must not replace File. Recorded as **ADR-008**.

```
Frappe File
    |
    | owns: bytes, attachments, privacy, permissions
    v
ai.extraction
    |
    | owns: detection, format resolution, extraction orchestration, evidence
    v
ai.readers
    |
    | owns: format-specific parsing (one registry)
    v
ai.readers.archive
    |
    | owns: ZIP bomb / traversal / encryption (one guard)
    v
ai.ingestion
    |
    | owns: persist + jobs
    v
AI Document.extraction_evidence
```

No second File store. No parallel reader registry. No duplicate ZIP validators.

---

## Implemented (real)

| Claim | Evidence |
| --- | --- |
| Detection | `ai_fr_hg/ai/extraction.py:84` `FormatIdentity`; `:116` `detect_format` (magic + extension + mismatch) |
| Format resolution | `ai_fr_hg/ai/extraction.py:256` `extract_bytes`; magic reader preferred on mismatch |
| Reader ownership | `ai_fr_hg/ai/readers/__init__.py` `BUILTIN_READERS` (37 extensions, including `"odp": OdpReader` at `:69`) |
| Archive security | `ai_fr_hg/ai/readers/archive.py:19` `MAX_ARCHIVE_MEMBERS = 500`; `:41` `validate_zip_container` |
| Structure extraction | `ai_fr_hg/ai/readers/base.py:23-24` `structure` / `embedded_objects`; PDF pages, DOCX headers/footers/comments/links, ODP slides |
| Live counts | `ai_fr_hg/ai/readers/base.py:31` `word_count` property (no longer dead code) |
| Evidence persistence | `AI Document.extraction_evidence` field; written in `ai_fr_hg/ai/ingestion.py:464`; patch `v0_0_20_extraction_evidence` |
| Provenance model | `ExtractionEvidence` at `ai_fr_hg/ai/extraction.py:95` (detector, reader, structure kinds, ≤50 embedded objects, SHA-256, counts) |
| Query / visualization | `ai_fr_hg.api.knowledge.get_document_evidence`; form summary via `public/js/ui/extraction_evidence.js` |
| Truthful advertising | README / ADR-008 / TRANSLATION: 37 registered extensions; OLE/audio/video/DB/msg/scanned-PDF OCR/generic zip **not** advertised |

### Format boundary (must remain real)

Implemented parsers: PDF text layer, DOCX, XLSX/XLSM, PPTX, ODT, ODS, **ODP**, CSV/TSV, EML, HTML, XML, JSON, Markdown, text/code, images (vision or optional image OCR).

### Inventory close-out

| ID | Status |
| --- | --- |
| W4-01 Detection | COMPLETE |
| W4-02 One archive guard | COMPLETE |
| W4-03 ReadResult contract | COMPLETE |
| W4-04 Real ODP reader | COMPLETE |
| W4-05 DOCX structure | COMPLETE |
| W4-06 Durable evidence | COMPLETE |
| W4-07 Honest unsupported | COMPLETE |

---

## Not included (by decision, not by accident)

These are **not** advertised and have **no** stub parsers:

- OCR pipeline for scanned PDFs
- media extraction (audio / video)
- database extraction
- legacy Office binary formats (OLE `.doc` / `.xls` / `.ppt`)
- Outlook `.msg`
- generic `zip` / `tar` as a document source

See ADR-008. A later wave may add a family only when a real parser, tests, and documentation land in the same change.

---

## Deferred (not Wave 4 defects)

| Item | Owner wave |
| --- | --- |
| Browser/Desk E2E of evidence UI | Wave 8 / Phase 7 |
| Hostile 1GB+ / memory-pressure / worker-timeout matrix | Wave 8 / Phase 7 |
| ING-06 OS-level RQ kill, TRN-04 Desk Stop/reconnect | Wave 8 / Phase 7 (unchanged from Phases 1–5) |
| OPS-01 branch protection (owner-only HTTP 403) | Wave 8 / Phase 7 |
| Entity extraction compounding Wave 4 evidence | Wave 5 Intelligence Layer |
| Full Office fixture matrix on optional `[documents]` extras | Wave 4 stabilization or Wave 8 runtime matrix |

Hosted Frappe v17 **did** migrate and run the app test suite on SHA `f03173c` (Server pass). That is implementation proof, not the full production qualification matrix.

---

## Test evidence

Local (no bench):

```
python3 -m unittest discover -s ai_fr_hg/tests -p 'test_extraction_units.py' -v
# PASS, 20

python3 -m unittest discover -s ai_fr_hg/tests -p 'test_phase_0_contracts.py' -v
# PASS, 14

node --test ai_fr_hg/tests/js/test_frontend_ui.mjs
# PASS, 19

python3 -m compileall -q ai_fr_hg
# PASS

ruff check ai_fr_hg
# PASS
```

Hosted Frappe v17 on SHA `f03173c`:

| Check | Result | Run |
| --- | --- | --- |
| Server | **pass** 2m34s | [32461564835](https://github.com/Oscar-Hyde/ai_fr_hg/actions/runs/32461564835) |
| Linter / Frontend static / Dependency audit | **pass** | [32461564867](https://github.com/Oscar-Hyde/ai_fr_hg/actions/runs/32461564867) |

`test_extraction_units.py` covers: PDF magic, extension/magic mismatch, email magic, JSON BOM, PNG, DOCX/ODP zip kind, zip member bomb, path traversal, compression ratio, absolute paths, live `word_count`, evidence attach, bounded evidence (no full text), 37-extension inventory (no `.doc`/`.xls`/`.ppt`/`.msg`/`.zip`).

---

## What this wave is not

Before Wave 4:

```
AI Fr HG = Files + Readers + AI Features
```

After Wave 4:

```
AI Fr HG = Enterprise information pipeline foundation

Files → Detection → Extraction → Normalization → Evidence
      → (Wave 5) Entities → Knowledge → Retrieval → Reasoning → Automation
```

The next compounding dependency is **not more readers**. It is entity extraction, evidence graph, and knowledge intelligence (Wave 5), after this certificate.

Wave 6 (automation / pipelines / tasks) already landed on this branch in Phase 5 and is out of Wave 4 scope.

---

## Verdict

```
PASS — IMPLEMENTATION COMPLETE
WITH DEFERRED RUNTIME QUALIFICATION
```

Engineering foundation is complete and CI-green on the pinned Frappe v17 bench. Browser E2E, hostile large-file chaos, and production qualification remain Wave 8. Unsupported format families remain unsupported.
