# Cell #1 Day 2 Backtest Report

**Generated:** 2026-06-05T04:34:53Z — re-run `python3 scripts/day2-backtest.py score` to refresh.

## Aggregate metrics (10 entries scored)

### Deterministic file-coverage (primary gate input — no LLM judgment involved)

| Metric | Value | Phase 0 gate |
|---|---|---|
| file_coverage@1 | 80% (8/10) | — |
| **file_coverage@3** | 80% (8/10) | **≥30% required** to proceed to Day 3 |
| file_coverage@5 | 80% (8/10) | — |
| **file_match_precision@5** | 100% (12/12 findings) | **≥20% required** (gate input) |
| total findings | 12 | — |
| total file matches | 12 | — |

> *file_coverage@K* counts an entry as covered iff one of the agent's top-K findings
> names a file the historical fix actually touched. *file_match_precision@K* is the
> fraction of findings overall that hit a fix-touched file. Both are computed from
> `files_touched` (deterministic) — no LLM labels involved.

### LLM/human-judged labels (informational only — NOT a gate input as of P0-1 fix)

| Metric | Value | Status |
|---|---|---|
| recall@1 | 10% (1/10) | informational |
| recall@3 | 10% (1/10) | informational |
| recall@5 | 10% (1/10) | informational |
| precision_matches@5 | 8% (1/12 `matches_known`) | informational |
| precision_anyTP@5 | 100% (12/12 matches_known + unrelated_tp) | informational |
| matches_known total | 1 | — |
| unrelated_tp total | 11 | — |
| FP total | 0 | — |

_Note: 0/10 entries have deterministic labels; others reflect LLM auto-label or human review._

### Baseline scanner coverage

| Metric | Value | Status |
|---|---|---|
| baseline coverage | 70% (7/10) | informational |
| baseline-dupe count | 0 | informational |

## Per-entry rollup

| Issue | Title | #findings | File-match rank | File matches | Label rank | Matches_known | Unrelated_tp | FP | Baseline-flagged file |
|---|---|---|---|---|---|---|---|---|---|
| #5870 | `EnumMap` and `EnumSet` properties ignore `@JsonDeserialize( | 2 | 1 | 2 | — | 0 | 2 | 0 | spotbugs |
| #5851 | Regression of `JsonTypeInfo.Id.MINIMAL_CLASS` in the 3.x bra | 1 | 1 | 1 | — | 0 | 1 | 0 | — |
| #5840 | Jackson 2.21 throws Conflicting property-based creators if b | 0 | — | 0 | — | 0 | 0 | 0 | spotbugs |
| #5819 | `JsonNodeFeature.STRIP_TRAILING_BIGDECIMAL_ZEROES` not worki | 1 | 1 | 1 | — | 0 | 1 | 0 | — |
| #5813 | `JsonMapper` not thread-safe when using custom serializers | 0 | — | 0 | — | 0 | 0 | 0 | spotbugs |
| #5734 | `DeserializationFeature.FAIL_ON_NULL_FOR_PRIMITIVES` treats  | 1 | 1 | 1 | — | 0 | 1 | 0 | spotbugs |
| #5616 | `ObjectWriter` serializes reference types (like `AtomicRefer | 1 | 1 | 1 | — | 0 | 1 | 0 | spotbugs |
| #5615 | JsonMapper seems to be not thread-safe when using the polymo | 2 | 1 | 2 | 1 | 1 | 1 | 0 | — |
| #5608 | Confusing error-handling logic in `FunctionalScalarDeseriali | 3 | 1 | 3 | — | 0 | 3 | 0 | spotbugs |
| #5978 | BuilderBasedDeserializer unwrapped update path still uses ig | 1 | 1 | 1 | — | 0 | 1 | 0 | spotbugs |

## Caveats

- **Statistical thinness:** 10-entry recall has ±15% error bars. Treat metrics as directional.
- **Found-fix bias:** the prompt pointed the agent at the right *file*. Real novel hunting doesn't get that hint. Adjust expectations: real-world recall will be lower.
- **Baseline coverage = 'free tools touched the file'**, NOT 'free tools caught the exact bug'. If baseline-flagged is high but agent's `matches_known` is on different findings than the baselines, the agent may still be adding signal. Inspect dupe_of_baseline counts.
- **If baseline coverage ≈ 100% AND dupe_of_baseline ≈ matches_known**, the agent is mostly regenerating free-tool output. **Kill Cell #1** even if recall@3 passed.

## Gate decision

Phase 0 Cell #1 proceeds to Day 3 novel-hunt IFF all of:

- [x] file_coverage@3 ≥ 30%  (actual: 80%)
- [x] file_match_precision@5 ≥ 20%  (actual: 100%)
- [x] at least one entry has file_match where the file was NOT baseline-flagged  (3/10)

**Decision:** PROCEED to Day 3

_Gate inputs are deterministic file-coverage metrics (P0-1 fix). The LLM-driven labels above are advisory; they do not affect PROCEED/KILL._
