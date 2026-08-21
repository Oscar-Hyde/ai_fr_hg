# Part 1 conformance assessment — File Intelligence Foundation

**Assessment date:** 2026-08-21
**Assessed against:** *AI_FR_HG Enterprise Production Completion and Implementation Proposal*, Part 1 (§1–§12)
**Codebase reviewed:** branch `arena/01a024b6-ai-fr-hg`, from `7abc3c9`
**Method:** clause-by-clause reading of the proposal against source, DocType JSON, and the existing audit artifacts (`DEVELOPMENT_PLAN.md`, `GAP_REGISTER.md`, `ARCHITECTURE_DECISIONS.md`).

**Status of this document:** assessment only. No code has been changed. Part 1 defines Domains 1–5 but only specifies Domain 1 (File Intelligence) in requirement detail; Domains 2–5 are named and are deferred to Part 2. This document therefore scopes to §6–§12.

---

## 1. Summary

Part 1's *architecture* is already substantially the architecture this application has. The pipeline in §7, the layering in §2.1, the single-owner rule in §4.1, and the evidence mandate in §12 are not new directions — they are existing, implemented properties of `ai.extraction` → `ai.readers` → `ai.ingestion`. That is the good news, and it means Part 1 is mostly a *verification* exercise rather than a redesign.

The material gaps are concentrated in **§6.2 (supported formats)**, **§8 (extraction result contract)**, and **§11 (entity and relationship intelligence)**. Two of these are genuine missing subsystems, not polish.

| Part 1 clause | Verdict |
| --- | --- |
| §2.1 Layering | Conforms |
| §3.1 Single application | Conforms |
| §4.1 One responsibility / one owner | Conforms |
| §6.2 Documents | Partial — legacy `.doc` absent |
| §6.2 Spreadsheets | Partial — `.xls` absent; **formulas discarded** |
| §6.2 Presentations | Partial — `.ppt` absent; embedded content dropped |
| §6.2 Images | Partial — **no confidence tracking** |
| §6.2 Archives | **Not implemented** |
| §6.2 Email | Partial — **threading headers absent**; attachment bytes dropped |
| §6.2 Source code | Partial — recognized, structure not preserved |
| §7 Pipeline | Conforms |
| §8 Extraction contract | Partial — **timestamp and version absent from evidence** |
| §9 Normalization | Conforms |
| §10 Metadata | Conforms, with one caveat on immutability |
| §11 Entities and relationships | **Largely not implemented** |
| §12 Evidence and provenance | Partial — follows from §8 |

---

## 2. What already conforms

### §2.1 / §3.1 / §4.1 — architecture, single app, single owner

The layering the proposal mandates exists and is enforced:

- **API facade** — `ai_fr_hg/api/` (`knowledge.py`, `chat.py`, `pipeline.py`, …), 137 whitelisted methods.
- **Service layer** — `ai_fr_hg/ai/` (`ingestion.py`, `retrieval.py`, `engine.py`, `governance.py`).
- **Domain/intelligence** — `ai/extraction.py`, `ai/readers/`, `ai/patterns.py`, `ai/intelligence.py`.
- **Persistence** — 48 DocTypes across 7 modules.
- **Operational** — `tasks.py`, `ai/monitoring.py`, `ai/limits.py`.

Everything is inside the single app `ai_fr_hg`. There is no second Frappe app in the dependency path; `pyproject.toml` optional extras (`pypdf`, `openpyxl`, …) are Python libraries, not hidden applications, and every reader degrades with a structured warning when its library is missing. §3.1 holds.

§4.1's canonical-extraction rule is genuinely enforced: `ai/extraction.py` is the *only* entry point that performs detect → guard → read → evidence, and `ai/ingestion.py` is its only caller. There is no duplicate parser in the chat or search paths. This is the exact anti-pattern §4.1 warns about, and it is already avoided.

### §7 — the pipeline

The mandated stages all exist as discrete, owned steps:

