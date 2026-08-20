# Copyright (c) 2026, Ai Fr Hg and contributors
# For license information, please see license.txt

"""Document intelligence operations.

Summarisation, classification, structured extraction and comparison. Long
documents are handled with a map-reduce strategy so content larger than the
model's context window is still processed accurately.
"""

import json
import re

import frappe
from frappe import _
from frappe.utils import cint, flt

from ai_fr_hg.ai.chunking import chunk_text, estimate_tokens
from ai_fr_hg.ai.engine import resolve_model, run_chat
from ai_fr_hg.ai.exceptions import HierarchicalReductionError
from ai_fr_hg.ai.validation import ValidationError as _ValidationError, validate_extraction

JSON_BLOCK = re.compile(r"```(?:json)?\s*(.+?)\s*```", re.DOTALL)

TYPE_MAP = {
	"String": "string",
	"Number": "number",
	"Integer": "integer",
	"Boolean": "boolean",
	"Date": "string",
	"Array": "array",
	"Object": "object",
}


def parse_json_response(text: str) -> dict | list | None:
	"""Best-effort extraction of a JSON value from a model response."""
	if not text:
		return None

	candidate = text.strip()
	if match := JSON_BLOCK.search(candidate):
		candidate = match.group(1).strip()

	try:
		return json.loads(candidate)
	except ValueError:
		pass

	# Fall back to the outermost balanced object or array in the text.
	for opener, closer in (("{", "}"), ("[", "]")):
		start = candidate.find(opener)
		end = candidate.rfind(closer)
		if start != -1 and end > start:
			try:
				return json.loads(candidate[start : end + 1])
			except ValueError:
				continue
	return None


def _context_budget(model_doc) -> int:
	"""Characters of input the model can comfortably accept in one call."""
	window = cint(model_doc.num_ctx_override) or cint(model_doc.context_window) or 8192
	# Reserve roughly a third of the window for the prompt and the answer.
	return max(int(window * 0.6) * 4, 2000)


# ---------------------------------------------------------------------------
# Summarisation
# ---------------------------------------------------------------------------

SUMMARY_PROMPT = (
	"Summarise the following text. Capture the key facts, decisions, figures and "
	"conclusions. Be faithful to the source and do not add information.\n\n"
	"{instructions}\n\nTEXT:\n{text}"
)

REDUCE_PROMPT = (
	"The following are summaries of consecutive sections of one document. "
	"Combine them into a single coherent summary without repetition.\n\n"
	"{instructions}\n\nSECTION SUMMARIES:\n{text}"
)


def summarize(
	text: str,
	model: str | None = None,
	instructions: str = "",
	max_words: int = 0,
	reference_doctype: str | None = None,
	reference_name: str | None = None,
) -> str:
	"""Summarise text, using map-reduce when it exceeds the context window."""
	if not (text or "").strip():
		return ""

	model_doc = resolve_model(model, "Chat")
	budget = _context_budget(model_doc)

	guidance = instructions or ""
	if max_words:
		guidance = f"{guidance}\nKeep the summary under {max_words} words.".strip()

	if len(text) <= budget:
		result = run_chat(
			[{"role": "user", "content": SUMMARY_PROMPT.format(instructions=guidance, text=text)}],
			model=model_doc.name,
			operation="Summarize",
			reference_doctype=reference_doctype,
			reference_name=reference_name,
		)
		return result.content.strip()

	# Map: summarise each window, then reduce the partial summaries.
	windows = chunk_text(text, chunk_size=budget, chunk_overlap=200)
	partials = []
	for window in windows:
		result = run_chat(
			[
				{
					"role": "user",
					"content": SUMMARY_PROMPT.format(instructions=guidance, text=window.content),
				}
			],
			model=model_doc.name,
			operation="Summarize",
			reference_doctype=reference_doctype,
			reference_name=reference_name,
		)
		partials.append(result.content.strip())

	# INT-03: hierarchical coverage-preserving reduction — never truncate tail summaries.
	# Provenance: each partial retains its source window index; intermediate reduces preserve ordering.
	def _hierarchical_reduce(summaries: list[str], level: int = 0) -> str:
		if len(summaries) == 1:
			return summaries[0]
		# Pack summaries into budget-respecting batches, then reduce each batch.
		batches: list[list[str]] = []
		cur: list[str] = []
		cur_len = 0
		for idx, s in enumerate(summaries):
			entry = f"[Section {idx+1}]\n{s}"
			if cur and cur_len + len(entry) + 2 > budget:
				batches.append(cur)
				cur = [entry]
				cur_len = len(entry)
			else:
				cur.append(entry)
				cur_len += len(entry) + 2
		if cur:
			batches.append(cur)
		if len(batches) == 1 and len("\n\n".join(batches[0])) <= budget:
			combined = "\n\n".join(batches[0])
			result = run_chat(
				[{"role": "user", "content": REDUCE_PROMPT.format(instructions=guidance, text=combined)}],
				model=model_doc.name,
				operation="Summarize",
				reference_doctype=reference_doctype,
				reference_name=reference_name,
			)
			return result.content.strip()
			# INT-03: Bounded recursion — never silently discard to fit budget
		if level > 10:
			raise HierarchicalReductionError(
				f"Hierarchical reduction exceeded 10 levels ({len(summaries)} summaries, budget {budget}). "
				f"Input too large for safe reduction — failing explicitly rather than truncating."
			)
		next_level: list[str] = []
		for batch in batches:
			combined = "\n\n".join(batch)
			result = run_chat(
				[{"role": "user", "content": REDUCE_PROMPT.format(instructions=guidance, text=combined)}],
				model=model_doc.name,
				operation="Summarize",
				reference_doctype=reference_doctype,
				reference_name=reference_name,
			)
			next_level.append(result.content.strip())
		return _hierarchical_reduce(next_level, level+1)

	return _hierarchical_reduce(partials)


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

