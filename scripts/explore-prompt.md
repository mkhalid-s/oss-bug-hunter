# Explore-Subagent Prompt — Cell #1 Day 1 Inventory

**Purpose:** Hand this prompt to the `Explore` subagent after `scripts/day1-recon.sh` finishes. The subagent does inventory/lookup only (its strength); the main session does synthesis afterwards.

**Search breadth to specify when invoking:** `very thorough`

**How to invoke** (in Claude Code):

```
Agent({
  description: "Cell #1 Jackson inventory",
  subagent_type: "Explore",
  prompt: "<<the prompt body below>>"
})
```

---

## Prompt body (copy from here, including this header line)

You are doing inventory and lookup for an OSS code investigation. **Read-only — do not edit any file.** You do NOT have Write/Edit access; return your full structured inventory in your final message — the caller will save it to `cell-1/recon/explore-inventory.md`.

# Context

- Target codebase: `targets/jackson-databind/`
- Pinned tag: see `cell-1/recon/target-pin.json` for the exact commit
- Recon artifacts already gathered by `scripts/day1-recon.sh`:
  - `cell-1/recon/closed-bugs.json` and `closed-bugs.tsv` — closed bug-labelled issues from the last 24mo
  - `cell-1/recon/releases.json` — last 50 release notes
  - `cell-1/recon/deserializer-inventory.txt` — coarse file list of `src/main/java/.../deser/`
  - `cell-1/recon/hot-spots-coarse.txt` — top 30 classes ranked by mention-count in closed bugs
  - `cell-1/recon/scanners/semgrep.json` — Semgrep baseline (if present)
  - `cell-1/recon/scanners/spotbugs.xml` — SpotBugs baseline (if present)

We are doing Phase 0 Cell #1 of an OSS bug-hunter experiment: hunting **correctness** (not security) bugs in `jackson-databind` deserialization. Your job is to produce a **structured inventory** so the main session can pick a Day 3 novel-hunt target list and build the Day 2 backtest dataset.

# Tasks

Do these in order. Each task has a defined output section in the final markdown.

## Task 1 — Complete JsonDeserializer subclass inventory

The coarse file list only covers `com/fasterxml/jackson/databind/deser/`. Find all concrete subclasses of these base types across the entire codebase:

- `JsonDeserializer`
- `StdDeserializer`
- `StdScalarDeserializer`
- `ContainerDeserializerBase`
- `BeanDeserializerBase`

For each subclass, record: fully qualified class name, file path (relative to the target dir), and the type it deserializes (look at the generic parameter `<T>`).

**Search hints:**
- `grep -rEn 'extends (JsonDeserializer|StdDeserializer|StdScalarDeserializer|ContainerDeserializerBase|BeanDeserializerBase)\b' src/main/java`
- Some are nested classes (inner class deserializers) — include those too.

## Task 2 — Polymorphic-type resolution surface

Locate the polymorphic-type machinery. Inventory:

- All concrete `TypeDeserializer` subclasses (file path + class name)
- All `TypeIdResolver` implementations
- All usage sites of `@JsonTypeInfo`, `@JsonSubTypes`, `@JsonTypeId` in the codebase (file:line)
- The entry-point classes/methods that decide which `TypeDeserializer` strategy applies (look for `As.*` enum branches: `As.PROPERTY`, `As.WRAPPER_ARRAY`, `As.WRAPPER_OBJECT`, `As.EXTERNAL_PROPERTY`)

## Task 3 — Deserialization pipeline entry points

Map where deserializers are *constructed* vs *invoked*. Specifically, find:

- The methods in `DeserializerFactory` / `BeanDeserializerFactory` that build deserializer instances
- The `DeserializerCache` lookup methods
- The top-level entry point: where `ObjectMapper.readValue` ultimately dispatches into a `JsonDeserializer.deserialize` call

Record method signatures with file:line. We don't need full call traces, just the entry-point locations.

## Task 4 — Recent-change hot-spots (last 12 months)

