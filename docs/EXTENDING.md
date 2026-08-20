# Extending the platform

Four hooks let any Frappe app add capability without modifying this one. Each
maps a name to a dotted path, and the platform merges them into its governed
registry at runtime.

```python
# your_app/hooks.py

ai_providers = {"My Runtime": "your_app.ai.providers.MyRuntimeProvider"}
ai_document_readers = {"dwg": "your_app.ai.readers.DWGReader"}
ai_tools = {"lookup_customer": "your_app.ai.tools.lookup_customer"}
ai_pipeline_methods = {"enrich_erp": "your_app.ai.steps.enrich_with_erp_data"}
```

---

## Custom AI providers

Subclass `BaseProvider`. Only `chat`, `embed`, `list_models` and `health_check`
are required; the base class supplies HTTP handling, the local-only guard,
timeouts and error translation.

```python
# your_app/ai/providers.py
import time

from ai_fr_hg.ai.providers.base import (
    BaseProvider,
    ChatMessage,
    CompletionResult,
    HealthStatus,
    ModelInfo,
)


class MyRuntimeProvider(BaseProvider):
    provider_type = "My Runtime"
    supports_streaming = True
    supports_tools = False
    supports_embeddings = True
    supports_model_pull = False

    def chat(self, messages, model, options=None, tools=None, json_schema=None):
        started = time.monotonic()
        response = self.request(
            "POST",
            "/generate",
            {
                "model": model,
                "messages": [m.as_dict() for m in messages],
                **self.build_options(options),
            },
        )
        data = self.parse_json(response)

        return CompletionResult(
            content=data["text"],
            prompt_tokens=data.get("input_tokens", 0),
            completion_tokens=data.get("output_tokens", 0),
            total_tokens=data.get("input_tokens", 0) + data.get("output_tokens", 0),
            duration_ms=int((time.monotonic() - started) * 1000),
            model=model,
            raw=data,
        )

    def embed(self, texts, model, options=None):
        data = self.parse_json(
            self.request("POST", "/embed", {"model": model, "inputs": texts})
        )
        return data["vectors"]

    def list_models(self):
        data = self.parse_json(self.request("GET", "/models"))
        return [
            ModelInfo(name=m["id"], context_window=m.get("ctx", 8192))
            for m in data["models"]
        ]

    def health_check(self):
        started = time.monotonic()
        try:
            models = self.list_models()
            return HealthStatus(
                status="Online",
                latency_ms=int((time.monotonic() - started) * 1000),
                available_models=len(models),
            )
        except Exception as exc:
            return HealthStatus(status="Offline", error=str(exc)[:500])
```

Register it, then create an `AI Provider` record with **Provider Type** set to
`My Runtime`.

For a one-off adapter without a hook, set Provider Type to `Custom` and put the
dotted path in the **Adapter Path** field.

### The contract

| Member | Purpose |
| --- | --- |
| `provider_type` | Must match the value used in the hook and the DocType field. |
| `supports_streaming` / `supports_tools` / `supports_embeddings` / `supports_model_pull` | Adapter declarations used by some paths. Complete effective capability enforcement remains open under PROV-02; adapters must also reject unsupported calls themselves. |
| `chat(...) -> CompletionResult` | Required. Tool calls go in `CompletionResult.tool_calls` as `{"id", "name", "arguments": dict}`. |
| `embed(texts, model, options) -> list[list[float]]` | Required for search. |
| `list_models() -> list[ModelInfo]` | Powers automatic discovery. |
| `health_check() -> HealthStatus` | Powers monitoring and failover. |
| `self.request(method, path, payload)` | Handles URLs, headers, timeouts, TLS and the local-only guard. |
| `self.build_options(overrides)` | Merges model parameters with per-request overrides. |

---

## Custom document readers

Subclass `BaseReader` and declare the extensions it handles.

```python
# your_app/ai/readers.py
from ai_fr_hg.ai.readers.base import BaseReader, ReadResult


class DWGReader(BaseReader):
    label = "AutoCAD Drawing"
    extensions = ("dwg", "dxf")

    def read(self, content: bytes, filename: str) -> ReadResult:
        # `require` raises MissingDependency with an install command,
        # which the UI surfaces to the user verbatim.
        ezdxf = self.require("ezdxf", "pip install ezdxf")

        import io

        doc = ezdxf.read(io.BytesIO(content))
        lines = [
            entity.dxf.text
            for entity in doc.modelspace()
            if entity.dxftype() in ("TEXT", "MTEXT")
        ]

        return ReadResult(
            text="\n".join(lines),
            page_count=1,
            metadata={"format": "dwg", "layers": len(doc.layers)},
        )
```

Once registered, the format works everywhere: upload, ingestion, pipelines,
the supported-formats dialog and automation rules. Nothing else changes.

`ReadResult` carries `text`, `metadata`, `page_count`, `pages` and `warnings`.
Use `warnings` for partial extraction — a scanned page with no text layer, a
password-protected section — rather than raising, so the rest of the document
still indexes.

For paginated formats, emit `[Page N]` markers in the text; the chunker reads
them and records page numbers on each chunk, which then appear in citations.

---

## Custom tools

A tool handler is an ordinary function. Keyword arguments correspond to the
parameters declared on the `AI Tool` record, and the return value must be
JSON-serialisable.