CLASSIFY_PROMPT = (
	"Classify the text into exactly one of these categories:\n{categories}\n\n"
	"{instructions}\n\n"
	'Respond with JSON only: {{"category": "<category>", "confidence": <0-100>, '
	'"reason": "<one short sentence>"}}\n\nTEXT:\n{text}'
)


def classify(
	text: str,
	categories: list[str],
	model: str | None = None,
	instructions: str = "",
	reference_doctype: str | None = None,
	reference_name: str | None = None,
) -> dict:
	"""Assign one of `categories` to the text, with a confidence score. Whole-document chunk-and-vote."""
	if not categories:
		frappe.throw(_("Classification needs at least one category."))

	model_doc = resolve_model(model, "Chat")
	budget = _context_budget(model_doc)

	source_chars = len(text or "")
	if not (text or "").strip():
		return {"category": None, "confidence": 0, "reason": "empty", "raw": "", "coverage": {"source_chars": 0, "processed_chars": 0, "windows_total": 0, "windows_processed": 0, "windows_failed": 0, "coverage_ratio": 0, "strategy": "single_pass", "provenance": []}}
	windows = chunk_text(text, chunk_size=budget, chunk_overlap=200) if len(text) > budget else [type("W", (), {"content": text, "index": 0})()]
	# provenance tracking: discovered vs submitted vs processed vs failed
	windows_total = len(windows)
	windows_processed = 0
	windows_failed = 0
	processed_chars = 0
	votes: list[dict] = []
	provenance = []
	for w in windows:
		content = w.content if hasattr(w, "content") else str(w)
		prompt = CLASSIFY_PROMPT.format(
			categories="\n".join(f"- {c}" for c in categories),
			instructions=instructions,
			text=content,
		)
		schema = {
			"type": "object",
			"properties": {
				"category": {"type": "string", "enum": list(categories)},
				"confidence": {"type": "number"},
				"reason": {"type": "string"},
			},
			"required": ["category"],
		}
		try:
			result = run_chat(
				[{"role": "user", "content": prompt}],
				model=model_doc.name,
				options={"temperature": 0, "json_mode": True},
				json_schema=schema,
				operation="Classify",
				reference_doctype=reference_doctype,
				reference_name=reference_name,
			)
			parsed = parse_json_response(result.content) or {}
			cat = parsed.get("category")
			if cat not in categories:
				lowered = {c.lower(): c for c in categories}
				cat = lowered.get(str(cat).lower().strip()) if cat else None
				if not cat:
					for candidate in categories:
						if candidate.lower() in (result.content or "").lower():
							cat = candidate
							break
			if cat in categories:
				votes.append({"category": cat, "confidence": flt(parsed.get("confidence")), "reason": parsed.get("reason"), "raw": result.content})
				windows_processed += 1
				processed_chars += len(content)
				provenance.append({"window_index": getattr(w, "index", 0), "chars": len(content), "category": cat, "status": "processed"})
			else:
				windows_failed += 1
				provenance.append({"window_index": getattr(w, "index", 0), "chars": len(content), "status": "failed", "reason": "invalid_category"})
		except Exception as e:
			windows_failed += 1
			provenance.append({"window_index": getattr(w, "index", 0), "chars": len(content), "status": "failed", "error": str(e)[:200]})
	# deterministic aggregation
	if not votes:
		return {"category": None, "confidence": 0, "reason": "no window classified", "raw": "", "coverage": {"source_chars": source_chars, "processed_chars": processed_chars, "windows_total": windows_total, "windows_processed": windows_processed, "windows_failed": windows_failed, "coverage_ratio": round(processed_chars/source_chars, 4) if source_chars else 0, "strategy": "chunk_vote", "provenance": provenance}}
	# count votes
	from collections import Counter
	counter = Counter(v["category"] for v in votes)
	most_common = counter.most_common()
	max_count = most_common[0][1]
	tied = [cat for cat,cnt in most_common if cnt == max_count]
	if len(tied) == 1:
		winner = tied[0]
	else:
		# tie: pick highest avg confidence among tied, deterministic by sorted name
		avgs = {}
		for cat in tied:
			conf = [v["confidence"] for v in votes if v["category"]==cat]
			avgs[cat] = sum(conf)/len(conf) if conf else 0
		winner = sorted(tied, key=lambda c: (-avgs[c], c))[0]
	winner_votes = [v for v in votes if v["category"]==winner]
	avg_conf = sum(v["confidence"] for v in winner_votes)/len(winner_votes) if winner_votes else 0
	# if low confidence or high failure, mark degraded but still return winner
	return {
		"category": winner,
		"confidence": avg_conf,
		"reason": winner_votes[0]["reason"] if winner_votes else "",
		"raw": winner_votes[0]["raw"] if winner_votes else "",
		"coverage": {"source_chars": source_chars, "processed_chars": processed_chars, "windows_total": windows_total, "windows_processed": windows_processed, "windows_failed": windows_failed, "coverage_ratio": round(processed_chars/source_chars, 4) if source_chars else 0, "strategy": "chunk_vote", "provenance": provenance},
	}


