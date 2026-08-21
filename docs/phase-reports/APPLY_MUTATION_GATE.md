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

**Current local result:** 26/26 caught, 0 survived, 0 stale (~2m20s).

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
          python -m pip install pytest requests
          python -m pip install --editable ".[documents]"

      - name: Prove the suite can fail
        # Injects real defects and fails if the tests do not notice. Guards
        # against tautological tests that assert on source text.
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

A mutation reported as `SKIP ... (anchor not found)` means the source moved and
the mutation no longer tests anything — fix the anchor rather than deleting it,
or the gate silently weakens over time.
