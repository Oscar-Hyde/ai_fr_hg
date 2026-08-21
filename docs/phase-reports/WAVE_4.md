# Wave 4 — File Intelligence Foundation

**Objective:** make extraction the authoritative information layer: detect, resolve, extract, normalize, persist evidence, and fail visibly.

**Opened:** 2026-08-21
**Status:** COMPLETE — backend contracts implemented; hosted Frappe v17 verification required; browser E2E remains Phase 7

Phase 4 remaining evidence (ING-06 OS-level RQ kill, TRN-04 Desk Stop/reconnect) stays Phase 7, matching Phases 1–3.

## Architecture decision (evaluated Frappe v17)

Frappe File owns bytes, folders, privacy, and attachments. It does not detect formats, parse Office/PDF/email, or preserve extraction provenance. A custom extraction owner is required and must not replace File. See ADR-008.

**Canonical owners:**

| Responsibility | Owner |
| --- | --- |
| Bytes, folders, privacy | Frappe File |
| Format detection + resolution + evidence | `ai.extraction` |
| Format parsers | `ai.readers` (one registry) |
| ZIP bomb / traversal / encryption | `ai.readers.archive` (one guard) |
| Persist + jobs | `ai.ingestion` |

## Format boundary

Implemented (must remain real): PDF text layer, DOCX, XLSX/XLSM, PPTX, ODT, ODS, **ODP**, CSV/TSV, EML, HTML, XML, JSON, Markdown, text/code, images (vision/OCR).

Intentionally unsupported (not advertised): audio, video, database files, OLE `.doc`/`.xls`/`.ppt`, Outlook `.msg`, scanned-PDF OCR, generic `zip`/`tar` as a document source.

## Inventory

| ID | Finding | Required | Status |
| --- | --- | --- | --- |
| W4-01 | Extension-only resolution | Magic + extension; mismatch warning; magic reader preferred | COMPLETE |
| W4-02 | Duplicate ZIP validators | One archive guard (`ai.readers.archive`) | COMPLETE |
| W4-03 | ReadResult incomplete (`word_count` dead) | Structure, embedded objects, live word/character counts | COMPLETE |
| W4-04 | ODP missing while PPTX/ODT exist | Real ODP reader (`odfpy`) | COMPLETE |
| W4-05 | DOCX drops headers/footers/comments/links | Extract them into text/structure/embedded objects | COMPLETE |
| W4-06 | Extraction evidence not durable | `AI Document.extraction_evidence` JSON + form summary + API | COMPLETE |
| W4-07 | Audio/video/DB/OLE not real | Do not advertise (ADR-008) | COMPLETE |

## Pipeline

```
File → Detection → Format Resolution → Extraction → Normalization
    → Metadata Processing → Entity Extraction (embedded objects + later pattern scan)
    → Evidence Creation → Persistence → Query and Visualization
```

Detection uses magic bytes (PDF, ZIP/OpenXML/ODF subtype, images, JSON, XML, HTML, email headers) compared with the filename extension. A mismatch is a visible warning; when a real magic-family reader exists it is preferred over the extension reader.

ZIP policy is enforced once: 500 members, 50 MB uncompressed, ratio 100, no `..` or absolute paths, no encryption. Office readers delegate to the same guard.

Evidence persisted on `AI Document` is bounded JSON (detector, reader, structure kinds, up to 50 embedded objects, checksum, counts). It does not store full extracted text.

## Data/migration

Patch `v0_0_20_extraction_evidence` is idempotent: adds `extraction_evidence` if missing and backfills empty objects. Frappe migrate also syncs the DocType JSON field.

## Tests

Local (no bench):

- `python3 -m unittest discover -s ai_fr_hg/tests -p 'test_extraction_units.py' -v` — PASS, 20
- `python3 -m unittest discover -s ai_fr_hg/tests -p 'test_phase_0_contracts.py' -v` — PASS, 14
- `node --test ai_fr_hg/tests/js/test_frontend_ui.mjs` — PASS, 19
- `python3 -m compileall -q ai_fr_hg` — PASS
- `ruff check ai_fr_hg` — PASS

Hosted Frappe v17 bench on SHA `f03173c`:

| Check | Result | Run |
| --- | --- | --- |
| Server | **pass** 2m34s | [32461564835](https://github.com/Oscar-Hyde/ai_fr_hg/actions/runs/32461564835) |
| Linter / Frontend static / Dependency audit | **pass** | [32461564867](https://github.com/Oscar-Hyde/ai_fr_hg/actions/runs/32461564867) |

PR: [#41](https://github.com/Oscar-Hyde/ai_fr_hg/pull/41)

## Remaining issues

1. Browser/Desk E2E of the evidence summary and mismatch warning is Phase 7.
2. Audio, video, database files, OLE `.doc`/`.xls`/`.ppt`, Outlook `.msg`, scanned-PDF OCR, and generic archives remain unsupported by decision (ADR-008).
3. ING-06 / TRN-04 Desk/OS evidence remains Phase 7.
4. OPS-01 branch protection remains owner-only.

## Wave verdict

`PASS WITH DOCUMENTED NON-BLOCKING LIMITATION` pending green hosted Server/Linter/Frontend static/Dependency audit on this SHA. Browser E2E is Phase 7. Advertised formats are real parsers; unsupported families are not advertised.