# ---------------------------------------------------------------------------
# Structured extraction
# ---------------------------------------------------------------------------


def build_json_schema(schema_doc) -> dict:
	"""Turn an `AI Extraction Schema` into a JSON Schema object."""
	properties: dict = {}
	required: list[str] = []

	for row in schema_doc.get("extraction_fields") or []:
		field: dict = {"type": TYPE_MAP.get(row.field_type, "string")}
		if row.description:
			field["description"] = row.description
		if row.field_type == "Date":
			field["description"] = f"{field.get('description', '')} Format: YYYY-MM-DD.".strip()
		if row.enum_values:
			values = [v.strip() for v in row.enum_values.replace(",", "\n").splitlines() if v.strip()]
			if values:
				field["enum"] = values
		if field["type"] == "array":
			field["items"] = {"type": "string"}
		properties[row.field_name] = field
		if row.required:
			required.append(row.field_name)

	schema = {"type": "object", "properties": properties}
	if required:
		schema["required"] = required
	if schema_doc.strict:
		schema["additionalProperties"] = False
	return schema


EXTRACT_PROMPT = (
	"Extract the requested fields from the text below.\n"
	"Use null for any field that is not present. Do not guess.\n\n"
	"{instructions}\n\nFIELDS:\n{fields}\n\nTEXT:\n{text}"
)


