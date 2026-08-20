#!/usr/bin/env bash
# Owner-only: copy desired Quality workflow into .github/workflows.
# Run from the repository root on a machine whose GitHub credentials
# include the `workflows` permission (the Arena GitHub App does not).
set -euo pipefail
root="$(cd "$(dirname "$0")/../.." && pwd)"
cp "$root/docs/phase-reports/linter.yml.desired" "$root/.github/workflows/linter.yml"
cp "$root/docs/phase-reports/test_phase_0_contracts.py.desired" \
	"$root/ai_fr_hg/tests/test_phase_0_contracts.py"
git -C "$root" add .github/workflows/linter.yml ai_fr_hg/tests/test_phase_0_contracts.py
echo "Staged Quality-gate files. Commit and push from this machine:"
echo "  git commit -m 'ci: executable Quality gate without Semgrep Cloud or editable audit'"
echo "  git push origin arena/01a01d05-ai-fr-hg"
