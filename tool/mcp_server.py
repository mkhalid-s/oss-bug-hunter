"""MCP server exposing the OSS bug-hunter pipeline as tools.

Shares state with the FastAPI dashboard via the filesystem (cell-1/). Either
surface can drive the pipeline; the other reflects updates on next poll/read.

Add to Claude Code MCP config (~/.claude/settings.json or a project mcp.json),
substituting <PROJECT_ROOT> with the absolute path to this checkout:

    {
      "mcpServers": {
        "oss-bug-hunter": {
          "command": "<PROJECT_ROOT>/.venv/bin/python",
          "args": ["<PROJECT_ROOT>/tool/mcp_server.py"]
        }
      }
    }

Tools exposed:
  - bug_hunter.status: pipeline state (which steps done, what's next)
  - bug_hunter.run_step: execute an auto step
  - bug_hunter.list_artifacts: enumerate all artifact names + existence
  - bug_hunter.read_artifact: read one artifact's content
  - bug_hunter.write_artifact: write to a whitelisted output (e.g., shortlist)
  - bug_hunter.list_backtest_entries: per-issue backtest state
  - bug_hunter.run_backtest_subagent: run claude-driver for one backtest entry
  - bug_hunter.label_backtest_subagent: auto-label one backtest entry
  - bug_hunter.run_explore_subagent: run claude-driver for the Day-1 Explore step
  - bug_hunter.run_hunt_subagent: run claude-driver for one hunt pass
"""
from __future__ import annotations

import asyncio
import json
import logging
import sys
from pathlib import Path

# Import pipeline.py from tool/
sys.path.insert(0, str(Path(__file__).resolve().parent))
import pipeline as pl  # noqa: E402

from mcp.server import Server, NotificationOptions
from mcp.server.models import InitializationOptions
import mcp.server.stdio
import mcp.types as types

# Logging goes to stderr — stdout is reserved for MCP protocol messages.
logging.basicConfig(level=logging.INFO, stream=sys.stderr,
                    format="[mcp_server] %(levelname)s %(message)s")
log = logging.getLogger(__name__)

server: Server = Server("oss-bug-hunter")


