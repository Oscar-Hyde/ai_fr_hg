#!/usr/bin/env python3
"""Part 3 phase gate — executable, not narrative.

Part 3 (§32) defines phase exit criteria and (§33) forbids a phase proceeding
while its foundations are incomplete. Part 2 closed with the instruction that
matters most here: *never rely on documents*. A roadmap written as prose is a
document. This script turns §32/§33/§36/§37 into a program that fails.

It answers three questions from the repository itself:

1. §33 — is any phase being worked while an earlier phase still has open
   findings? (Dependency inversion: the proposal's explicit anti-pattern of
   building AI before permissions and retrieval are reliable.)
2. §36/§37 — does any row claim completion on prohibited evidence: a field
   exists, an endpoint exists, a screen exists, tests pass?
3. §32 — for each phase, what exactly is outstanding, and is its exit
   criterion satisfied?

Exit code 1 when a dependency is inverted or a prohibited claim is found.
`--report` prints the phase table without failing, for status review.

This does not judge whether a closed row is *correct* — that is what
`mutation_check.py` and the behavioural suites do. It judges whether the
project is allowed to move on.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REGISTER = ROOT / "docs" / "GAP_REGISTER.md"

#: §32 phase names, used only for reporting.
PHASE_NAMES = {
	0: "Truthful baseline and quality gate",
	1: "Isolation, permissions, and API safety",
	2: "Retrieval correctness and knowledge access",
	3: "Conversation and agent completion",
	4: "Ingestion, intelligence, patterns, translation",
	5: "Automation, pipelines, tasks, approvals",
	6: "Governance, operations, learning, backup",
	7: "Production qualification and release",
}

#: Findings whose ID prefix places them in a phase when the row itself carries
#: no phase column (the VER/API/CHAT-10/FILE-08 verification table is 5-column).
PREFIX_PHASE = {
	"VER": 0,
	"API": 1,
	"FILE-08": 1,
	# CHAT-10 came from the VER-08 reachability sweep: a Desk button that
	# never called its server method. It is a frontend/backend integration
	# defect, so it belongs with Phase 3, not with the chat features.
	"CHAT-10": 3,
}

#: §37 — evidence that is explicitly not acceptable as a completion claim.
#: Each pattern is paired with the prohibited claim it represents.
PROHIBITED_EVIDENCE = (
	(re.compile(r"\bfield exists\b", re.I), "§37: 'the field exists' is not a feature"),
	(re.compile(r"\bendpoint exists\b", re.I), "§37: 'the API exists' is not correct behaviour"),
	(re.compile(r"\b(?:screen|page|UI) exists\b", re.I), "§37: 'the UI exists' is not integration"),
	(re.compile(r"^\s*tests? pass(?:es|ing)?\.?\s*$", re.I), "§37: 'tests pass' is not coverage"),
	(re.compile(r"\bcode is merged\b", re.I), "§37: 'merged' is not operational validation"),
	(re.compile(r"\bdeclared only\b", re.I), "§37: a declaration is not an implementation"),
)

OPEN_STATUSES = ("OPEN", "IN PROGRESS", "BLOCKED", "REOPENED")


class Finding:
	__slots__ = ("evidence", "fid", "line", "phase", "status")

	def __init__(self, fid: str, phase: int | None, status: str, evidence: str, line: int):
		self.fid = fid
		self.phase = phase
		self.status = status
		self.evidence = evidence
		self.line = line

	@property
	def is_open(self) -> bool:
		return self.status.startswith(OPEN_STATUSES)

	@property
	def is_closed(self) -> bool:
		return self.status.startswith("CLOSED")


def _phase_for(fid: str, phase_cell: str) -> int | None:
	"""Phase from the row's own column, else from the ID prefix."""
	digits = phase_cell.strip()
	if digits.isdigit():
		return int(digits)
	if fid in PREFIX_PHASE:
		return PREFIX_PHASE[fid]
	prefix = fid.split("-")[0]
	return PREFIX_PHASE.get(prefix)


def parse_register() -> list[Finding]:
	findings: list[Finding] = []
	for number, raw in enumerate(REGISTER.read_text().splitlines(), start=1):
		if not re.match(r"^\|\s*[A-Z]+-\d+\s*\|", raw):
			continue
		cells = [cell.strip() for cell in raw.split("|")]
		fid = cells[1]
		status = ""
		status_index = 0
		for index, cell in enumerate(cells[2:], start=2):
			if cell.startswith(("CLOSED", *OPEN_STATUSES)):
				status = cell
				status_index = index
				break
		if not status:
			continue
		# The 7-column table puts the phase at index 3; the 5-column
		# verification table has no phase column at all.
		phase_cell = cells[3] if status_index >= 5 and len(cells) > 3 else ""
		findings.append(Finding(fid, _phase_for(fid, phase_cell), status, cells[-2], number))
	return findings


