# Translation

Arabic ⇄ English ⇄ Hebrew translation of extracted text through configured
providers, with segment review and execution records. Translation-memory
isolation and policy identity are enforced (SEC-01/TRN-01). Glossary permission
parity, durable worker restoration, progress, and cancellation remain Phase 4.

Translation is not a prompt wrapped around a document. It is a pipeline that
segments the source, protects everything a model must not touch, translates in
context-sized batches, scores every segment locally, repairs what fails and
reassembles extracted text with its block/spacing structure intact. It does not
reconstruct the original PDF, Office, email, or image binary.

---

## Contents

- [What it does](#what-it-does)
- [Quick start](#quick-start)
- [How a document is translated](#how-a-document-is-translated)
- [Quality gate](#quality-gate)
- [Glossaries](#glossaries)
- [Translation memory](#translation-memory)
- [Reviewing a translation](#reviewing-a-translation)
- [Indexing a translation](#indexing-a-translation)
- [Pipelines](#pipelines)
- [Chat and agents](#chat-and-agents)
- [API](#api)
- [Settings](#settings)
- [Choosing a model](#choosing-a-model)
- [Troubleshooting](#troubleshooting)

---

## What it does

| Capability | Detail |
| --- | --- |
| **Languages** | Arabic (`ar`), English (`en`), Hebrew (`he`) — all six directions, translated directly without pivoting through a third language. |
| **Sources** | Extracted text from 37 registered extensions, subject to optional parser availability. Text-layer PDFs, Office Open XML, OpenDocument (including `.odp`), and `.eml` are supported; scanned-PDF OCR, Outlook `.msg`, OLE `.doc`/`.xls`/`.ppt`, audio, video, and database files are not. |
| **Extracted-text structure** | Text headings, paragraphs, list markers, table rows, indentation, blank lines and `[Page N]` markers are reassembled. Original binary formatting is not reconstructed. |
| **Protected spans** | Numbers, dates, currency figures, URLs, e-mail addresses, file paths, identifiers, code spans, template placeholders and HTML tags are masked and checked after restoration; failed integrity checks flag the segment rather than proving corruption is impossible. |
| **RTL aware** | Arabic and Hebrew presentation forms from PDF extraction are folded to base letters, bidi control characters are stripped, and the output is rendered with the correct direction in Desk. |
| **Terminology control** | A trilingual glossary can force a rendering for a term, or protect a name so it is never translated at all. |
| **Quality scored** | Every segment gets a 0–100 score from local checks: placeholder integrity, target-script purity, residual source text, length ratio, glossary compliance, refusals, and degenerate repetition. |
| **Self-repairing** | A segment that fails review is retried once with a stricter prompt that names the defect, and the retry is kept only when it scores better. |
| **Translation memory** | Reuse requires an authorized knowledge-base scope. No scope means no lookup. Fingerprints include KB plus glossary, tone, and domain policy identity. |
| **Quality signal** | Optional back-translation compares a sample with an embedding model; it is a heuristic, not formal verification. |
| **Network scope** | Calls use configured providers through the guarded transport (SEC-04). Firewall/egress policy remains the actual network boundary. |

---

## Quick start

1. Upload and process a document as usual, so it has extracted text.
2. Open it and choose **Intelligence → Translate…**.
3. Pick the target language, and optionally a glossary, a register and a
   subject domain.
4. The translation is queued on a background worker and opens as an
   **AI Translation** record. Watch the status; review the segments when it
   completes.

From code:

```python
import frappe

result = frappe.call(
    "ai_fr_hg.api.translation.translate_document",
    document="AIDOC-2026-00007",
    target_language="ar",
    tone="Legal",
    index_output=True,
)
# {'translation': 'AITRN-2026-00001', 'status': 'Queued', 'job_id': '...'}
```

---

## How a document is translated

```
extracted text
   ↓ normalise      fold Arabic/Hebrew presentation forms, strip bidi controls,
   ↓                canonicalise line endings and spacing
   ↓ detect         dominant script + function words decide the source language
   ↓ segment        structure-aware extracted-text blocks and separators
   ↓ memory         authorized KB scope only; policy identity in the fingerprint
   ↓ protect        numbers, URLs, IDs, code, page markers, protected terms → [[T0]]
   ↓ translate      batched calls at temperature 0, per-segment fallback
   ↓ restore        placeholders put back, model chatter stripped
   ↓ score          eight local checks per segment
   ↓ repair         one stricter retry for anything flagged, kept only if better
   ↓ verify         optional back-translation similarity via local embeddings
   ↓ reassemble     extracted-text spacing and block structure restored
AI Translation text (not a reconstructed source binary)
```

**Segmentation** splits on blank lines and recognises headings, list blocks,
table blocks, page markers and horizontal rules. Blocks longer than the segment
budget are divided on sentence boundaries — including the Arabic question mark
`؟` and full stop `۔` — and the exact whitespace between pieces is carried on
each segment, so `reassemble()` reproduces the source layout exactly. Page
markers, rules and number-only blocks are copied through untranslated, which
keeps pagination references trustworthy.

**Placeholder protection** replaces untranslatable spans with `[[T0]]`,
`[[T1]]` … sentinels. Masking is applied only to still-unmasked text, so a
sentinel can never be masked again by a later pattern. Restoration tolerates
the mangling small models produce: extra spaces, a localised digit, a Cyrillic
look-alike `Т`. A sentinel that was dropped and whose value never appeared in
the output is reported as a lost placeholder and flags the segment.

**Batching** groups segments up to a character budget and a maximum count. A
batch response is split on its `<<<SEG n>>>` markers, and only requested
indices are accepted, so a hallucinated marker cannot inject text into another
segment. Anything the batch failed to return is retried as an individual call.

---

## Quality gate

Each segment is scored out of 100 by subtracting penalties. Below **70** the
segment is flagged, the document's status becomes **Needs Review**, and the
repair pass gets one attempt at it.

| Issue | Penalty | Meaning |
| --- | --- | --- |
| `empty` | 100 | The model returned nothing. |
| `untranslated` | 45 | The output is the source text. |
| `refusal` | 40 | The model answered or refused instead of translating. |
| `wrong_script` | 35 | The output is not written in the target script. |
| `placeholder_lost` | 35 | A protected number, URL or identifier is missing. |
| `placeholder_unresolved` | 32 | A sentinel was left in the output. |
| `repetition` | 25 | A degenerate loop of one phrase. |
| `source_residue` | 20 | A large amount of source-language text remains. |
| `length_short` | 18 | Much shorter than the source; content may be missing. |
| `length_long` | 12 | Much longer than the source; content may be invented. |
| `glossary` | 12 | A required term rendering is absent. |
| `meta_commentary` | 10 | Notes or explanations that are not the translation. |

Length bounds are direction-specific — Arabic and Hebrew are denser than
English, so `en→he` accepts 0.45–1.60 while `he→en` accepts 0.70–2.40.

The document score is the character-weighted mean of its segments, so one
two-word heading cannot mask a broken twenty-page section, and vice versa.

### Back-translation verification

Set **Back-Translation Samples** above zero to have the longest *n* segments
translated back into the source language and compared to the original with the
local embedding model. Cosine similarity below 0.75 flags the segment for a
human. This costs extra model calls, so it is off by default.

---

## Glossaries

**AI Translation Glossary** holds one concept per row, in all three languages:

| English | Arabic | Hebrew | Keep As Is |
| --- | --- | --- | --- |
| Contractor | المقاول | הקבלן | ☐ |
| Acme Corp | | | ☑ |

- The direction being translated decides which column is the source and which
  is the required output, so one glossary serves all six directions.
- **Keep As Is** protects a term in every direction: it is masked as a
  placeholder before translation, so the model cannot alter it at all.
- Only the terms that actually occur in a segment are sent with it. Shipping a
  whole termbase with every call wastes context and measurably degrades small
  local models.
- A mapped term that is missing from the output raises the `glossary` issue and
  lowers the score.

Attach a glossary per translation, or set a **Default Glossary** in
**AI Platform Settings**.

---

## Translation memory

Every stored segment carries a fingerprint of normalised source text, language
pair, authorized knowledge base, glossary, tone, and domain. Memory reuse:

- no knowledge-base scope performs no lookup (never a global memory scan);
- document, inline, and tool paths pass only an authorized knowledge base;
- a policy change (glossary/tone/domain) produces a different fingerprint and
  does not reuse a prior translation;
- cross-user and cross-KB isolation tests cover these paths.

Turn off **Use Translation Memory** only when you want a fresh model pass.

---

## Reviewing a translation

The **AI Translation** form is built for review:

- **Review Segments** opens a bilingual side-by-side of the whole document,
  each pane rendered in its own text direction, with per-segment status, score
  and issues.
- **Review Flagged Only** narrows it to the segments that failed the gate.
- **Re-translate segment** re-runs one segment, optionally with a human
  instruction such as *"keep the clause numbering"*, rescores it and rebuilds
  the document text.
- **Mark as Reviewed** accepts the translation and clears the flags.
- **Copy Translation** and **Download as Text** export the result.

---

## Indexing a translation

Tick **Index Translation as Document**, or press **Index as Document** on a
finished translation, to store the translated text as its own `AI Document` in
the same knowledge base and folder, titled `Original title [Arabic]`, with its
language already recorded.

That makes the translation searchable, citable and answerable in chat — so an
Arabic-speaking user can query an English corpus in Arabic and get grounded
citations from the Arabic text.

---

## Pipelines

Add a **Translate** step to any `AI Pipeline`:

```json
{
  "target_language": "ar",
  "source_language": "en",
  "tone": "Legal",
  "domain": "construction contracts",
  "glossary": "Group Terminology",
  "return": "text"
}
```

- `target_language` is required; everything else is optional.
- The step translates `input_field` (default `content`) and writes to
  `output_field`.
- `"return": "text"` yields the translated string; omit it to get the full
  result object with `quality_score`, `flagged`, `memory_hits` and the text.

A typical unattended chain is **Extract Text → Translate → Summarize**, or
**Extract Text → Translate → Extract Data** to pull structured fields out of a
document written in a language your schema is not authored in.

---

## Chat and agents

The built-in **`translate_content`** tool lets an agent translate a stored
document or a passage inside a conversation:

> *"Summarise the maintenance agreement in Hebrew."*
> *"ترجم العقد إلى العربية."*

It is a read-only tool: it returns the translation to the conversation without
creating a record. Use the document action when you want a stored, reviewable
translation instead.

---

## API

All endpoints live in `ai_fr_hg.api.translation`.

### `get_languages`

Supported languages and pairs, plus whether translation is enabled.

### `translate`

Translate a passage inline (up to 20 000 characters).

| Parameter | Type | Notes |
| --- | --- | --- |
| `text` | string | Required. |
| `target_language` | string | Required: `ar`, `en` or `he`. |
| `source_language` | string | Detected when omitted. |
| `model`, `glossary`, `tone`, `domain` | string | Optional overrides. |

```json
{
  "text": "…",
  "source_language": "en",
  "target_language": "ar",
  "direction": "rtl",
  "quality_score": 96.4,
  "issues": {},
  "memory_hits": 2,
  "flagged": 0,
  "segment_count": 14,
  "model": "qwen2.5:7b",
  "duration_ms": 18422,
  "total_tokens": 5310
}
```

### `translate_document`

Translate an extracted `AI Document` into a stored `AI Translation`.

| Parameter | Type | Notes |
| --- | --- | --- |
| `document` | string | Required. |
| `target_language` | string | Required. |
| `source_language`, `model`, `glossary`, `tone`, `domain` | string | Optional. |
| `preserve_formatting` | bool | Preserve extracted-text blocks/separators. Does not reconstruct the source file. |
| `index_output` | bool | Also store the result as a searchable document. |
| `background` | bool | Default true. Set false to translate in the request. |

### Other endpoints

| Method | Purpose |
| --- | --- |
| `get_translation(translation, include_segments=True)` | Full record with segments, for review UIs. |
| `list_translations(document, knowledge_base, target_language, limit)` | Translations the user may read. |
| `retranslate(translation, segment_index, instructions)` | Re-run one segment. |
| `index_output(translation)` | Index the finished translation as a document. |
| `get_glossaries(knowledge_base)` | Enabled glossaries. |

---

## Settings

**AI Platform Settings → Knowledge → Translation**

| Setting | Default | Effect |
| --- | --- | --- |
| Enable Translation | on | Master switch for the feature. |
| Default Target Language | `en` | Used when a caller does not name one. |
| Default Translation Model | empty | Falls back to the default chat model. |
| Default Glossary | empty | Applied when a translation names none. |
| Segment Size (characters) | 1800 | How much source text is translated as one unit. Minimum 200. |
| Segments per Model Call | 6 | Batch size. Lower it for small context windows. |
| Run Quality Checks | on | Score and flag every segment. |
| Repair Flagged Segments | on | One stricter retry per flagged segment. |
| Use Translation Memory | on | Disable for production until SEC-01/TRN-01 isolation and policy identity close. |
| Back-Translation Samples | 0 | Segments verified by back-translation. 0 disables it. |
| Index Translations as Documents | off | Default for new translations. |

---

## Choosing a model

Translation quality between Arabic, English and Hebrew depends far more on the
model than on the prompt. On Ollama:

| Model | Notes |
| --- | --- |
| `qwen2.5:7b` / `qwen2.5:14b` | Strong Arabic; the best general starting point. |
| `gemma2:9b` | Good Hebrew and Arabic; heavier on CPU. |
| `aya-expanse:8b` | Built for multilingual work; excellent Arabic. |
| `llama3.1:8b` | Usable English↔Arabic; weaker Hebrew. |

Register the model as an **AI Model** of type *Chat*, then set it as the
**Default Translation Model** so translation can use a different model from
chat.

Translation always runs at temperature 0 and `top_p` 1, whatever the model's
own defaults are: translation is a deterministic task, and sampling is exactly
what makes a model paraphrase a clause it should have translated.

---

## Troubleshooting

**"Could not detect the source language."**
The extracted text has too little signal. Scanned-PDF OCR is not supported;
OCR the PDF before upload. For extractable text, set the source language
explicitly if automatic detection is uncertain.

**"The text is already in Arabic."**
The detector found the target language as the dominant script. For a mixed
document, translate it into the language it is *not* mostly written in, or set
the source language explicitly.

**Many segments flagged `wrong_script`.**
The model is not multilingual enough. Try `qwen2.5:7b` or `aya-expanse:8b`.

**Many segments flagged `length_short`.**
The model is summarising instead of translating. Reduce **Segments per Model
Call**, and check that the model's **Max Tokens** is large enough for the
segment size.

**Status stays Queued.**
Background workers or the scheduler are not running:
`bench --site your-site.local enable-scheduler` and check `bench doctor`.

**Arabic looks reversed or broken in the source pane.**
That is the PDF extractor, not the translation. The platform folds presentation
forms and strips bidi controls before translating, so the *output* is clean
even when the extracted source pane looks odd.
