# Cell #1 Final Report — Jackson-databind x correctness

**Phase:** 0  ·  **Cell:** #1  ·  **Owner:** mshaikh@guidewire.com

**Generated:** $(re-run `python3 scripts/day4-finalize.py report` to refresh)

**Source artifacts:**
- Recon: [cell-1/recon/cell-1-recon.md](recon/cell-1-recon.md)
- Backtest: [cell-1/cell-1-backtest.md](cell-1-backtest.md)
- Pass-1 candidates: [cell-1/cell-1-candidates-pass1.md](cell-1-candidates-pass1.md)
- Validation scaffolds: [cell-1/hunt/validation/](hunt/validation/)

---

## TL;DR

- Cell #1 auto-gate: **FAIL** (auto-checks)
- Final validated findings (after self-consistency): **0** (of 1 pass-1 candidates, 0 survived self-consistency)
- Auto-pass candidates (status=validated AND repro=pass AND not-dupe AND self-consistent): **0**
- Full pass requires a HUMAN judgment that >=1 of those would have taken >2h unaided (see scope §2 success criteria) — fill in below.

---

## Stats

| Metric | Value |
|---|---|
| Pass-1 candidates total | 1 |
| Code-quality candidates | 0 |
| Edge-case candidates | 1 |
| Survived self-consistency (>=2/3) | 0 |
| Dropped by self-consistency (1/3 only) | 1 |
| Final status = validated | 0 |
| Final status = unreproducible | 0 |
| Final status = dupe | 0 |
| Final status = false-positive | 0 |
| Final status = failed-self-consistency | 1 |
| Final status = pending | 0 |

### Self-consistency matcher drops (P1-15 diagnostic)