```python
# your_app/ai/tools.py
import frappe


def lookup_customer(name: str, include_orders: bool = False) -> dict:
    """Find a customer and optionally their recent orders."""
    # Always check permissions as the calling user. Never elevate.
    frappe.has_permission("Customer", "read", throw=True)

    customer = frappe.db.get_value(
        "Customer",
        {"customer_name": ["like", f"%{name}%"]},
        ["name", "customer_name", "customer_group", "territory"],
        as_dict=True,
    )
    if not customer:
        return {"found": False, "message": f"No customer matching '{name}'."}

    result = {"found": True, **customer}

    if include_orders:
        result["recent_orders"] = frappe.get_list(
            "Sales Order",
            filters={"customer": customer.name},
            fields=["name", "transaction_date", "grand_total", "status"],
            order_by="transaction_date desc",
            limit_page_length=5,
        )

    return result
```

Then create an `AI Tool` record:

- **Tool Name**: `lookup_customer` (lowercase snake_case — runtimes require it)
- **Tool Type**: `Builtin`
- **Handler**: `lookup_customer`
- **Description**: written for the model, not for a human. State plainly when
  the tool should be used; this text is the model's only guide.
- **Parameters**: `name` (String, required), `include_orders` (Boolean)
- **Read Only**: tick it if the tool never writes, to skip the approval gate.

Finally add the tool to an agent's Tools table.

### Writing descriptions the model can act on

The description is the entire specification the model sees. Compare:

> Gets customer data.

against:

> Look up a customer by name or partial name. Returns their customer group and
> territory. Set include_orders to true when the user asks about recent
> purchases, orders or spending.

The second reliably triggers at the right moment; the first does not.

### Tool types

| Type | Behaviour |
| --- | --- |
| `Builtin` | Calls a registered handler function. |
| `Server Method` | Calls a whitelisted dotted path. Non-whitelisted methods are refused. |
| `DocType Query` | Generic read against a DocType, filtered to valid fields, capped at 100 rows. |
| `DocType Action` | Create or update a record. Always subject to the approval gate. |
| `Report` | Runs a query report and returns columns and rows. |
| `Pipeline` | Runs an AI Pipeline synchronously and returns its output. |

---

## Custom pipeline steps

For logic that does not fit the built-in step types, use a `Custom Method` step.

```python
# your_app/ai/steps.py

def enrich_with_erp_data(context, step, config):
    """Pipeline step: attach ERP context to the run.

    context - the shared run dictionary; earlier steps wrote their output here
    step    - the AI Pipeline Step row, for model, input_field, output_field
    config  - the step's parsed JSON configuration
    """
    import frappe

    document = context.get("document")
    supplier = (context.get("extracted") or {}).get("supplier_name")
    if not supplier:
        return {"matched": False}

    match = frappe.db.get_value(
        "Supplier", {"supplier_name": ["like", f"%{supplier}%"]}, "name"
    )
    return {"matched": bool(match), "supplier": match, "document": document}
```

Register the dotted path in the extension app's `ai_pipeline_methods` hook,
then set the step's **Method** to
`your_app.ai.steps.enrich_with_erp_data`. Unregistered methods are rejected at
both configuration and execution time. The callable runs as the user who
started the Pipeline and must use permission-aware Frappe APIs; it must never
elevate privileges.

The return value is written into the run context under the step's **Output
Field**, where later steps read it via their **Input Field**.

---

## Adding AI to your own forms

`frappe.ai` is available on every Desk page.

```javascript
frappe.ui.form.on("Sales Order", {
    refresh(frm) {
        // Adds a ready-made "Ask AI" button.
        frappe.ai.add_form_button(frm);

        // Or drive it yourself.
        frm.add_custom_button(__("Check Against Policy"), async () => {
            await frappe.ai.ask(
                `Does this order comply with our discount policy?\n\n` +
                `Order ${frm.doc.name}, total ${frm.doc.grand_total}, ` +
                `discount ${frm.doc.discount_amount}`,
                { knowledge_bases: ["Company Policies"] }
            );
        });
    },
});
```

From Python, call the service layer directly:

```python
from ai_fr_hg.ai.agent import run_agent_turn
from ai_fr_hg.ai.intelligence import classify, extract_data, summarize
from ai_fr_hg.ai.knowledge import retrieve

answer = run_agent_turn("What is our refund window?", save_messages=False)
summary = summarize(long_text, max_words=200)
category = classify(text, categories=["Invoice", "Contract", "Report"])
fields = extract_data(text, schema="Invoice Data")
passages = retrieve("refund policy", top_k=5)
```

From Jinja, in a print format or notification:

```jinja
{{ get_ai_summary("Sales Order", doc.name) }}

{% for hit in ai_search("refund policy", limit=3) %}
  <p>{{ hit.content }}</p>
{% endfor %}
```

---

## Automating without writing code

Most automation needs no Python at all. Create an **AI Automation Rule**:

- **Document Type**: `Purchase Invoice`
- **Event**: `after_insert`
- **Condition**: `doc.grand_total > 10000` (optional, safely evaluated)
- **Action**: `Extract Data` with an extraction schema
- **Target Field**: where the result is written back

The rule runs on a background worker, records its own success and failure
counts, and can be tested against an existing document from the form before
you enable it.