Run `git -C targets/jackson-databind log --since=12.months.ago --name-only --pretty=format: -- src/main/java/com/fasterxml/jackson/databind/deser/ src/main/java/com/fasterxml/jackson/databind/jsontype/` and aggregate by file. List the top 20 most-modified files with their commit counts. These are high-churn = elevated correctness-bug risk.

## Task 5 — Cross-reference: hot-spots vs release notes

For each of the top 10 classes in `cell-1/recon/hot-spots-coarse.txt`:
- Find the file path (resolve from the bare class name)
- Grep its class name in `cell-1/recon/releases.json` — list any release tags that mention it in the release notes body
- Report: (rank, class name, file path, mention-count from coarse, release tags mentioning it)

## Task 6 — Scanner-baseline categorization (Semgrep only — skip if missing)

If `cell-1/recon/scanners/semgrep.json` exists, group its `results[]` entries by `check_id`. Output a table: (check_id, count, brief one-line description of what rule fires on). We need this so the main session can later tell "agent finding X is just rule Y" at a glance.

# Output format

Return the entire inventory below as the body of your final message — the caller will save it to `cell-1/recon/explore-inventory.md`. Use tables liberally. Use file:line links where applicable.

```markdown
# Cell #1 Explore Inventory

**Generated:** <ISO-8601 UTC>
**Target pin:** <tag + short SHA from target-pin.json>
**Subagent:** Explore (read-only inventory pass)

## 1. JsonDeserializer subclass inventory

| FQCN | File | Deserialized type | Notes |
|---|---|---|---|
| ... | ... | ... | ... |

(N classes total)

## 2. Polymorphic-type resolution surface

### 2.1 TypeDeserializer subclasses
| FQCN | File |

### 2.2 TypeIdResolver implementations
| FQCN | File |

### 2.3 @JsonTypeInfo / @JsonSubTypes usage sites
| File | Line | Annotation | Notes |

### 2.4 Strategy dispatch entry points (As.PROPERTY / WRAPPER_ARRAY / ...)
| File | Line | Strategy branch |

## 3. Deserialization pipeline entry points

| Stage | Class | Method | File:line |
|---|---|---|---|
| Construct | DeserializerFactory | ... | ... |
| Cache lookup | DeserializerCache | ... | ... |
| Top-level dispatch | ObjectMapper.readValue → ? | ... | ... |

## 4. Recent-change hot-spots (last 12mo, by commit count)

| Rank | File | Commits (12mo) |
|---|---|---|

## 5. Hot-spot × release-note cross-reference

| Rank | Class | File | Coarse mentions | Release tags mentioning |
|---|---|---|---|---|

## 6. Semgrep baseline categorization (if available)

| check_id | Count | One-line description |
|---|---|---|

(Total: <N> findings; group_count: <M> distinct rules)

## Caveats and gaps

- Anything you couldn't fully resolve (e.g., dynamic class lookups, reflection-based dispatch you couldn't trace)
- Files you sampled but didn't read in full (Explore reads excerpts)
- Any inputs that were missing or empty
```

# Constraints

- **Read-only.** Do not edit any file. You do not have Write/Edit access; return everything in your final message.
- **No analysis or judgment calls** — those are the main session's job. You produce raw structured data.
- **No web fetches** — everything you need is on disk.
- **No guesses.** If a section can't be filled (e.g., a tool wasn't run, a file is missing), note it in "Caveats and gaps" rather than inventing entries.
- **Time-bound your exploration.** If a single task is taking >5min of grep iterations, emit what you have for that task with a caveat and move on.

# Return format

Your final message MUST contain the full inventory markdown (sections 1-6 + Caveats), followed by a one-paragraph summary at the end (≤100 words) with:
- Total counts: # JsonDeserializer subclasses, # TypeDeserializer subclasses, # @JsonTypeInfo sites, # hot-spot files
- Any major gaps (e.g., "Semgrep baseline missing — section 6 omitted")
