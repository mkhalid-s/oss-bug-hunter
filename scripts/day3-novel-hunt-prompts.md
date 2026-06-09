# Novel Hunt Prompts — Cell #1 Day 3 Pass 1

Two prompts, one per angle. Both target the same file shortlist; the angle changes the lens.

**Prerequisite:** Day 2 backtest passed the gate (recall@3 ≥ 30%, precision@5 ≥ 20%, at least one TP independent of baseline). Otherwise: stop, go to §6 kill protocol in `phase-0-scope.md`.

**Prerequisite input:** `cell-1/shortlist.txt` — one file path per line, 3-8 files max, picked by the human after Day 1 synthesis (cross-referencing `hot-spots-coarse.txt`, `explore-inventory.md`, and recent-change density).

**Pass 1 vs Pass 2:**
- This file = Pass 1 (Day 3) — single run per angle, capture all findings.
- Day 4 re-runs the same prompts in fresh contexts and keeps only findings that appear in 2-of-3 contexts (self-consistency, per brief §5.4).

**Generated prompts** — let `scripts/day3-hunt.py prepare` substitute the shortlist:

```
python3 scripts/day3-hunt.py prepare
# writes cell-1/hunt/code-quality/prompt.md
# writes cell-1/hunt/edge-case/prompt.md
```

**How to invoke each prompt** (in Claude Code):

```
Agent({
  description: "Cell #1 novel hunt — code-quality (pass 1)",
  subagent_type: "general-purpose",
  prompt: "<<contents of cell-1/hunt/code-quality/prompt.md>>"
})
```

```
Agent({
  description: "Cell #1 novel hunt — edge-case (pass 1)",
  subagent_type: "general-purpose",
  prompt: "<<contents of cell-1/hunt/edge-case/prompt.md>>"
})
```