def check_prohibited_claims(findings: list[Finding]) -> list[str]:
	"""§36/§37 — a closed row may not rest on prohibited evidence."""
	problems = []
	for finding in findings:
		if not finding.is_closed:
			continue
		for pattern, reason in PROHIBITED_EVIDENCE:
			if pattern.search(finding.evidence):
				problems.append(f"{REGISTER.name}:{finding.line} {finding.fid} — {reason}")
	return problems


def check_dependency_order(findings: list[Finding]) -> list[str]:
	"""§33 — no phase may proceed while an earlier phase is incomplete.

	"Proceeding" means a later phase has work recorded as IN PROGRESS. An
	earlier phase holding OPEN findings while a later one is being actively
	worked is the dependency inversion §33 prohibits.
	"""
	open_by_phase: dict[int, list[str]] = {}
	active_by_phase: dict[int, list[str]] = {}
	for finding in findings:
		if finding.phase is None or not finding.is_open:
			continue
		open_by_phase.setdefault(finding.phase, []).append(finding.fid)
		if finding.status.startswith(("IN PROGRESS", "REOPENED")):
			active_by_phase.setdefault(finding.phase, []).append(finding.fid)

	problems = []
	for phase, active in sorted(active_by_phase.items()):
		blocking = {
			earlier: ids
			for earlier, ids in open_by_phase.items()
			if earlier < phase
			# A finding blocked on an external owner or on the unavailable
			# runtime tier is registered, not ignored; it does not stall
			# every later phase (§33 vs §30.1).
			and any(not _blocked_externally(findings, fid) for fid in ids)
		}
		if blocking:
			detail = "; ".join(f"phase {p}: {', '.join(sorted(ids))}" for p, ids in sorted(blocking.items()))
			problems.append(
				f"phase {phase} is active ({', '.join(sorted(active))}) while earlier work is open — {detail}"
			)
	return problems


#: A finding whose only remaining evidence needs the runtime tier (real
#: MariaDB, Redis, workers, browser) cannot be closed in this environment. It
#: is registered as a Phase 7 prerequisite rather than being allowed to stall
#: every later phase indefinitely — §33 orders *work*, and no amount of
#: reordering makes an unavailable tier available. The distinction is only
#: legitimate because the row states which tier it is waiting on; a row that
#: merely says "pending" still blocks.
#: The marker must be explicit and uniform. An earlier version matched loose
#: prose ("runtime verification PENDING") and silently missed TRN-04's
#: "browser Stop/reconnect still PENDING" — an exemption that depends on how a
#: sentence happens to be worded is not a control. A row claiming this
#: exemption must say so in exactly this form.
_RUNTIME_PENDING = re.compile(r"\[RUNTIME-TIER\]")


def _blocked_externally(findings: list[Finding], fid: str) -> bool:
	"""True when a finding waits on an owner action or the runtime tier."""
	for finding in findings:
		if finding.fid != fid:
			continue
		if "OWNER ACTION" in finding.status:
			return True
		return bool(_RUNTIME_PENDING.search(finding.status))
	return False


def report(findings: list[Finding]) -> None:
	print(f"{'phase':<6}{'open':>5}{'closed':>8}  outstanding")
	print("-" * 78)
	for phase in sorted(PHASE_NAMES):
		rows = [f for f in findings if f.phase == phase]
		if not rows:
			continue
		open_rows = [f for f in rows if f.is_open]
		closed = len([f for f in rows if f.is_closed])
		outstanding = ", ".join(sorted(f.fid for f in open_rows)) or "— exit criteria met"
		print(f"{phase:<6}{len(open_rows):>5}{closed:>8}  {outstanding}")
		print(f"{'':<19}{PHASE_NAMES[phase]}")
	unplaced = [f for f in findings if f.phase is None]
	if unplaced:
		print(f"\nunplaced rows (no phase column, no prefix rule): {len(unplaced)}")
		for finding in unplaced:
			print(f"    {finding.fid}")


def main() -> int:
	parser = argparse.ArgumentParser(description=__doc__)
	parser.add_argument("--report", action="store_true", help="print the phase table without failing")
	args = parser.parse_args()

	findings = parse_register()
	if len(findings) < 50:
		print(f"FAIL: register parsing collapsed — only {len(findings)} findings found", file=sys.stderr)
		return 1

	if args.report:
		report(findings)
		return 0

	failures = check_prohibited_claims(findings) + check_dependency_order(findings)

	print(f"parsed {len(findings)} findings from {REGISTER.name}")
	if failures:
		print(f"\n{len(failures)} phase-gate violation(s):\n")
		for failure in failures:
			print(f"  - {failure}")
		print("\nSee Part 3 §33 (dependency order) and §36/§37 (definition of done).")
		return 1

	print("phase gate: no dependency inversion, no prohibited completion claims")
	return 0


if __name__ == "__main__":
	raise SystemExit(main())
