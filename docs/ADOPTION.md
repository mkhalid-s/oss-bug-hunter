# Adopting Anthropic's defending-code-reference-harness

**Decision (2026-06-09): adopt their skills, keep our engine.** We independently
built the same architecture Anthropic published (and productized as *Claude
Security*). Rather than reinvent the find/triage/patch reasoning, we vendor their
Apache-2.0 **skills** (`vendor/anthropic-skills/`) and wrap them with the parts
they explicitly *don't* provide. See the memory note `anthropic-defending-code-harness`.

## Two layers — adopt one, replace the other

| Their layer | Prereqs | Verdict |
|-------------|---------|---------|
| **Skills** `/threat-model` `/vuln-scan` `/triage` `/patch` | Claude Code only (read/write, no sandbox) | **ADOPT** — vendored, used directly |
| **Autonomous `bin/vp-sandboxed` pipeline** | Linux + Docker + gVisor + KVM; **C/C++ + ASAN only**; "not maintained, a reference not a product" | **REPLACE** with our portable multi-language daemonless verifier |

## Pipeline mapping (theirs ↔ ours)

| Reference stage | OSS Bug Hunter |
|-----------------|----------------|
| Threat model | `vendor/.../threat-model` (newly adopted) |
| Recon / partition | hunt subagents (`run_hunt_*`) |
| Find | `vendor/.../vuln-scan` (static) → candidates |
| **Verify** (reproduce in fresh container) | **`run_harness.validate_repro` — multi-language, daemonless** (must FAIL on HEAD) + self-consistency |
| Dedupe / triage | `vendor/.../triage` + our dedup/CWE gates |
| Report | findings YAML + the React findings board |
| Patch + validate | `vendor/.../patch` proposes → `run_harness.validate_fix` disposes (flip green, contained) → `orchestrate` retry loop |
| *(missing in theirs)* outer loop / discovery / PR | our §12 autonomy: discovery → scheduler → gated-PR (`pr.py`) |

## Artifact mapping (their JSON ↔ our finding)

Their `VULN-FINDINGS.json` finding `{id, file, line, category, severity, title,
description}` maps to our finding YAML:

| Theirs | Ours |
|--------|------|
| `id` | `finding_id` |
| `file` + `line` | `location` |
| `category` | `type` (+ feeds the `cwe` gate) |
| `severity` | *(new — adopt their severity discipline: write reachability/attacker-control/preconditions/blast-radius BEFORE assigning)* |
| `title` | `summary` |
| `description` | `evidence` |
| *(ours adds)* | `language`, `target`, `gates{reproducer,dedup,cwe,fix_passes_tests}`, `final_status` — the execution-verification their static scan lacks |

## Their stated gaps = our autonomy layer

| Reference says (verbatim) | We provide |
|---------------------------|------------|
| "single pre-configured target… add an **outer loop** yourself" | discovery + scheduler (§12.3/§12.5) |
| "autonomous triage and patching are still open issues" | self-correcting `orchestrate` + non-AI gates |
| "patch files only" / "not always upstreamable" | gated-PR draft + identity gate (§12.6, `pr.py`) |
| static-only outside C/C++ → "expect more false positives" | **multi-language execution-verification** (the false-positive killer) |
| needs Docker + gVisor + KVM | **daemonless** local/trust-gated backend |

## Phased plan
1. **Vendor the skills** (DONE) — `vendor/anthropic-skills/` + this doc.
2. **Ingest skill output → finding scaffolds** (DONE) — `tool/ingest.py` maps
   `VULN-FINDINGS.json`/`TRIAGE.json` → our scaffolds (`proposed` column, carrying
   `severity` + `source` + `reproducer_hint`; triage-rejected findings skipped).
3. **Close ingest → Verify** (DONE for the bug side — #54). `tool/llm_repro_provider.py`
   builds a non-Java reproducer (AI proposes a FAILING test from `reproducer_hint`/
   location/evidence) and `pipeline.verify_finding` runs it via
   `run_harness.validate_repro` (non-AI disposes), moving an ingested finding from
   `proposed` → `reproduced`. Proven end-to-end on py-1 (real daemonless pytest).
   **#55 (DONE):** `tool/llm_fix_builder.py` adds the non-Java fix-builder + retry
   provider, wired into orchestrate steps 2/3. The full reproduce→fix→retry loop now
   works for **all five languages** — proven end-to-end on py-1 (`orchestrate_finding`
   → fixed). So **ingest → reproduced → fixed is closed**; Phase 3 (outer loop) is next.
4. **Build the outer loop** — discovery → scheduler → gated-PR (§12). That loop is
   the product; their harness is per-target by design.

> Note: the "multi-language execution-verification" advantage above is real but
> **gated on step 3** for non-Java targets — our engine *can* verify any language,
> but only once a reproducer exists. Don't read the tables as "already closed."

## Positioning
> OSS Bug Hunter = the autonomous **outer loop** (discover → schedule → gated-PR) +
> portable **multi-language** execution-verification, wrapping Anthropic's
> Apache-2.0 per-target find/triage/patch skills.