| §7 stage | Owner |
| --- | --- |
| Detection | `extraction.detect_format` — magic bytes vs. extension, with a `mismatch`/`reason` verdict |
| Reader selection | `readers.get_reader` (+ `ai_document_readers` hook) |
| Extraction | `readers.*.read` |
| Normalization | `BaseReader.clean` + `ReadResult` |
| Metadata | `ingestion.py` → `AI Document` metadata fields |
| Entity | `ai/patterns.py` |
| Evidence recording | `extraction.build_evidence` → `AI Document.extraction_evidence` |
| Persistence | `AI Document` / `AI Document Chunk` |
| Retrieval availability | `ai/retrieval.py` |

Failure reporting is structured, not swallowed: `StructuredWarning` (`readers/base.py:35`) carries `code`, `category`, `severity`, `reader`, `source_file`, `location`, `message`, `details`, `timestamp`, `stage`, and is persisted durably to `AI Document.extraction_warnings` (finding ING-05, closed).

### §9 — normalization

`ReadResult` is the common internal representation and satisfies §9's three prohibitions. Original evidence is not modified (evidence is built from the raw `content` bytes and the parse result independently), source references survive (`pages`, `structure[].index`, `first_offset`), and extraction limitations are surfaced rather than hidden (structured warnings, `page_count` vs. extracted pages, `mismatch` reason).

### §10 — metadata

`AI Document` carries file identity, origin (`source_type`, `source_file_record`, `source_url`, `source_folder`), timestamps, ownership (`processing_requested_by`, native `owner`), classification (`tags`, `confidence`, `document_type`), technical properties (`mime_type`, `file_size`, `page_count`, `word_count`, `character_count`, `checksum`), and processing history (`reader_used`, `processing_duration_ms`, `retry_count`, `error_type`). It is searchable and `track_changes = 1` gives auditability.

> **Caveat on "immutable where required."** `mime_type`, `file_size`, `checksum`, `reader_used`, `metadata`, and `extraction_evidence` are `read_only: 1` — but that is a *client-side* Desk constraint. Frappe does not enforce `read_only` server-side; a whitelisted write or `db_set` can still mutate them. If §10's immutability is a real requirement rather than a presentational one, it needs a `validate` guard comparing against `self.get_doc_before_save()`. This is a small, contained fix.

---

## 3. Gaps requiring decision

### 3.1 §6.2 — legacy binary Office formats (DOC, XLS, PPT)

The proposal lists `DOC`, `XLS`, and `PPT` as examples. **None are registered.** `BUILTIN_READERS` (`readers/__init__.py:39`) contains only the OOXML and OpenDocument families. `get_reader("report.doc")` returns `None`, and ingestion reports the file as unsupported.

This is not an oversight to be quietly patched — it collides with **ADR-008** ("advertise only real parsers"). The three legacy formats are compound-binary (OLE2), not ZIP, and need a genuinely different parser stack (`olefile` + format-specific decoders, or an external converter such as LibreOffice headless). Naming them in a registry without a real parser would recreate exactly the "declared only" pattern the audit spent Phase 0 removing.

**Disposition required:** *Full Implementation* (accept a new parser dependency, or an out-of-process converter with its own sandboxing/timeout contract) **or** *Removal/Hiding if intentionally unsupported* (an ADR stating the legacy binary family is out of scope, with the unsupported-format message naming the conversion path for users).

### 3.2 §6.2 Spreadsheets — "maintain formulas where applicable"

`XlsxReader` loads with `data_only=True` (`readers/office.py:198`). That returns the last cached *value* and discards the formula entirely. `OdsReader` extracts plain cell text only (`office.py:297`). Cell relationships and formulas are therefore lost.

This directly violates **§8: "never silently discard information."** Today the loss is not even warned about. Note also `data_only=True` returns `None` for every formula cell in a workbook that has never been opened by Excel, so the current behaviour can silently yield blank cells.

**Disposition:** *Full Implementation* — a second `data_only=False` pass to capture formulas into `structure`, or at minimum a structured warning declaring that formulas were dropped. The warning alone is cheap and closes the §8 violation immediately.

### 3.3 §6.2 Presentations — "process embedded content"

