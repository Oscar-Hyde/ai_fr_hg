# Owner action: add the mutation gate to CI

**Why this is not applied automatically.** The GitHub App used by this session
does not hold the `workflows` permission, so pushes that modify
`.github/workflows/**` are rejected by GitHub. Everything else in the
verification work is merged; only the CI wiring needs a maintainer.

**What it does.** Runs `scripts/mutation_check.py`, which injects 26 realistic
defects into application source and reruns the offline suite once per defect.
If any mutation survives — meaning the tests did not notice a real bug — the
job fails. This is what stops tautological tests (`assertIn("field", source)`)
from re-entering the suite and inflating apparent coverage.

It needs no MariaDB, Redis, or Frappe: the offline suite runs against the
in-memory bench in `ai_fr_hg/tests/fakebench.py`.

**Current local result:** 90/90 caught, 0 survived, 0 stale (~13m30s on the offline batch).

**`FRAPPE_SOURCE` is required, not optional.** It must point at a
`frappe/develop` checkout so `test_doctype_schema_against_frappe.py` compares
its transcribed constants against Frappe's actual source and the schema rules
cannot silently drift from the framework. Without it those tests *skip*: the
job still reports green while proving materially less. The full job below sets
it; treat a green run with missing dependencies as an unverified run.

Without it those two cross-check tests skip; the 865 schema subtests still run.

## Apply

Append this job to `.github/workflows/ci.yml`:

```yaml
  mutation:
    name: Mutation gate
    runs-on: ubuntu-latest
    timeout-minutes: 30

    steps:
      - name: Clone application
        uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7

      - name: Setup Python
        uses: actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97 # v7
        with:
          python-version: "3.14"
          cache: pip

      - name: Install offline test dependencies
        run: |
          python -m pip install --upgrade pip
          # The offline suite runs against the in-memory bench in
          # ai_fr_hg/tests/fakebench.py, so Frappe itself is not required.
          # `fakeredis[lua]` and the frappe checkout are NOT optional: without
          # them the affected suites skip, and the run goes green while
          # proving materially less. Keep ruff pinned - newer versions have
          # reformatted `except (A, B):` in a way the mutation anchors notice.
          python -m pip install pytest requests pyyaml "fakeredis[lua]" ruff==0.14.10
          python -m pip install --editable ".[documents]"

      - name: Fetch Frappe source for schema/hook checks
        # test_doctype_schema_against_frappe.py and test_hook_targets_resolve.py
        # resolve against a real checkout; they skip silently without it.
        run: git clone --depth 1 --branch develop https://github.com/frappe/frappe.git /tmp/frappe-src

      - name: Enforce phase and dependency order
        run: python scripts/phase_gate.py

      - name: Prove the suite can fail
        # Injects real defects and fails if the tests do not notice. Guards
        # against tautological tests that assert on source text.
        env:
          FRAPPE_SOURCE: /tmp/frappe-src
        run: python scripts/mutation_check.py
```

Then add **Mutation gate** to the required status checks for
`arena/01a024b6-ai-fr-hg` and `main`, alongside the existing `Server` and
`Linter` checks. Note that OPS-01 still applies: until branch protection is
actually enforcing, a required check is advisory.

## Run it locally

```bash
python scripts/mutation_check.py          # full campaign
python scripts/mutation_check.py --list   # show mutations without running
```

### Before pushing: run the register checks the way CI does

pytest is **not** sufficient for the documentation-consistency suites. Several
of them (`test_rpc_contract_reachability.py`, `test_phase_0_contracts.py`,
`test_phase_gate.py`) parse `GAP_REGISTER.md`, and CI runs them through
`unittest`, not pytest. A register edit that satisfies pytest can still fail
the Server job — that happened at 455f022, where a new row cited a symbol in
prose and the "every cited symbol must be referenced by a test" rule rejected
it.

```bash
python -m unittest ai_fr_hg.tests.test_rpc_contract_reachability \
                   ai_fr_hg.tests.test_phase_0_contracts \
                   ai_fr_hg.tests.test_phase_gate
```

Run this after **any** edit to `GAP_REGISTER.md` or `DEVELOPMENT_PLAN.md`.
Note that backticked identifiers in a CLOSED row's evidence column are treated
as claims: if a symbol is mentioned only as prose, name it in plain text
instead of backticks, or the row is rejected.

A mutation reported as `SKIP ... (anchor not found)` means the source moved and
the mutation no longer tests anything — fix the anchor rather than deleting it,
or the gate silently weakens over time.