# ---------- tool definitions ----------
TOOLS: list[types.Tool] = [
    types.Tool(
        name="bug_hunter.status",
        description=(
            "Snapshot of the Cell-#1 pipeline: which of the 17 steps are done, "
            "which is next, and per-step metadata (kind, instructions, output paths). "
            "Call this first to see where the pipeline is."
        ),
        inputSchema={"type": "object", "properties": {}, "additionalProperties": False},
    ),
    types.Tool(
        name="bug_hunter.run_step",
        description=(
            "Execute an AUTO step by id (e.g. 'day1-recon', 'day2-build', 'day2-score'). "
            "Returns stdout/stderr/returncode/elapsed_s. Will fail if the step is "
            "human-kind or its dependencies aren't met. Use bug_hunter.status to "
            "find the next auto step."
        ),
        inputSchema={
            "type": "object",
            "properties": {"step_id": {"type": "string"}},
            "required": ["step_id"],
            "additionalProperties": False,
        },
    ),
    types.Tool(
        name="bug_hunter.list_artifacts",
        description="List all whitelisted artifacts and whether each exists yet.",
        inputSchema={"type": "object", "properties": {}, "additionalProperties": False},
    ),
    types.Tool(
        name="bug_hunter.read_artifact",
        description=(
            "Read one artifact by name (see bug_hunter.list_artifacts). Returns "
            "{path, exists, content}."
        ),
        inputSchema={
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
            "additionalProperties": False,
        },
    ),
    types.Tool(
        name="bug_hunter.write_artifact",
        description=(
            "Write to a whitelisted output (e.g. 'shortlist', 'explore-inventory'). "
            "Body is the full file content. Refuses to write to non-output paths."
        ),
        inputSchema={
            "type": "object",
            "properties": {"name": {"type": "string"}, "content": {"type": "string"}},
            "required": ["name", "content"],
            "additionalProperties": False,
        },
    ),
    types.Tool(
        name="bug_hunter.list_backtest_entries",
        description=(
            "Per-issue state for the Day-2 backtest: which entries have findings "
            "populated, labels filled, matched_rank assigned."
        ),
        inputSchema={"type": "object", "properties": {}, "additionalProperties": False},
    ),
    types.Tool(
        name="bug_hunter.run_backtest_subagent",
        description=(
            "Spawn `claude -p` headless against one backtest entry's prompt, "
            "parse the findings YAML, save to runs/<issue>/findings.yaml. Uses "
            "haiku/low by default (~3-5min per entry on Java codebase)."
        ),
        inputSchema={
            "type": "object",
            "properties": {"issue_num": {"type": "string"}},
            "required": ["issue_num"],
            "additionalProperties": False,
        },
    ),
    types.Tool(
        name="bug_hunter.run_backtest_batch",
        description=(
            "Run MULTIPLE backtest subagents IN PARALLEL via "
            "claude_driver.run_claude_batch. Omit issue_nums to run every prepared "
            "entry. max_parallel bounds the fan-out (default 4, capped 10); each "
            "call retries transient failures. Returns per-issue results + a summary "
            "{total, succeeded, failed}."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "issue_nums": {"type": "array", "items": {"type": "string"}},
                "max_parallel": {"type": "integer", "minimum": 1, "maximum": 10},
            },
            "additionalProperties": False,
        },
    ),
    types.Tool(
        name="bug_hunter.label_backtest_subagent",
        description=(
            "Auto-label one backtest entry's findings against the known fix-commit "
            "diff. Writes runs/<issue>/labels.yaml with _auto_labeled=true. Human "
            "review still recommended (labeler is itself an LLM)."
        ),
        inputSchema={
            "type": "object",
            "properties": {"issue_num": {"type": "string"}},
            "required": ["issue_num"],
            "additionalProperties": False,
        },
    ),
    types.Tool(
        name="bug_hunter.run_explore_subagent",
        description=(
            "Spawn `claude -p` for the Day-1 Explore inventory; save full response "
            "to cell-1/recon/explore-inventory.md."
        ),
        inputSchema={"type": "object", "properties": {}, "additionalProperties": False},
    ),
    types.Tool(
        name="bug_hunter.run_hunt_subagent",
        description=(
            "Spawn `claude -p` for one novel-hunt pass. angle: code-quality | "
            "edge-case. pass_num: 1 | 2 | 3 (Day 3 = 1; Day 4 = 2,3). Saves to "
            "findings-pass<N>.yaml."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "angle": {"type": "string", "enum": ["code-quality", "edge-case"]},
                "pass_num": {"type": "integer", "enum": [1, 2, 3]},
            },
            "required": ["angle", "pass_num"],
            "additionalProperties": False,
        },
    ),
    types.Tool(
        name="bug_hunter.run_hunt_batch",
        description=(
            "Run multiple novel-hunt passes IN PARALLEL via run_claude_batch. "
            "Omit `passes` to run the four Day-4 self-consistency passes "
            "(code-quality x{2,3}, edge-case x{2,3}); else pass a list of "
            "[angle, pass_num] pairs. max_parallel bounds fan-out (default 4)."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "passes": {"type": "array", "items": {
                    "type": "array", "prefixItems": [
                        {"type": "string", "enum": ["code-quality", "edge-case"]},
                        {"type": "integer", "enum": [1, 2, 3]},
                    ],
                }},
                "max_parallel": {"type": "integer", "minimum": 1, "maximum": 10},
            },
            "additionalProperties": False,
        },
    ),
    types.Tool(
        name="bug_hunter.run_repro_subagent",
        description=(
            "Build a JUnit reproducer .java for one finding (reads its validation "
            "scaffold, spawns `claude -p`, extracts the ```java block, writes "
            "cell-1/hunt/repros/<id>.java). Does NOT run the test — that's the "
            "non-AI validator `day3-hunt.py run-repros`."
        ),
        inputSchema={
            "type": "object",
            "properties": {"finding_id": {"type": "string"}},
            "required": ["finding_id"],
            "additionalProperties": False,
        },
    ),
    types.Tool(
        name="bug_hunter.run_repro_batch",
        description=(
            "Build reproducers for several findings IN PARALLEL via run_claude_batch. "
            "Omit `finding_ids` to build for every validation scaffold still missing "
            "a .java. max_parallel bounds fan-out (default 4)."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "finding_ids": {"type": "array", "items": {"type": "string"}},
                "max_parallel": {"type": "integer", "minimum": 1, "maximum": 10},
            },
            "additionalProperties": False,
        },
    ),
    types.Tool(
        name="bug_hunter.run_fix_subagent",
        description=(
            "Build a fix PATCH for one finding (reads its scaffold + reproducer, "
            "spawns `claude -p`, extracts the ```diff block, writes "
            "cell-1/hunt/patches/<id>.patch). Needs the finding's reproducer .java. "
            "Does NOT apply/run it — that's the non-AI `day3-hunt.py run-fixes`."
        ),
        inputSchema={
            "type": "object",
            "properties": {"finding_id": {"type": "string"}},
            "required": ["finding_id"],
            "additionalProperties": False,
        },
    ),
    types.Tool(
        name="bug_hunter.run_fix_batch",
        description=(
            "Build fix patches for several findings IN PARALLEL via run_claude_batch. "
            "Omit `finding_ids` to build for every finding that has a reproducer .java "
            "but no patch yet. max_parallel bounds fan-out (default 4)."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "finding_ids": {"type": "array", "items": {"type": "string"}},
                "max_parallel": {"type": "integer", "minimum": 1, "maximum": 10},
            },
            "additionalProperties": False,
        },
    ),
    types.Tool(
        name="bug_hunter.suggest_gates",
        description=(
            "Deterministic (non-AI) advisory auto-fill of blank dedup (references) "
            "+ cwe gates from data already in the scaffolds. Fills only blanks; "
            "never sets is_duplicate or final_status."
        ),
        inputSchema={"type": "object", "properties": {}, "additionalProperties": False},
    ),
    types.Tool(
        name="bug_hunter.orchestrate",
        description=(
            "Self-correcting loop per finding via the converged multi-language "
            "engine (run_harness.orchestrate): reproduce → fix → retry-with-feedback. "
            "Reads each finding's language/target from its scaffold (Java + "
            "Python/Go/Rust/JS). Java builds the reproducer+fix via the LLM and "
            "validates LOCALLY (no Docker); non-Java findings need a pre-existing "
            "reproducer+patch and validate local (trusted) or in a container "
            "(untrusted → docker/podman). Omit `finding_ids` for every scaffold. "
            "max_fix_attempts = retries after the first (default 2). `network`/"
            "`worktree` are optional overrides applied to ALL findings (normally "
            "leave unset — derived per finding)."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "finding_ids": {"type": "array", "items": {"type": "string"}},
                "max_fix_attempts": {"type": "integer", "minimum": 0, "maximum": 5},
                "worktree": {"type": "string"},
                "network": {"type": "string"},
                "model": {"type": "string"},
                "effort": {"type": "string"},
            },
            "additionalProperties": False,
        },
    ),
]