`PptxReader` (`office.py:227`) extracts text frames, tables, and speaker notes, and preserves slide order correctly. But unlike `PDFReader` and `DocxReader`, it emits **no `embedded_objects`** — slide images, charts, and OLE objects are dropped with no record. §6.2 explicitly requires processing embedded content.

**Disposition:** *Full Implementation* — enumerate `slide.shapes` for picture/chart/OLE shape types into `embedded_objects`, matching the contract `PDFReader` already follows.

### 3.4 §6.2 Images — "confidence tracking"

OCR runs via `pytesseract.image_to_string` (`readers/plain.py:~225`), which returns a bare string with **no confidence data at all**. §6.2 requires confidence tracking and §11 requires entity confidence. Tesseract *can* supply per-word confidence via `image_to_data`, so this is achievable without a new dependency.

**Disposition:** *Full Implementation* — switch to `image_to_data`, aggregate mean/min confidence into metadata and evidence. Also relevant: the vision-model description path is tried *before* OCR and its output carries no confidence signal either.

### 3.5 §6.2 Archives — not implemented

This is the largest Domain 1 gap. **There is no archive reader.** `ai/readers/archive.py` is misleadingly named for this purpose: it is a ZIP *bomb guard* (`validate_zip_container`) applied to Office containers only — `ZIP_EXTENSIONS = {docx, xlsx, xlsm, pptx, odt, ods, odp}` (`archive.py:22`). No `zip`, `tar`, `gz`, or `7z` key exists in `BUILTIN_READERS`. Uploading a `.zip` produces "unsupported format."

All four §6.2 archive requirements are therefore unmet: safe extraction, recursive processing, file relationship tracking, protection against unsafe input. Only the last has reusable groundwork — the existing bomb guard (member count 500, 50 MB uncompressed, ratio 100, traversal and encryption rejection) is well-built and is the correct foundation.

This is a genuine subsystem, not a reader. Recursive processing means: per-member reader dispatch, a parent/child relationship model between `AI Document` records, recursion-depth limits, cumulative (not per-member) resource budgets, cycle protection, and a permission story for members. It also interacts with **ADR-003** (native File is the only folder authority) — extracted members must not become a synthetic folder tree that competes with `File`.

**Disposition:** *Full Implementation* as a scoped work package with its own design note, **or** explicit exclusion. It should not be attempted as an incidental reader addition.

### 3.6 §6.2 Email — "maintain conversation relationships"

`EmailReader` (`readers/plain.py:151`) captures `From`, `To`, `Cc`, `Subject`, `Date`, `Message-ID`. **`In-Reply-To` and `References` are not captured** — and those are precisely the headers that make threading possible. Conversation relationships cannot be reconstructed from what is stored. Separately, attachments are recorded by *filename only* (`plain.py:163-178`); the bytes are discarded, so "preserve attachments" is satisfied nominally but not substantively, and attachment content is never ingested.

**Disposition:** *Full Implementation.* Capturing the two headers is trivial and unblocks threading. Attachment-content ingestion is the same recursive-processing problem as §3.5 and should be sequenced with it.

### 3.7 §6.2 Source code — "preserve structure"

`py`, `js`, `ts`, `sql`, `sh` route to `TextReader`, which returns flat cleaned text with `metadata={"format": "text"}` and no structure (`plain.py:12-17`). So files are *recognized* but structure is not preserved and no analysis workflow is supported. Common languages are also simply absent: `java`, `go`, `rb`, `c`, `cpp`, `cs`, `php`, `rs`, `jsx`, `tsx`, `kt`, `swift`.

**Disposition:** decide the ambition level. Extending the extension map is minutes of work. "Preserve structure" in a meaningful sense (symbols, imports, definitions) implies per-language parsing — Python's stdlib `ast` covers `.py` for free; anything broader needs `tree-sitter` and a new dependency.

### 3.8 §8 — extraction result contract is incomplete

§8 mandates that **every** extraction result contain six elements. Measured against `ExtractionEvidence` (`extraction.py:95`) and `build_evidence` (`extraction.py:194`):

