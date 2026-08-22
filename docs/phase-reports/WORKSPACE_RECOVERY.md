# Workspace recovery procedure (OPS-07)

## Observed

The development workspace has been recreated twice mid-audit. Each time:

* local git history reset to the base commit,
* newly-added files lost,
* modifications to tracked files retained as uncommitted edits,
* `/home/user/.venv` and `/tmp/frappe-src` destroyed,
* **the pushed branch on `origin` was unaffected.**

## Impact

Local verification state is disposable. That is tolerable — but only if the
recovery path is deterministic, because the failure mode is subtle: a
half-restored tree looks like ordinary uncommitted work, and committing from
it would silently drop every file added since the last push.

## Recovery

Run in order. Do not skip step 2 — the whole procedure depends on the remote
being ahead of the local tree, and that must be verified rather than assumed.

```bash
# 1. Is the local history actually damaged?
git log --oneline -1                       # base commit instead of your work?

# 2. Confirm the remote holds the work, and note the SHA.
git ls-remote --heads origin arena/<session-branch>

# 3. Fetch the branch explicitly; a plain `git fetch` may not create the ref
#    after a re-clone.
git fetch origin 'refs/heads/arena/<session-branch>:refs/remotes/origin/arena/<session-branch>'

# 4. Snapshot whatever is in the tree, then reset to the remote.
git stash push -u -m "pre-restore snapshot"
git reset --hard origin/arena/<session-branch>

# 5. Verify artifact-by-artifact before continuing.
for f in scripts/mutation_check.py scripts/phase_gate.py \
         ai_fr_hg/tests/fakebench.py ai_fr_hg/tests/test_part2_behaviour.py; do
  [ -f "$f" ] && echo "OK   $f" || echo "MISS $f"
done

# 6. Only once the tree is verified.
git stash drop
```

## Rebuild the toolchain

```bash
python3 -m venv /home/user/.venv        # no -q; it is not a valid flag here
/home/user/.venv/bin/pip install -q pytest requests pypdf openpyxl python-pptx \
    odfpy python-docx beautifulsoup4 lxml "ruff==0.14.10" pyyaml "fakeredis[lua]"

git clone --depth 1 --branch develop https://github.com/frappe/frappe.git /tmp/frappe-src
```

`fakeredis[lua]` and the Frappe checkout are both load-bearing: without them
the admission-control and schema suites **skip** rather than fail, so a
partial rebuild produces a green run that proves less than it appears to.

## Re-establish the baseline before doing any work

```bash
/home/user/.venv/bin/ruff check ai_fr_hg/ scripts/
FRAPPE_SOURCE=/tmp/frappe-src /home/user/.venv/bin/python -m pytest ai_fr_hg/tests/ -q \
  --ignore=ai_fr_hg/tests/test_units.py \
  --ignore=ai_fr_hg/tests/test_pattern_units.py \
  --ignore=ai_fr_hg/tests/test_api_validation_units.py \
  --ignore=ai_fr_hg/tests/test_int02_validation.py \
  --ignore=ai_fr_hg/tests/test_int03_hierarchical.py \
  --ignore=ai_fr_hg/tests/test_int04_whole_doc.py \
  --ignore=ai_fr_hg/tests/test_phase_6_governance.py
/home/user/.venv/bin/python scripts/phase_gate.py
```

The test count must match the last commit's stated figure. A *lower* count
after a restore means files are still missing, not that tests were removed.

## Why this is registered rather than fixed

The cause is outside the repository. What belongs to the project is the
recovery path and the verification step that stops a partial restore being
committed — both recorded here, and `test_workspace_recovery_is_documented`
keeps this file honest about the commands it names.