(`general-purpose` because we need codebase reads + tool access. `code-reviewer` would also work but adds Bedrock dispatch overhead we don't need here.)

After each run, paste the agent's `findings:` YAML block into:
- `cell-1/hunt/code-quality/findings-pass1.yaml`
- `cell-1/hunt/edge-case/findings-pass1.yaml`

Then run:

```
python3 scripts/day3-hunt.py validate
# - runs automated dedup for each candidate against OSV + GitHub issues
# - emits cell-1/hunt/validation/<finding-id>.yaml scaffolds (human fills the gates)
# - writes cell-1/cell-1-candidates-pass1.md
```

---

## Template A — code-quality angle

(Placeholder `{SHORTLIST_FILES_BLOCK}` is substituted by `prepare`.)

```
You are reviewing Java code in the `jackson-databind` project for CORRECTNESS
bugs from a code-quality angle. This is a novel-hunt pass: you do not know
which (if any) of these files contains a bug.

Working directory: {{TARGET_DIR}}

Files to review (read each in full, plus callers/callees as needed):
{SHORTLIST_FILES_BLOCK}

# Find at most 5 distinct correctness bugs in the listed files

Focus areas (use as a checklist):
- NullPointerException risks on parameters or intermediate values that are
  reachable from public entry points without a guard
- Off-by-one errors in indexing, loop bounds, substring math, array slicing
- Wrong return values (empty where null expected, inverted boolean, wrong
  branch of a ternary, mis-cast)
- Dropped or swallowed exceptions (caught and ignored where the caller would
  reasonably expect propagation)
- Logic errors in control flow: unreachable branches, inverted conditions,
  fall-through where break was intended
- Incorrect generic-type handling: raw type leaks, unchecked casts that
  would ClassCastException under specific bindings, type erasure assumptions
- Concurrency: unsynchronized mutation of fields documented as shared

# Out of scope (do not report)

- Security (deserialization gadgets, authn/authz, crypto)
- Code style, naming, formatting
- Refactoring opportunities, dead code, redundant abstractions
- Performance, unless it's a correctness issue (StackOverflow, infinite loop)
- Missing tests, javadoc gaps

# Confidence bar

Only report findings where you would bet money that triggering the described
input causes the described wrong behavior. If unsure, OMIT. We would rather
miss real bugs than emit slop — slop here costs the user's reputation with
the Jackson maintainers.

# Constraints

- Do NOT consult: git history, release notes, issue tracker, OSV/GHSA, the
  internet. Reason only from the source code in the working directory.
- Each finding must cite a specific file:line-range, not "somewhere in X".
- Each finding must be derivable from the code shown in `evidence` alone —
  no "trust me, I traced it" arguments.
- Each `reproducer_hint` must be a concrete Java snippet a Jackson user could
  write (typically: `ObjectMapper.readValue(<input>, <type>)` or similar).

# Output format

Return a single YAML block at the end of your message. If you find nothing,
return `findings: []`.

```yaml
findings:
  - summary: "<one-line description>"
    location: "<repo-relative-path:line-range>"
    type: "<NPE | off-by-one | wrong-return | dropped-exception | logic | generic-type | concurrency | other>"
    evidence: |
      <buggy code excerpt — 3-15 lines — followed by 1-2 sentences on why it's wrong>
    reproducer_hint: |
      <Java snippet, 1-5 lines>
```
```

---

## Template B — edge-case angle

(Placeholder `{SHORTLIST_FILES_BLOCK}` is substituted by `prepare`.)

```
You are reviewing Java code in the `jackson-databind` project for CORRECTNESS
bugs from an edge-case angle. This is a novel-hunt pass: you do not know
which (if any) of these files contains a bug.

Working directory: {{TARGET_DIR}}

Files to review (read each in full, plus callers/callees as needed):
{SHORTLIST_FILES_BLOCK}

# Find at most 5 distinct edge-case correctness bugs in the listed files

Edge-case categories to consider (use as a checklist — not all apply to every file):
- Timezone / DST: parsing/serializing dates around DST transitions, fixed
  offsets vs zone IDs, ZoneOffset.UTC vs ZoneId.of("UTC") equivalence
- Unicode: surrogate pairs, combining characters, BOM in input streams,
  non-BMP code points in string parsing
- Integer boundaries: MAX_VALUE+1 overflow, MIN_VALUE negation, unsigned-vs-signed
  in size/length math, overflow in size hint allocation
- Empty / singleton / deeply nested collections: empty arrays/maps where
  iteration assumed non-empty, single-element optimizations that break for size 0
- Locale-dependent formatting: Locale.getDefault() leaking into "should be
  invariant" code paths (decimal separator, case folding)
- Polymorphic-type resolution: subtype not yet loaded when parent deserializer
  runs, type id collision between equally-named classes in different packages
- Concurrent cache access: deserializer-cache reads during invalidation,
  double-construction of cached deserializers
- Recursive structures: cycles in object graphs, deep recursion hitting
  StackOverflowError before MAX_VALUE depth

# Out of scope (do not report)

- Security (deserialization gadgets, authn/authz, crypto)
- Code style, naming, formatting
- Refactoring opportunities, dead code, redundant abstractions
- Performance unless it's a correctness issue (StackOverflow, infinite loop)
- Generic NPE / off-by-one without an edge-case trigger (those are the
  code-quality angle's territory)

# Confidence bar

Only report findings where you would bet money that the described edge-case
input causes the described wrong behavior. If unsure, OMIT.

# Constraints

- Do NOT consult: git history, release notes, issue tracker, OSV/GHSA, the
  internet. Reason only from the source code in the working directory.
- Each finding must cite a specific file:line-range.
- Each finding must be derivable from the code shown in `evidence` alone.
- Each `reproducer_hint` must be a concrete Java snippet exhibiting the
  edge case (specific bad input value, specific call sequence).

# Output format

Return a single YAML block at the end of your message. If you find nothing,
return `findings: []`.

```yaml
findings:
  - summary: "<one-line description>"
    location: "<repo-relative-path:line-range>"
    type: "<timezone | unicode | integer-overflow | empty-collection | locale | polymorphic | concurrent-cache | recursion | other>"
    evidence: |
      <buggy code excerpt + 1-2 sentences on the edge case>
    reproducer_hint: |
      <Java snippet, 1-5 lines, with the specific edge-case input>
```
```