| §8 required element | Present | Where |
| --- | --- | --- |
| Source identity | Yes | `provenance.checksum_sha256`, `provenance.bytes`, `detector.*` |
| Processing timestamp | **No** | — |
| Extractor identity | Yes | `reader` (label) |
| Version information | **No** | — |
| Extracted content | Yes | `ReadResult.text` → `AI Document.content` |
| Evidence references | Yes | `structure`, `embedded_objects` |

Two of six are missing. Neither the app version (`__init__.py:1`, `0.0.1`), nor a reader version, nor the parsing library version (`pypdf.__version__` etc.) is recorded anywhere in evidence. A timestamp exists on `StructuredWarning` and on the document row, but not in the evidence object §8 governs.

This matters more than it looks. **§12 asks "which version produced it?" and today the system cannot answer.** When a reader is fixed, there is no way to identify which documents were extracted by the broken version and need reprocessing. This is the cheapest high-value fix in Part 1: three fields on a dataclass, plus a patch to backfill `null` for existing rows.

**Disposition:** *Full Implementation.* Recommended first action.

### 3.9 §11 — entity and relationship intelligence

§11 requires people, organizations, locations, dates, concepts, and relationships. The `AI Pattern Entity` DocType offers `entity_type` options: `email`, `url`, `phone`, `ip`, `hash`, `date`, `identifier`, `money`, `custom`.

**Only `date` overlaps.** People, organizations, locations, concepts, and relationships have no implementation anywhere in the app — no NER model, no extraction path, no schema. There is no `relationship` field or DocType.

The §11 sub-requirements fare as follows:

- *"maintain confidence"* — `AI Pattern Entity` has **no confidence field**. The current regex engine is deterministic (match or no match), so confidence has no meaning in it; a semantic NER layer would need the field added.
- *"preserve source references"* — **satisfied**: `first_offset`, `context_quote`, `source_checksum`, and PAT-02 already maps tail-window offsets back to true document positions.
- *"avoid unsupported assumptions"* — **satisfied and notably well done**: `_passes_semantic_check` rejects invalid IPv4 octets, impossible calendar dates, and unparseable money (PAT-03).

The existing pattern engine is a solid regex/deterministic extractor. §11 asks for a semantic one. That is a different capability class requiring either a local NER model (spaCy or similar — a substantial new dependency and a model-distribution question for a local-first app) or LLM-based extraction routed through the existing provider layer with the structured-output validator (`ai/validation.py`, INT-02) enforcing the schema.

The LLM route is architecturally cheaper here: it reuses `engine.resolve_model`, governance/quota (`GOV-01`…`GOV-04`), failover (`PROV-01`), and the existing validation contract, and it avoids shipping model weights. It costs determinism — and §8 asks for "deterministic outputs where possible," which would need an explicit carve-out for the semantic layer.

**Disposition:** *Full Implementation* as a Domain 1 work package with a prior architecture decision on the extraction mechanism. Relationships in particular need a data model before any code.

---

## 4. Conflicts with standing decisions

Part 1 intersects three closed audit findings and one ADR. These must be resolved deliberately, because reopening them reverses work that was completed with evidence.

| Part 1 clause | Conflicts with | Nature |
| --- | --- | --- |
| §6.2 DOC/XLS/PPT, Archives | **ADR-008** — advertise only real parsers | New formats need real parsers, not registry entries |
| §6.2 Images "OCR support" | **ING-02** (CLOSED — SCOPED) | Image OCR exists and conforms. If §6.2 is intended to include *scanned PDFs*, that promise was deliberately removed and its removal would be reversed |
| §6.2 Email | **ING-03** (CLOSED — REMOVED) | `.msg` was removed as not-real-parsing. `.eml` conforms. Reinstating `.msg` requires a real OLE2 parser |
| §6.2 Archives, email attachments | **ADR-003** — native File is the sole folder authority | Recursive member extraction must not create a competing folder tree |

**None of these are blockers — but each needs an explicit decision recorded as a dated ADR, not an implicit override by implementation.** That is the governance standard this repository already holds itself to, and Part 1's own governing principle reinforces it.

---

## 5. Sequencing observation