def extract_data(
	text: str,
	schema: str,
	model: str | None = None,
	reference_doctype: str | None = None,
	reference_name: str | None = None,
) -> dict:
	"""Extract structured data from text using an `AI Extraction Schema`. Whole-document map/merge with per-window INT-02 validation."""
	schema_doc = frappe.get_cached_doc("AI Extraction Schema", schema)
	if not schema_doc.enabled:
		frappe.throw(_("Extraction Schema {0} is disabled.").format(schema))

	model_doc = resolve_model(model or schema_doc.model, "Chat")
	budget = _context_budget(model_doc)
	json_schema = build_json_schema(schema_doc)

	field_lines = []
	for row in schema_doc.get("extraction_fields") or []:
		line = f"- {row.field_name} ({row.field_type})"
		if row.label:
			line += f" - {row.label}"
		if row.description:
			line += f": {row.description}"
		if row.required:
			line += " [required]"
		field_lines.append(line)

	source_chars = len(text or "")
	if not (text or "").strip():
		return {}

	# Short path: single window
	if len(text) <= budget:
		prompt = EXTRACT_PROMPT.format(instructions=schema_doc.instructions or "", fields="\n".join(field_lines), text=text)
		result = run_chat([{"role": "user", "content": prompt}], model=model_doc.name, options={"temperature": 0, "json_mode": True}, json_schema=json_schema, operation="Extract", reference_doctype=reference_doctype, reference_name=reference_name)
		data = parse_json_response(result.content)
		if data is None:
			raise _ValidationError("Model did not return valid JSON.", errors=[{"field":"","code":"malformed_json","message":"Model output is not valid JSON","severity":"error"}], provenance={"schema": schema_doc.name, "strict": bool(schema_doc.strict), "raw_preview": (result.content or "")[:500]})
		if not isinstance(data, dict):
			raise _ValidationError("Model did not return a JSON object.", errors=[{"field":"","code":"type","message":f"Expected object got {type(data).__name__}","severity":"error"}], provenance={"schema": schema_doc.name, "strict": bool(schema_doc.strict), "raw_preview": (result.content or "")[:500]})
		ok, errors = validate_extraction(data, schema_doc)
		if not ok:
			raise _ValidationError(f"Structured output failed schema validation: {errors[0]['message'] if errors else 'invalid'}", errors=errors, provenance={"schema": schema_doc.name, "strict": bool(schema_doc.strict), "payload_bytes": len((result.content or "").encode("utf-8")), "field_count": len(data)})
		coerced = _coerce_types(data, schema_doc)
		ok2, errors2 = validate_extraction(coerced, schema_doc)
		if not ok2:
			raise _ValidationError(f"Coerced output failed validation: {errors2[0]['message']}", errors=errors2, provenance={"schema": schema_doc.name, "strict": bool(schema_doc.strict)})
		# attach coverage for uniform API
		coerced["_coverage"] = {"source_chars": source_chars, "processed_chars": source_chars, "windows_total": 1, "windows_processed": 1, "windows_failed": 0, "coverage_ratio": 1.0, "strategy": "single_pass", "provenance": [{"window_index": 0, "chars": source_chars, "status": "processed"}]}
		return coerced

	# Long document: map/merge
	windows = chunk_text(text, chunk_size=budget, chunk_overlap=200)
	windows_total = len(windows)
	windows_processed = 0
	windows_failed = 0
	processed_chars = 0
	per_window_results: list[dict] = []
	provenance = []
	for idx, w in enumerate(windows):
		content = w.content
		prompt = EXTRACT_PROMPT.format(instructions=schema_doc.instructions or "", fields="\n".join(field_lines), text=content)
		try:
			result = run_chat([{"role": "user", "content": prompt}], model=model_doc.name, options={"temperature": 0, "json_mode": True}, json_schema=json_schema, operation="Extract", reference_doctype=reference_doctype, reference_name=reference_name)
			data = parse_json_response(result.content)
			if data is None or not isinstance(data, dict):
				raise _ValidationError("Invalid JSON", errors=[{"field":"","code":"malformed_json","message":"Window invalid JSON"}], provenance={"window": idx})
			ok, errors = validate_extraction(data, schema_doc)
			if not ok:
				raise _ValidationError(f"Window validation failed", errors=errors, provenance={"window": idx})
			coerced = _coerce_types(data, schema_doc)
			ok2, errors2 = validate_extraction(coerced, schema_doc)
			if not ok2:
				raise _ValidationError(f"Coerced window failed", errors=errors2, provenance={"window": idx})
			per_window_results.append(coerced)
			windows_processed += 1
			processed_chars += len(content)
			provenance.append({"window_index": idx, "chars": len(content), "status": "processed"})
		except _ValidationError as ve:
			windows_failed += 1
			provenance.append({"window_index": idx, "chars": len(content), "status": "failed", "error": str(ve)[:200], "errors": getattr(ve, "errors", [])})
		except Exception as e:
			windows_failed += 1
			provenance.append({"window_index": idx, "chars": len(content), "status": "failed", "error": str(e)[:200]})
	if not per_window_results:
		raise _ValidationError("All windows failed validation", errors=[{"field":"","code":"all_windows_failed","message":"No window produced valid extraction"}], provenance={"windows_total": windows_total, "windows_failed": windows_failed})
	# Deterministic merge
	merged: dict = {}
	merge_conflicts: list[dict] = []
	for row in schema_doc.get("extraction_fields") or []:
		fname = row.field_name
		values = [r.get(fname) for r in per_window_results if r.get(fname) not in (None, "")]
		if not values:
			merged[fname] = None
			continue
		uniq = []
		for v in values:
			if v not in uniq:
				uniq.append(v)
		if len(uniq) == 1:
			merged[fname] = uniq[0]
		else:
			# conflict: choose most frequent, deterministic by value str, or null if no confidence
			from collections import Counter
			cnt = Counter(str(v) for v in values)
			most = cnt.most_common(1)[0][0]
			# map back to original value with that string
			winner = next(v for v in uniq if str(v)==most)
			# if tie with different values and no confidence, prefer null + provenance
			if len(uniq) > 1 and cnt[most] == 1 and len(values) > 1:
				# ambiguous — expose conflict, set None to avoid fabricated certainty
				merged[fname] = None
				merge_conflicts.append({"field": fname, "values": uniq, "chosen": None})
			else:
				merged[fname] = winner
				if len(uniq) > 1:
					merge_conflicts.append({"field": fname, "values": uniq, "chosen": winner})
	# Final validation before return/persist
	okf, errorsf = validate_extraction(merged, schema_doc)
	if not okf:
		# If merged fails due to missing required but per-window had it, try to fill from any window
		raise _ValidationError(f"Merged output failed validation: {errorsf[0]['message']}", errors=errorsf, provenance={"merge_conflicts": merge_conflicts, "provenance": provenance})
	merged["_coverage"] = {"source_chars": source_chars, "processed_chars": processed_chars, "windows_total": windows_total, "windows_processed": windows_processed, "windows_failed": windows_failed, "coverage_ratio": round(processed_chars/source_chars, 4) if source_chars else 0, "strategy": "map_merge", "provenance": provenance, "merge_conflicts": merge_conflicts}
	return merged


