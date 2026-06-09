"""Open-PR PREVIEW + identity gate (plan §4 U4).

This module is deliberately READ-ONLY: it assembles everything a pull request
WOULD contain (branch, title, body, commit message, upstream repo) and reports
the gates that must pass before a human opens it — but it NEVER pushes or runs
`gh pr create`. Per .claude/rules/confirm-gh-account-before-commit.md, pushing to
a public repo is a hard gate that requires the personal GitHub identity and
explicit human confirmation. The UI surfaces this; the actual push is a manual
step (the exact commands are returned in `manual_steps`).
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import findings as fnd
import targets as tgt

PERSONAL_ACCOUNT = "mkhalid-s"          # the OSS identity (see the rule)
_REJECTED = {"failed-self-consistency", "dupe", "false-positive", "unreproducible"}


def _owner_repo(url: str | None) -> str | None:
    # anchored to a github host form; repo may contain dots; trailing .git stripped.
    m = re.match(r"^(?:https?://github\.com/|git@github\.com:|ssh://git@github\.com/)"
                 r"([^/]+)/(.+?)(?:\.git)?/?$", url or "")
    return f"{m.group(1)}/{m.group(2)}" if m else None


def _is_keeper(f: dict) -> bool:
    g = f.get("gates_full") or {}
    fix = (g.get("fix_passes_tests") or {}).get("status") == "pass"
    dupe = (g.get("dedup") or {}).get("is_duplicate") is True
    return fix and not dupe and f.get("final_status") not in _REJECTED


def gh_identity() -> dict:
    """Best-effort read of the ACTIVE gh account + git identity, so the UI can
    show whether a public-repo PR would go out under the right (personal) name."""
    out = {"active_account": None, "is_personal": False,
           "gh_token_set": bool(os.environ.get("GH_TOKEN")),
           "git_user": None, "git_email": None}
    try:
        r = subprocess.run(["gh", "auth", "status"], capture_output=True, text=True, timeout=10)
        txt = (r.stdout or "") + (r.stderr or "")
        # pick the account whose block is marked active, NOT just the first listed.
        cur = first = active = None
        for line in txt.splitlines():
            ma = re.search(r"account (\S+)", line) or re.search(r" as (\S+)", line)
            if ma:
                cur = ma.group(1)
                first = first or cur
            elif re.search(r"Active account:\s*true", line, re.I):
                active = cur
        out["active_account"] = active or first
    except Exception:
        pass
    for key, dst in (("user.name", "git_user"), ("user.email", "git_email")):
        try:
            r = subprocess.run(["git", "config", "--get", key], capture_output=True,
                               text=True, timeout=5)
            if r.returncode == 0:
                out[dst] = r.stdout.strip()
        except Exception:
            pass
    out["is_personal"] = out["active_account"] == PERSONAL_ACCOUNT
    return out


def _pr_body(f: dict, upstream: str | None, sha: str | None) -> str:
    loc = f.get("location") or ""
    repro = f.get("reproducer_src") or "(no reproducer)"
    patch = f.get("patch_text") or "(no patch)"
    g = f.get("gates_full") or {}
    rep_ok = (g.get("reproducer") or {}).get("status") == "pass"
    fix_ok = (g.get("fix_passes_tests") or {}).get("status") == "pass"
    return f"""## Summary

{f.get('summary') or ''}

**Location:** `{loc}`
**Type:** {f.get('type') or '—'} · **CWE:** {(g.get('cwe') or {}).get('cwe') or '—'}

## Evidence

{(f.get('evidence') or '').strip()}

## Reproducer

A JUnit test that fails on the buggy code and passes after the fix:

```java
{repro.strip()}
```

## Fix

```diff
{patch.strip()}
```

## Validation

- Reproducer reproduces on `{sha or 'HEAD'}`: {'yes' if rep_ok else 'unverified'}
- Fix makes the reproducer pass: {'yes' if fix_ok else 'unverified'}

---
_Proposed by the OSS Bug Hunter (agent-assisted). The reproducer + fix were
validated by non-AI test execution. Please review carefully before merging._
"""


def pr_preview(finding_id: str, target_name: str = "jackson-databind") -> dict | None:
    f = fnd.get_finding(finding_id)
    if f is None:
        return None
    t = tgt.get_target(target_name) or {}
    upstream = _owner_repo(t.get("repo"))
    sha = t.get("sha")
    short = (f.get("location") or "").split(":")[0]
    fname = Path(short).name if short else "code"
    title = f"Fix {f.get('type') or 'bug'} in {fname}"
    branch = f"oss-bug-hunter/fix-{finding_id}"

    keeper = _is_keeper(f)
    ident = gh_identity()
    blockers = []
    if not keeper:
        blockers.append("finding is not a validated keeper (needs: fix gate=pass, "
                        "not a duplicate, and survived self-consistency)")
    if f.get("patch_text") is None:
        blockers.append("no fix patch on file")
    if ident["gh_token_set"]:
        blockers.append("GH_TOKEN is set — it pins gh to the ENTERPRISE account; "
                        "`unset GH_TOKEN` before any public-repo PR")
    if not ident["is_personal"]:
        blockers.append(f"active gh account is "
                        f"{ident['active_account'] or 'unknown'}, not the personal "
                        f"{PERSONAL_ACCOUNT}; public repos reject enterprise (EMU) accounts")

    fork = f"{PERSONAL_ACCOUNT}/{target_name}"
    manual_steps = [
        "unset GH_TOKEN",
        f"gh auth switch -u {PERSONAL_ACCOUNT}",
        "gh auth status   # confirm Active account: true for the personal account",
        f"git -C targets/{target_name} checkout -b {branch}",
        f"git -C targets/{target_name} apply {f.get('patch_path') or '<patch>'}",
        f"git -C targets/{target_name} commit -am {title!r}",
        f"gh repo fork {upstream or '<upstream>'} --remote=false --clone=false  # once",
        f"git -C targets/{target_name} push -u git@github.com:{fork}.git {branch}",
        f"gh pr create --repo {upstream or '<upstream>'} --head {PERSONAL_ACCOUNT}:{branch} "
        f"--title {title!r} --body-file -",
    ]

    return {
        "finding_id": finding_id, "target": target_name, "upstream": upstream,
        "fork": fork, "branch": branch, "title": title,
        "commit_message": title, "body": _pr_body(f, upstream, sha),
        "keeper": keeper, "blockers": blockers, "identity": ident,
        "ready": keeper and not blockers,
        "manual_steps": manual_steps,
        "note": "Read-only preview. This tool does NOT push or open the PR — that "
                "is a manual, identity-confirmed step (hard gate).",
    }