The gap register currently carries **10 `OPEN` and 2 `IN PROGRESS — DEFERRED EVIDENCE`** findings out of 80. Notably, they are almost entirely *not* in Domain 1:

- **Phase 6 (Learning):** LEARN-02, -03, -04, -05
- **Phase 6 (Operations):** OPS-03, -04, -05, -06
- **Phase 6 (Providers):** PROV-03
- **Phase 7:** CHAT-09
- **Deferred to Phase 7 evidence:** ING-06, TRN-04
- **Owner-blocked:** OPS-01 (branch protection is not a gate)

Domain 1 — the subject of Part 1 — is the *most* complete area of the application. The genuinely open work sits in Learning, Operations, and browser/E2E evidence.

This creates a real tension worth naming: Part 1 directs new Domain 1 capability (archives, semantic NER, legacy formats), while the repository's own audit says the path to production runs through closing Phase 6 and executing Phase 7 qualification. Both are legitimate, but they compete for the same capacity, and Part 1's §5 lists Domains 2–5 as still to be specified in Part 2.

**Recommendation:** treat the §8 evidence contract (timestamp + version) and the §6.2 silent-discard warnings as immediate, low-cost, high-value work — they are strict improvements that make everything downstream auditable, and they close real §8 violations. Hold archives and semantic NER until Part 2 defines Domains 2–5, so the entity model is designed once against the full retrieval and knowledge requirements rather than twice.

One further note on **OPS-01**: while GitHub Actions is not enforced as a merge gate, no acceptance criterion in any part of this proposal can be *mechanically* guaranteed. Evidence would remain advisory. That is an owner action and it gates the credibility of every "Full Testing" disposition in this document.

---

## 6. Proposed disposition register (Part 1)

Using the six dispositions defined in the directive.

| ID | Clause | Finding | Disposition | Cost |
| --- | --- | --- | --- | --- |
| P1-01 | §8, §12 | Evidence lacks processing timestamp | Full Implementation | Low |
| P1-02 | §8, §12 | Evidence lacks app/reader/library version | Full Implementation | Low |
| P1-03 | §6.2, §8 | XLSX/ODS formulas silently discarded | Full Implementation | Low (warn) / Medium (capture) |
| P1-04 | §6.2 | PPTX embedded content dropped | Full Implementation | Low |
| P1-05 | §6.2 | Email `In-Reply-To`/`References` absent | Full Implementation | Low |
| P1-06 | §6.2 | OCR has no confidence tracking | Full Implementation | Low |
| P1-07 | §10 | Immutability is client-side only | Full Immunization | Low |
| P1-08 | §6.2 | Source-code extensions incomplete | Full Implementation | Low |
| P1-09 | §6.2 | Source-code structure not preserved | Decision required | Medium–High |
| P1-10 | §6.2 | Legacy DOC/XLS/PPT unsupported | Decision required | High / or Removal |
| P1-11 | §6.2 | Archives unsupported | Decision required | High |
| P1-12 | §6.2 | Email attachment bytes discarded | Full Implementation (with P1-11) | Medium |
| P1-13 | §11 | People/orgs/locations/concepts absent | Decision required | High |
| P1-14 | §11 | Relationships absent | Decision required | High |
| P1-15 | §11 | Entity confidence field absent | Full Implementation (with P1-13) | Low |

P1-01 through P1-08 and P1-15 are mechanical and carry no architectural risk. P1-09 through P1-14 need decisions before estimation.

---

## 7. Awaiting Part 2

Part 2 is expected to cover retrieval, AI capabilities, chat/agents, model management, Frappe architecture, security, jobs, monitoring, testing, phasing, milestones, and acceptance criteria. Two Part 1 items should be held open until then:

- **The entity model (P1-13, P1-14)** — Domain 2 (knowledge representation, contextual linking, knowledge relationships) will constrain the relationship schema. Designing it against Part 1 alone risks rework.
- **Deterministic-output carve-out (§8)** — if semantic extraction is LLM-based, §8's determinism requirement needs an explicit exception that Part 2's testing strategy must accommodate.