def _coerce_types(data: dict, schema_doc) -> dict:
	"""Coerce extracted values to the declared field types."""
	from frappe.utils import getdate

	coerced: dict = {}
	for row in schema_doc.get("extraction_fields") or []:
		value = data.get(row.field_name)
		if value in (None, "", "null", "N/A"):
			coerced[row.field_name] = None
			continue

		try:
			if row.field_type == "Number":
				coerced[row.field_name] = flt(value)
			elif row.field_type == "Integer":
				coerced[row.field_name] = cint(value)
			elif row.field_type == "Boolean":
				coerced[row.field_name] = (
					value if isinstance(value, bool) else str(value).lower() in ("1", "true", "yes")
				)
			elif row.field_type == "Date":
				coerced[row.field_name] = str(getdate(value))
			elif row.field_type == "Array":
				coerced[row.field_name] = value if isinstance(value, list) else [value]
			else:
				coerced[row.field_name] = value
		except Exception:
			coerced[row.field_name] = value
	return coerced


# ---------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------

COMPARE_PROMPT = (
	"Compare the two documents below.\n\n{instructions}\n\n"
	"Report:\n"
	"1. Key differences\n"
	"2. Key similarities\n"
	"3. Content present in A but missing from B\n"
	"4. Content present in B but missing from A\n\n"
	"=== DOCUMENT A: {title_a} ===\n{text_a}\n\n"
	"=== DOCUMENT B: {title_b} ===\n{text_b}"
)


