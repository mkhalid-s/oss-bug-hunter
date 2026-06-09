# Vendored: Anthropic `defending-code-reference-harness` skills

These are the Claude Code **skills** from Anthropic's Apache-2.0
[defending-code-reference-harness](https://github.com/anthropics/defending-code-reference-harness),
vendored here so OSS Bug Hunter can reuse the battle-tested find / triage / patch
**reasoning** instead of reinventing it. See `../../docs/ADOPTION.md` for the full
build-vs-adopt rationale and how these map onto our engine.

License: **Apache-2.0** (`./LICENSE`, `./NOTICE`). Copyright 2026 Anthropic PBC.

## What's here (`skills/`)
| Skill | Role | Emits |
|-------|------|-------|
| `threat-model/` | Define what counts as a vuln before scanning | `THREAT_MODEL.md` |
| `vuln-scan/` | **Read-only** static review, parallel per focus area | `VULN-FINDINGS.json` + `.md` |
| `triage/` | Dedupe by root cause + rank (reachability/blast-radius) | `TRIAGE.json` + `.md` |
| `patch/` | Generate candidate fixes with an independent reviewer | `PATCHES/` |
| `quickstart/`, `customize/`, `_lib/` | Orientation, porting to a new stack, shared checkpoint helper | — |

All of the above are **read/write-only and run unsandboxed in Claude Code** — no
Docker/gVisor needed (that requirement is only for the harness's autonomous
`bin/vp-sandboxed` pipeline, which we deliberately do NOT adopt).

## What we adopt vs. replace
- **ADOPT** the skills above — the AI reasoning for find/triage/patch + the
  `THREAT_MODEL.md` / `VULN-FINDINGS.json` / `TRIAGE.json` / `PATCHES/` artifact
  shapes (portable to Anthropic's managed "Claude Security").
- **REPLACE** their `vp-sandboxed` execution pipeline (Linux+Docker+gVisor+KVM,
  C/C++ + ASAN only) with our **portable, multi-language, daemonless** verifier
  (`tool/run_harness.py` + `tool/adapters.py` + `tool/exec_backend.py`), which is
  the execution-verification layer their static scan explicitly lacks outside C/C++.

These files are vendored **unmodified**. Our adaptations (artifact ↔ finding
mapping, wiring to the verifier) live in OSS Bug Hunter code, not in this tree.
