# The Learning Loop

The platform can be **taught**. A human corrects an answer, writes an explicit
instruction, or the system captures feedback, and that becomes a *knowledge
candidate*. The candidate is validated for provenance, tested for conflicts
against the existing store, then — through a controlled approval gate —
becomes a persistent **AI Memory** or **AI Skill**. Those are injected into
future turns so the model behaves better next time, and the outcome of that
behaviour is observed and fed back into the loop.

This turns the system from *"AI that can read my files"* into *"AI that can
acquire knowledge, acquire skills, remember experience, accept instruction,
reason over evidence, and improve in a controlled and auditable manner."* The
LLM remains one component; the intelligence lives in the combination of
models + knowledge + memory + skills + tools + retrieval + learning +
verification + execution.

```
 User teaches
     ▼
 Knowledge candidate ───────── create_candidate()
     ▼
 Validate provenance ────────── validate_candidate()
     ▼
 Test against existing data ─── check_conflicts()   (dedupe / overlap)
     ▼
 Approved knowledge ─────────── approve_candidate() (AI Manager / System Manager)
     ▼
 Memory / skill update ──────── _promote_to_memory() / _promote_to_skill()
     ▼
 Future AI behaviour ────────── recall() → injected into the agent system prompt
     ▼
 Observe result ─────────────── observe_feedback() + usage tracking
     │
     └────────────────────────► back to the top
```

---

## Stages, mapped to code

### 1 + 2. User teaches → knowledge candidate

A candidate is any piece of teaching the platform should adopt:

- `Explicit Teaching` — a user writes "always cite sources" or "our refund
  period is thirty days".
- `Chat Correction` — a user rates an answer Not Helpful; the answer is
  captured for review.
- `Document` / `Tool Result` / `Automation` — knowledge extracted from another
  step, always carrying a reference back to its origin.

`create_candidate()` infers the type from the content when not supplied
(`learning_utils.classify_candidate`):

| Candidate type | Becomes | Example |
| --- | --- | --- |
| `Fact` / `Preference` / `Feedback` | `AI Memory` | "The refund period is thirty days." |
| `Instruction` | `AI Skill` | "Always use Markdown tables when comparing options." |

Candidates carry a target scope (`Global`, `User`, `Role`, or `Agent`) through
promotion. Preferences, feedback, and chat corrections default to the teaching
user; other types default to global review. Non-global values are validated
against Frappe users, roles, and agents before the candidate is accepted.

### 3. Validate provenance

`validate_candidate()` confirms the record is learnable: non-empty content, a
valid type, a recorded teaching user and source. Document and tool sources must
also reference an originating record. This provides provenance on covered
paths; OPS-05 tracks remaining end-to-end execution/audit links.

### 4. Test against existing data

`check_conflicts()` compares the candidate against active memories and skills:

- **duplicates** — the store already contains essentially the same knowledge
  (token-set Jaccard above 0.85 after stop-word removal and light stemming).
  A duplicate candidate is held at `Conflict` for a human decision rather than
  silently re-added.
- **overlaps** — meaningfully similar but not identical, surfaced for review.

### 5 + 6. Approval → memory / skill update

`approve_candidate()` is restricted to **AI Manager / System Manager**. On
approval it creates an `AI Memory` (optionally embedded) or an `AI Skill`, and
writes an `AI Audit Log` entry. Promotion is idempotent and serialized, so two
approval requests cannot create duplicate learned records. `reject_candidate()`
records the decision so it is never learned. **`Require Approval for Learned
Knowledge`** (AI Platform Settings → Learning) is on by default. If an
administrator deliberately disables it, conflict-free candidates are promoted
by policy; duplicates and overlaps still stop at `Conflict` for review.

### 7. Future AI behaviour

Each turn, `run_agent_turn` calls `prepare_memory_context(prompt, agent)` →
`recall()`. Active memories are filtered to the caller's scope (Global / User /
Role / Agent), ranked by relevance to the question (coverage + Jaccard), and
the top-N (default 5) plus all enabled skills are rendered into the system
prompt as numbered `LEARNED KNOWLEDGE` / `KNOWN PROCEDURES` blocks. The exact
memory and skill identifiers are persisted on the assistant message for later
feedback attribution. Recall is best-effort: if it fails the chat still
answers. Row-level Frappe permissions apply the same scopes to Desk and API
lists, so private memories are not exposed merely because a user can open the
Learning workspace.

### 8. Observe result

`observe_feedback()` runs when a user rates an answer. A supplied correction
becomes a `Chat Correction` candidate. Without one, the system stores a clearly
labelled **failure example** rather than accidentally proposing the incorrect
answer as authoritative knowledge. Repeated ratings are idempotent, and changing
a rating moves the exact recalled memories between `helpful_count` and
`not_helpful_count`. Recalling a memory also increments its `usage_count` /
`last_used_on`, so administrators can see what is shaping behaviour and retire
what is not.

---

## Governance and control

- **Capability:** teaching is gated by the `learning` capability
  (`allow_learning` on `AI Resource Policy`, default on).
- **Approval:** promoting a candidate to memory/skill requires AI Manager /
  System Manager, and can be required globally via settings.
- **Audit:** candidate creation, approval and rejection each write an
  `AI Audit Log` entry; every candidate carries provenance (who, what source,
  what originating record).
- **Scope:** memories and skills can be `Global`, or restricted to a user,
  role, or agent.
- **Retirement:** memories can be archived and skills disabled, instantly
  stopping their influence on future answers.

---

## Files

| File | Role |
| --- | --- |
| `ai/learning_utils.py` | Pure, DB-free scoring / dedup / classification / formatting. |
| `ai/learning.py` | Orchestration: candidate lifecycle, approval, recall, feedback. |
| `api/learning.py` | Whitelisted endpoints: `teach`, `approve_candidate`, `reject_candidate`, listers, `overview`. |
| `ai_learning/doctype/ai_knowledge_candidate` | The teaching intake record. |
| `ai_learning/doctype/ai_memory` | Approved persistent knowledge, injected into future turns. |
| `ai_learning/doctype/ai_skill` | Approved learned procedures, injected into future turns. |
| `ai_learning/workspace/ai_learning` | Desk workspace linking the three doctypes. |
| `ai_learning/doctype/ai_knowledge_candidate/ai_knowledge_candidate.js` | Approve / Reject buttons on the candidate form. |

Wiring into the agent lives in `ai/agent.py` (`build_system_prompt` +
`run_agent_turn`) and feedback capture in `api/chat.py` (`submit_feedback`).

---

## Development & testing standard

The Learning Loop follows the platform's layered-testing standard. The pure
scoring / dedup / classification logic in `learning_utils` is covered by
**unit tests** that run with **no Frappe site present**:

```bash
python -m unittest ai_fr_hg.tests.test_learning_utils
```

The full lifecycle — candidate creation, validation, conflict detection,
approval, promotion, recall scoping, and feedback capture — is covered by
**integration tests** in
`ai_fr_hg.ai_learning.doctype.ai_knowledge_candidate.test_ai_knowledge_candidate`
(`TestLearningLoop`), which stub the model runtime and run on a normal bench:

```bash
bench --site your-site.local run-tests --app ai_fr_hg
```

Before trusting this in production, validate on a real bench + worker + model
as described in the repository's general production checklist (see
`docs/FILE_TO_ANSWER.md`): hostile/large inputs, failure recovery, and
performance are exercised there for the pipeline this loop extends.