# ---------- dispatch ----------
@server.list_tools()
async def list_tools() -> list[types.Tool]:
    return TOOLS


def _text(payload) -> list[types.TextContent]:
    """Render a payload to MCP TextContent. P1-9: all payloads pass through
    the unified envelope shape from pipeline.envelope_success/envelope_error.

    If `payload` already has `ok` set (came from pipeline subagent funcs that
    set ok directly), pass through unchanged. Otherwise wrap as success.
    """
    if isinstance(payload, dict) and "ok" in payload:
        body = payload
    elif isinstance(payload, dict):
        body = pl.envelope_success(**payload)
    else:
        body = pl.envelope_success(data=payload)
    return [types.TextContent(type="text", text=json.dumps(body, indent=2, default=str))]


def _err(code: str, message: str | None = None) -> list[types.TextContent]:
    return [types.TextContent(type="text",
                              text=json.dumps(pl.envelope_error(code, message),
                                              indent=2, default=str))]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    log.info("call_tool: %s args=%s", name, list(arguments.keys()))
    try:
        if name == "bug_hunter.status":
            return _text(pl.get_state())
        if name == "bug_hunter.run_step":
            return _text(pl.run_step(arguments["step_id"]))
        if name == "bug_hunter.list_artifacts":
            return _text({"artifacts": pl.list_artifacts()})
        if name == "bug_hunter.read_artifact":
            r = pl.get_artifact(arguments["name"])
            if r is None:
                return _err("unknown_artifact",
                            f"Artifact {arguments['name']!r} not in whitelist.")
            return _text(r)
        if name == "bug_hunter.write_artifact":
            return _text(pl.write_file(arguments["name"], arguments["content"]))
        if name == "bug_hunter.list_backtest_entries":
            return _text({"entries": pl.list_backtest_entries()})
        if name == "bug_hunter.run_backtest_subagent":
            return _text(pl.run_backtest_subagent(arguments["issue_num"]))
        if name == "bug_hunter.run_backtest_batch":
            return _text(pl.run_backtest_batch(
                arguments.get("issue_nums"),
                max(1, min(int(arguments.get("max_parallel", 4)), 10)),
            ))
        if name == "bug_hunter.label_backtest_subagent":
            return _text(pl.label_backtest_subagent(arguments["issue_num"]))
        if name == "bug_hunter.run_explore_subagent":
            return _text(pl.run_explore_subagent())
        if name == "bug_hunter.run_hunt_subagent":
            return _text(pl.run_hunt_subagent(arguments["angle"], arguments["pass_num"]))
        if name == "bug_hunter.run_hunt_batch":
            passes = arguments.get("passes")
            return _text(pl.run_hunt_batch(
                [(a, int(n)) for a, n in passes] if passes else None,
                max(1, min(int(arguments.get("max_parallel", 4)), 10)),
            ))
        if name == "bug_hunter.run_repro_subagent":
            return _text(pl.run_repro_subagent(arguments["finding_id"]))
        if name == "bug_hunter.run_repro_batch":
            return _text(pl.run_repro_batch(
                arguments.get("finding_ids"),
                max(1, min(int(arguments.get("max_parallel", 4)), 10)),
            ))
        if name == "bug_hunter.run_fix_subagent":
            return _text(pl.run_fix_subagent(arguments["finding_id"]))
        if name == "bug_hunter.run_fix_batch":
            return _text(pl.run_fix_batch(
                arguments.get("finding_ids"),
                max(1, min(int(arguments.get("max_parallel", 4)), 10)),
            ))
        if name == "bug_hunter.suggest_gates":
            return _text(pl.suggest_gates())
        if name == "bug_hunter.orchestrate":
            okw = {k: arguments[k] for k in ("model", "effort") if arguments.get(k)}
            return _text(pl.orchestrate(
                arguments.get("finding_ids"),
                max(0, min(int(arguments.get("max_fix_attempts", 2)), 5)),
                arguments.get("worktree"), arguments.get("network"),
                **okw,
            ))
        return _err("internal", f"Unknown tool name: {name!r}")
    except Exception as e:
        log.exception("tool error")
        return _err("internal", f"{type(e).__name__}: {e}")


# ---------- entry point ----------
async def main() -> None:
    async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            InitializationOptions(
                server_name="oss-bug-hunter",
                server_version="0.1.0",
                capabilities=server.get_capabilities(
                    notification_options=NotificationOptions(),
                    experimental_capabilities={},
                ),
            ),
        )


if __name__ == "__main__":
    asyncio.run(main())
