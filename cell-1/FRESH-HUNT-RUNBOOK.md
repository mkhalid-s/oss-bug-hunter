# Cell #1 — fresh hunt (round 2) runbook  [QUEUED 2026-06-06]

Goal: a finding that BOTH reproduces (Docker validator) AND survives 2-of-3
self-consistency — the real Phase-0 keeper that round 1 didn't yield (ec-1 was
1/3). Shortlist = `cell-1/shortlist-round2.txt` (deser files the free scanners
did NOT flag). Run this only AFTER the host Docker reproduce→fix loop is confirmed
green (so findings can actually be validated, not just collected).

## 1. Preserve the round-1 record (don't lose ec-1 / the filled final report)
    cp -r cell-1/hunt cell-1/_round1/hunt
    cp cell-1/cell-1-candidates-pass1.md cell-1/_round1/
    cp cell-1/cell-1-report.md            cell-1/_round1/
    cp cell-1/hunt/.gate.ok               cell-1/_round1/ 2>/dev/null || true

## 2. Swap in the round-2 shortlist (keep a copy of round 1)
    cp cell-1/shortlist.txt cell-1/_round1/shortlist.txt
    cp cell-1/shortlist-round2.txt cell-1/shortlist.txt

## 3. Reset Day 3 + Day 4 (keeps recon, baselines, backtest, gate.ok)
    make reset-hunt        # removes cell-1/hunt + day3/day4 reports; recon+backtest stay
    mkdir -p cell-1/hunt && cp cell-1/_round1/.gate.ok cell-1/hunt/.gate.ok   # gate is still PROCEED

## 4. Generate fresh prompts, then run BOTH angles in FRESH agent contexts
    .venv/bin/python scripts/day3-hunt.py prepare
    # Ask THIS Claude session: "run the round-2 pass-1 hunt" — it dispatches
    # code-quality + edge-case agents (fresh Agent calls) and pastes findings into
    # cell-1/hunt/{code-quality,edge-case}/findings-pass1.yaml. (Read-only; no Docker.)

## 5. Scaffolds + validation (Docker)
    .venv/bin/python scripts/day3-hunt.py validate                 # writes scaffolds
    .venv/bin/python scripts/day3-hunt.py suggest-gates            # advisory dedup/CWE
    # For each finding: build + validate the reproducer, then a fix:
    .venv/bin/python tool/pipeline.py orchestrate --network bridge # reproduce→fix→retry (opus/high)
    #   ...or per-finding: run-repros / run-fixes (see VALIDATION-RUNBOOK.md)

## 6. Day 4 self-consistency (the keeper test)
    .venv/bin/python scripts/day4-finalize.py prepare
    # Ask THIS session: "run the round-2 Day-4 passes" — 4 fresh runs (2 angles x pass 2,3).
    .venv/bin/python scripts/day4-finalize.py report
    # A KEEPER = final_status validated AND reproducer pass AND survived 2/3 AND
    # not a baseline dupe. That's the Phase-0 success criterion round 1 missed.

## Notes
- First validator run needs `--network bridge` (warms the container /work/.m2).
- Re-running `day4-finalize.py report` blanks the HUMAN sections — re-fill after.
- If round-2 comes back empty/low-signal: that's a legitimate Phase-0 result given
  the 70% scanner overlap — record it; consider Cell #2 (Drools, scanner-thin) per
  phase-0-scope §1, where the agent's marginal value should be higher.