def compare_documents(
	document_a: str, document_b: str, model: str | None = None, instructions: str = ""
) -> dict:
	"""Compare two `AI Document` records whole-document with coverage. Window alignment is for budgeting only, not semantic equivalence."""
	doc_a = frappe.get_doc("AI Document", document_a)
	doc_b = frappe.get_doc("AI Document", document_b)
	doc_a.check_permission("read")
	doc_b.check_permission("read")

	model_doc = resolve_model(model, "Chat")
	budget = _context_budget(model_doc) // 2
	text_a = doc_a.content or ""
	text_b = doc_b.content or ""
	source_chars_a = len(text_a)
	source_chars_b = len(text_b)
	# Short path
	if len(text_a) <= budget and len(text_b) <= budget:
		prompt = COMPARE_PROMPT.format(instructions=instructions, title_a=doc_a.title, text_a=text_a, title_b=doc_b.title, text_b=text_b)
		result = run_chat([{"role": "user", "content": prompt}], model=model_doc.name, operation="Compare", reference_doctype="AI Document", reference_name=document_a)
		return {"document_a": document_a, "document_b": document_b, "comparison": result.content, "model": model_doc.name, "coverage": {"source_chars_a": source_chars_a, "source_chars_b": source_chars_b, "processed_chars_a": source_chars_a, "processed_chars_b": source_chars_b, "windows_total_a": 1, "windows_total_b": 1, "windows_processed_a": 1, "windows_processed_b": 1, "windows_failed": 0, "coverage_ratio_a": 1.0, "coverage_ratio_b": 1.0, "coverage_ratio": 1.0, "strategy": "single_pass", "provenance": []}}
	# Long: chunk each doc, pair windows sequentially for budgeting
	wins_a = chunk_text(text_a, chunk_size=budget, chunk_overlap=200) if len(text_a) > budget else [type("W", (), {"content": text_a, "index": 0})()]
	wins_b = chunk_text(text_b, chunk_size=budget, chunk_overlap=200) if len(text_b) > budget else [type("W", (), {"content": text_b, "index": 0})()]
	max_wins = max(len(wins_a), len(wins_b))
	per_window_comparisons: list[str] = []
	provenance = []
	windows_failed = 0
	processed_a = 0
	processed_b = 0
	for i in range(max_wins):
		wa = wins_a[i] if i < len(wins_a) else wins_a[-1]
		wb = wins_b[i] if i < len(wins_b) else wins_b[-1]
		ca = wa.content if hasattr(wa, "content") else str(wa)
		cb = wb.content if hasattr(wb, "content") else str(wb)
		prompt = COMPARE_PROMPT.format(instructions=instructions, title_a=doc_a.title, text_a=ca, title_b=doc_b.title, text_b=cb)
		try:
			result = run_chat([{"role": "user", "content": prompt}], model=model_doc.name, operation="Compare", reference_doctype="AI Document", reference_name=document_a)
			per_window_comparisons.append(result.content.strip())
			processed_a += len(ca) if i < len(wins_a) else 0
			processed_b += len(cb) if i < len(wins_b) else 0
			provenance.append({"window_index": i, "chars_a": len(ca), "chars_b": len(cb), "status": "processed"})
		except Exception as e:
			windows_failed += 1
			provenance.append({"window_index": i, "chars_a": len(ca), "chars_b": len(cb), "status": "failed", "error": str(e)[:200]})
	if not per_window_comparisons:
		raise frappe.ValidationError("All window comparisons failed")
	# Synthesize via hierarchical reduce similar to summarize but for comparison
	if len(per_window_comparisons) == 1:
		final = per_window_comparisons[0]
	else:
		# Reuse hierarchical logic: pack and reduce
		combined = "\n\n".join(f"[Window {i+1}]\n{c}" for i,c in enumerate(per_window_comparisons))
		# If still too large, hierarchically reduce
		if len(combined) > budget*2:
			# simple hierarchical: batch reduce
			batches = []
			cur = []
			cur_len = 0
			for idx, c in enumerate(per_window_comparisons):
				entry = f"[Window {idx+1}]\n{c}"
				if cur and cur_len + len(entry) > budget*2:
					batches.append(cur); cur=[entry]; cur_len=len(entry)
				else: cur.append(entry); cur_len+=len(entry)
			if cur: batches.append(cur)
			next_level=[]
			for batch in batches:
				combined_batch = "\n\n".join(batch)
				result = run_chat([{"role": "user", "content": f"Combine these per-window comparisons into one coherent comparison.\n\n{combined_batch}"}], model=model_doc.name, operation="Compare", reference_doctype="AI Document", reference_name=document_a)
				next_level.append(result.content.strip())
			combined = "\n\n".join(next_level)
			result = run_chat([{"role": "user", "content": f"Synthesize final comparison from:\n{combined}"}], model=model_doc.name, operation="Compare", reference_doctype="AI Document", reference_name=document_a)
			final = result.content.strip()
		else:
			result = run_chat([{"role": "user", "content": f"Synthesize these per-window comparisons into one final comparison:\n{combined}"}], model=model_doc.name, operation="Compare", reference_doctype="AI Document", reference_name=document_a)
			final = result.content.strip()
	coverage_ratio_a = round(processed_a/source_chars_a,4) if source_chars_a else 0
	coverage_ratio_b = round(processed_b/source_chars_b,4) if source_chars_b else 0
	return {
		"document_a": document_a,
		"document_b": document_b,
		"comparison": final,
		"model": model_doc.name,
		"coverage": {"source_chars_a": source_chars_a, "source_chars_b": source_chars_b, "processed_chars_a": processed_a, "processed_chars_b": processed_b, "windows_total_a": len(wins_a), "windows_total_b": len(wins_b), "windows_processed_a": len(wins_a)-windows_failed if windows_failed < len(wins_a) else 0, "windows_processed_b": len(wins_b)-windows_failed if windows_failed < len(wins_b) else 0, "windows_failed": windows_failed, "coverage_ratio_a": coverage_ratio_a, "coverage_ratio_b": coverage_ratio_b, "coverage_ratio": round(min(coverage_ratio_a, coverage_ratio_b),4), "strategy": "windowed_synthesis", "provenance": provenance, "note": "window alignment is for computational budgeting, not semantic section equivalence"},
	}