Pass-2 + pass-3 lookups that returned no match, by reason. Multiple entries per pass-1 candidate possible (one per pass that didn't match). If `same_file_overlap_le_1` dominates, the 2-keyword threshold may be too strict; if `no_same_file_candidate` dominates, the re-run found entirely different bugs (likely a real signal-vs-noise issue).

| Reason | Count |
|---|---|
| `pass-2: no_same_file_candidate` | 1 |
| `pass-3: no_same_file_candidate` | 1 |

---

## Validated findings (survived self-consistency)

_No candidates passed all auto-checks._

---

## Dropped by self-consistency (1-of-3 contexts only)

| ID | Angle | Location | Summary | Pass-1 status |
|---|---|---|---|---|
| `ec-1` | edge-case | `src/main/java/com/fasterxml/jackson/databind/deser/std/CollectionDeserializer.java:464-479` | Object-Id collection path adds JSON null directly to a null-hostile collection,  | failed-self-consistency |

_If you disagree with any of these drops (e.g., the matcher was too strict on summary keywords), edit the corresponding `cell-1/hunt/validation/<id>.yaml` scaffold's `self_consistency.survived` field manually and re-run report._

---

## Other rejected findings (full breakdown)

### failed-self-consistency (1)

- `ec-1` (edge-case) — `src/main/java/com/fasterxml/jackson/databind/deser/std/CollectionDeserializer.java:464-479`: Object-Id collection path adds JSON null directly to a null-hostile collection, bypassing the _tryTo

---

## Auto-gate breakdown

Per phase-0-scope.md §2 (Success criteria for Cell #1):

| Gate | Auto-check | Status |
|---|---|---|
| (a) novel | status=validated AND dedup.is_duplicate=false | FAIL |
| (b) reproducible | gates.reproducer.status=pass | FAIL |
| (c) self-consistent | appears in >=2/3 contexts | FAIL |
| (d) would have taken >2h unaided | dataset.yaml `gt_2h_unaided` field | _PARTIAL_ — 10 entries unjudged, 0 judged ≤2h |

---

## Kill-criteria check

Per phase-0-scope.md §2 (Kill criteria for Cell #1):

- [ ] All candidates are Semgrep/SpotBugs dupes → KILL
- [x] 0 reproducers ran successfully → KILL
- [ ] Token spend > $50 with no validated findings → KILL (fill cost section below)
- [ ] Engineer judges every finding to be slop → KILL (judgment call — fill below)

---

## Cost (filled 2026-06-05 — agent-driven run)

- **Token spend (approx):** not instrumented in this run (no billing capture
  in-sandbox). The headless path's cap is $25; this cell was driven via
  session Opus agents whose spend isn't tracked here. _Operator: fill from billing._
- **Engineer hours:** N/A as a human run — Cell #1 was executed agent-driven
  across this session, not by an engineer following the ~14h plan. Wall-clock
  agent time was modest; the real bottleneck was environment unblocking
  (Python-SSL proxy CA, then the Maven/SpotBugs baseline build), not analysis.
- **Within budget?** indeterminate (untracked); no evidence of overspend.

---

## Lessons learned (filled 2026-06-05)

- What worked:
  - The deterministic file-coverage gate (P0-1) — auditable, ungameable, and it
    produced a real PROCEED once baselines existed.
  - No-slop discipline: 5 of 6 hunt passes returned `findings: []` with explicit
    rejection reasoning; only one borderline candidate (ec-1) was emitted.
  - The reproducer-builder + fix-builder produced a real JUnit reproducer and a
    minimal patch for ec-1 that `git apply --check`s cleanly against pristine source.
  - Self-consistency correctly dropped ec-1 (appeared 1 of 3 fresh contexts).
  - A two-scanner baseline (Semgrep security + SpotBugs correctness) made the
    "novel over free tools" claim falsifiable rather than vacuous.
- What didn't:
  - The actual validation never ran — the Docker daemon is down, so every
    reproducer/fix is `not-attempted`. **0 findings could be validated**, so the
    Phase-0 question ("does the loop beat free tools with a *validated* finding?")
    is unanswered — blocked by environment, not by the method.
- What I'd change about the loop:
  - Make a Docker/baseline preflight a Day-0 gate — don't start the hunt until the
    validators can actually run.
  - Point the hunt at files SpotBugs does NOT cover: against SpotBugs the agent's
    file-localization overlaps 70% (7/10 fix-files), so marginal value concentrates
    in the files free tools miss.
- Surprises:
  - The network was reachable all along (system curl/git/mvn → 200). The blocker
    was Python 3.13/OpenSSL 3.x rejecting the corporate proxy MITM CA, not
    connectivity. Fetching rules with `git` + running scanners on local files
    unblocked both baselines.
  - SpotBugs flags 70% of the fix-touched files — a free correctness scanner
    already localizes most of what the agent did (file-level).

---

## Recommendation (filled 2026-06-05)

**DEFER the Cell #2-vs-kill decision — first complete Cell #1's *validation* in a
Docker-capable environment.**

- [ ] ~~Proceed to Cell #2~~ — requires ≥1 validated finding; there are **0**
  (ec-1 failed self-consistency and its reproducer/fix could not be executed).
  Can't honestly check this box yet.
- [x] **Run the kill-protocol retry — but as Cell #1's own validation, not a new cell.**
  The failure was environmental (Docker down), not the method.
- [ ] ~~Kill Phase 0~~ — not warranted: the calibration gate PROCEEDED (now backed
  by both a security AND a correctness baseline), and the agents showed no-slop
  discipline. There IS signal; it just wasn't *validated* here.

**Reasoning.** Cell #1's **calibration** gate is a real PROCEED (file_coverage@3=80%,
precision@5=100%, novel-over-baseline 3/10 against Semgrep+SpotBugs). But the
**outcome** is 0 validated findings: the single candidate (ec-1) appeared in only
1 of 3 fresh contexts (failed self-consistency) AND its reproducer/fix couldn't run
(Docker daemon down), so the "reproducible / would-take-2h-unaided" criteria can't
be assessed. The honest next step is to bring up Docker and re-run the Day-3
validators + orchestrator on Cell #1 to get a true validated-finding count —
*then* decide Cell #2 vs kill. Weigh this too: against SpotBugs the agent overlaps
70% at the file level, so the marginal value over free tools lives in the 3/10
novel files and in whether the *specific* bugs differ from SpotBugs's — evaluable
only once reproducers can execute.

---

## Open questions

- (Auto) Did self-consistency feel too strict or too lenient? Edit `cell-1/hunt/validation/<id>.yaml::self_consistency.survived` if matches were missed; the matcher is coarse (same file + >=2 keyword overlap).
- (Auto) Were OSV/GitHub auto-dedup matches useful or noisy? Feedback shapes Phase 1's dedup design.
- (Human) Would you have found any of the validated findings in your normal review work? If yes, the agent's marginal value is lower than the recall numbers suggest.
- (Human) Reproducer-builder gap: how much time did writing each reproducer take? This is the critical Phase 1 deliverable.
