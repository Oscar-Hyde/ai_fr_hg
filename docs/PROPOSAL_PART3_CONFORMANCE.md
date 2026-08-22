# Part 3 conformance — execution roadmap, milestones, governance

Part 3 defines the lifecycle, phase gates, dependency rules and Definition of
Done. Part 2 closed with the instruction that governs how this was executed:

> The most important thing is to never, ever rely on documents.

A roadmap written as prose is a document. It cannot fail, so it cannot govern.
Part 3 has therefore been implemented as **executable gates**, and this file
records only what those gates now enforce and what they cannot.

## What is enforced by a program

| Part 3 clause | Enforced by | Fails when |
| --- | --- | --- |
| §33 dependency order | `scripts/phase_gate.py` | A later phase is active while an earlier phase has open, non-exempt findings |
| §36 Definition of Done | `TestRegisterEvidenceIsReal` | A closed row cites a code symbol no test references |
| §37 prohibited claims | `scripts/phase_gate.py` | Evidence reads "field exists", "endpoint exists", "UI exists", "tests pass", "merged", "declared only" |
| §31 stage 4 verification | `scripts/mutation_check.py` | Any of 60 injected defects survives, or an anchor goes stale |
| §32 exit criteria | `phase_gate.py --report` | — (reporting; the per-phase outstanding list is derived from the register) |
| §38 security readiness | behavioural suites + mutations | A permission guard can be deleted without a test failing |

`test_phase_gate.py` runs the gate in CI and proves it can fail, by mutating a
copy of the register and asserting a non-zero exit — the gate is held to the
same standard as the code it governs.

## Current phase state

Produced by `python scripts/phase_gate.py --report`, not by hand:

```
phase  open  closed  outstanding
0         1      17  OPS-01                          (blocked: owner action)
1         3      13  SEC-03, SEC-04, SEC-07          (blocked: runtime tier)
2         0       6  — exit criteria met
3         0       9  — exit criteria met
4         2      13  ING-06, TRN-04                  (blocked: runtime tier)
5         0      11  — exit criteria met
6         9       8  LEARN-02..05, OPS-03..06, PROV-03
7         1       0  CHAT-09                         (blocked: runtime tier)
```

## The runtime exemption, and why it is narrow

§33 orders *work*. No amount of reordering makes an unavailable tier
available: this environment has no MariaDB, no Redis, no workers and no
browser, because HTTP egress is filtered and neither server is installable.

A row may therefore be exempted from blocking later phases **only** by
carrying the literal marker `[RUNTIME-TIER]` in its status, and
`test_the_runtime_exemption_requires_an_explicit_marker` additionally requires
it to name the evidence it is waiting for.

The marker is deliberately literal. An earlier version matched prose
("runtime verification PENDING") and silently missed TRN-04's "browser
Stop/reconnect still PENDING". An exemption that depends on how a sentence
happens to be worded is not a control.

Five rows currently hold it: SEC-03, SEC-04, SEC-07, ING-06, TRN-04. Each has
passing backend evidence and is waiting only on browser or chaos testing.

## FILE-08 — dispositioned by Removal

The one finding Part 3's §33 gate flagged as genuinely actionable.

**Frappe V17 capabilities evaluated first**, per the engineering principles:
`frappe.core.api.file` already ships `get_files_in_folder`,
`get_files_by_search_text`, `create_new_folder` and `move_file`, and
`frappe/public/js/frappe/views/file/file_view.js` provides breadcrumbs, folder
navigation, drag-move and search. The custom endpoints duplicated framework
responsibility, which the principles prohibit.

Nine unreachable endpoints were **deleted**: `get_tree`,
`list_folder_contents`, `get_tabs`, `get_recents`, `list_favorites`,
`get_folder_info`, `move_file`, `move_folder`, `search`.

`add_favorite` and `remove_favorite` were **kept**: `public/js/file_list.js`
calls them and Frappe has no favourites concept, so they are not duplication.

All 469 tests passed unchanged after removal, which is the evidence that
nothing depended on them.

## What Part 3 cannot yet certify

§38 production readiness and §32 Phase 7 exit both require the runtime tier.
Nothing in this repository rests on it. Specifically unproven and unclaimed:
MariaDB transactions and InnoDB isolation, index behaviour, deadlocks, Redis
as deployed, worker execution, `bench migrate`, and browser Desk behaviour.

§35 review and approval remains a human process. The gates establish that a
milestone is *eligible* for review; they do not approve it.

**The mutation gate is still not mandatory in CI.** The GitHub App cannot
write `.github/workflows/`, so `docs/phase-reports/APPLY_MUTATION_GATE.md`
carries the YAML for a maintainer. Until a required status check enforces it,
the evidence exists but the governance does not — VER-02 stays open on
exactly that.