def render_prompt_template(template: str, context: dict) -> dict:
	"""Render an `AI Prompt Template` against a context dictionary."""
	from frappe.utils.jinja import render_template

	template_doc = frappe.get_cached_doc("AI Prompt Template", template)
	template_doc.check_permission("read")
	if not template_doc.enabled:
		frappe.throw(_("Prompt Template {0} is disabled.").format(template))

	merged = dict(context or {})
	for row in template_doc.get("variables") or []:
		if row.variable not in merged and row.default_value is not None:
			merged[row.variable] = row.default_value
		if row.required and merged.get(row.variable) in (None, ""):
			frappe.throw(_("Prompt variable {0} is required.").format(row.variable))

	return {
		# Prompt templates are manager-authored and rendered by Frappe's sandboxed Jinja environment.
		"system_prompt": render_template(template_doc.system_prompt or "", merged),  # nosemgrep: frappe-ssti
		"user_prompt": render_template(template_doc.user_prompt or "", merged),  # nosemgrep: frappe-ssti
		"model": template_doc.model,
		"output_format": template_doc.output_format,
		"json_schema": json.loads(template_doc.json_schema) if template_doc.json_schema else None,
	}


def run_prompt_template(
	template: str,
	context: dict | None = None,
	model: str | None = None,
	reference_doctype: str | None = None,
	reference_name: str | None = None,
) -> dict:
	"""Render and execute a prompt template."""
	rendered = render_prompt_template(template, context or {})

	messages = []
	if rendered["system_prompt"]:
		messages.append({"role": "system", "content": rendered["system_prompt"]})
	messages.append({"role": "user", "content": rendered["user_prompt"]})

	options = {"json_mode": True} if rendered["output_format"] == "JSON" else None
	result = run_chat(
		messages,
		model=model or rendered["model"],
		options=options,
		json_schema=rendered["json_schema"],
		operation="Chat",
		reference_doctype=reference_doctype,
		reference_name=reference_name,
	)

	output = result.content
	if rendered["output_format"] == "JSON":
		output = parse_json_response(result.content)

	return {
		"output": output,
		"raw": result.content,
		"total_tokens": result.total_tokens,
		"duration_ms": result.duration_ms,
	}
