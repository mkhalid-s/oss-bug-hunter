"""FastAPI dashboard server for the OSS bug-hunter pipeline.

Reuses tool/pipeline.py for state + step execution.

Security (post P0-7 + P0-9 + P1-11):
  - Bound to 127.0.0.1 only.
  - Per-launch bearer token required on every `/api/*` route.
    Token is printed to stderr at startup; dashboard HTML embeds it for app.js.
  - Host header allowlist (env-configurable for codespace port-forwarding).
  - Long-running endpoints (run_step + run_*_subagent + label_*) are wrapped
    in asyncio.to_thread so /api/status polls never block on a multi-minute
    claude -p call (P1-11).
  - /api/run/{step_id} executes WHITELISTED scripts only (steps in PIPELINE).
  - /api/artifact/{name} reads WHITELISTED paths only.
  - /api/write/{name} writes WHITELISTED output paths only.
  - claude -p subagents run with --allowedTools Read/Glob/Grep by default (P0-9).

Run:
  .venv/bin/python tool/server.py
  # or: make dashboard
"""
from __future__ import annotations

import asyncio
import json
import os
import secrets
import sys
from contextlib import asynccontextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import pipeline as pl  # noqa: E402
import run_store  # noqa: E402
import run_harness  # noqa: E402
import findings as fnd  # noqa: E402
import targets as tgt  # noqa: E402
import pr as prmod  # noqa: E402
import pr_draft as prdraft  # noqa: E402

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
import uvicorn

WEB_DIR = Path(__file__).parent / "web"


@asynccontextmanager
async def _lifespan(_app: "FastAPI"):
    run_store.init_db(str(pl.CELL / ".runs.db"))   # job/run store (plan §4.2)
    yield


app = FastAPI(title="OSS Bug Hunter — Cell #1", lifespan=_lifespan)


# ---- P1-9 exception handler ----
# All HTTPException responses now follow the unified envelope shape from
# tool/pipeline.py: {ok: false, error: {code, message, status}}. Existing
# 401/403 middleware responses upgraded to the same shape below.
@app.exception_handler(HTTPException)
async def _http_exception_handler(request: Request, exc: HTTPException):
    code_map = {400: "bad_request", 401: "unauthorized", 403: "forbidden",
                404: "not_found", 500: "internal", 503: "unavailable"}
    code = code_map.get(exc.status_code, "internal")
    return JSONResponse(
        status_code=exc.status_code,
        content={"ok": False, "error": {"code": code, "message": str(exc.detail),
                                          "status": exc.status_code}},
    )

# ---- P0-7 auth setup ----
# Per-launch random token. Required on every /api/* request via
# Authorization: Bearer <token>. Defeats DNS-rebinding + cross-origin RCE
# from any browser tab — the attacker can't obtain the token because
# cross-origin reads of the dashboard HTML are SOP-blocked.
AUTH_TOKEN = secrets.token_urlsafe(32)

# Host allowlist. Strict default is localhost only; users running in a
# codespace / dev container can extend via env var to include their forwarded
# host. The bearer token is the primary defense; the Host check is
# defense-in-depth against DNS rebinding.
_DEFAULT_HOSTS = "127.0.0.1:8765,localhost:8765,127.0.0.1,localhost"
HOST_ALLOWLIST = {h.strip() for h in os.environ.get(
    "OSS_BUG_HUNTER_HOST_ALLOWLIST", _DEFAULT_HOSTS
).split(",") if h.strip()}

_MAX_WRITE_BYTES = 1 * 1024 * 1024  # 1 MiB — guard /api/write against oversized payloads


@app.middleware("http")
async def auth_and_host(request: Request, call_next):
    path = request.url.path
    if path.startswith("/api/"):
        # Bearer token check (P0-7), constant-time. SSE: the browser EventSource
        # API cannot set an Authorization header, so the /stream route also
        # accepts the token via ?token= (B2 fix). Same per-launch token; server
        # is loopback-only, so query-token leakage is limited to local logs.
        auth = request.headers.get("authorization", "")
        ok = secrets.compare_digest(auth, f"Bearer {AUTH_TOKEN}")
        if not ok and path.endswith("/stream"):
            ok = secrets.compare_digest(request.query_params.get("token", ""),
                                        AUTH_TOKEN)
        if not ok:
            return JSONResponse(
                status_code=401,
                content={"ok": False, "error": {
                    "code": "unauthorized",
                    "message": "Missing or invalid bearer token. "
                               "Tokens are printed at server startup; "
                               "reload the dashboard HTML to refresh window.AUTH_TOKEN.",
                    "status": 401,
                }},
            )
        # Host allowlist (defense-in-depth against DNS rebinding)
        host = (request.headers.get("host") or "").lower()
        if host not in HOST_ALLOWLIST:
            return JSONResponse(
                status_code=403,
                content={"ok": False, "error": {
                    "code": "forbidden",
                    "message": f"Unexpected Host header: {host}. "
                               f"Extend via OSS_BUG_HUNTER_HOST_ALLOWLIST env var "
                               f"(currently: {sorted(HOST_ALLOWLIST)}).",
                    "status": 403,
                }},
            )
    return await call_next(request)


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    html = (WEB_DIR / "index.html").read_text()
    # P0-7: inject auth token into the HTML so app.js can use it. Same-origin
    # JS receives the token; cross-origin readers (DNS-rebind, malicious tabs)
    # cannot read the HTML body due to SOP.
    inject = f"<script>window.AUTH_TOKEN = {json.dumps(AUTH_TOKEN)};</script>"
    if "</head>" in html:
        return html.replace("</head>", f"{inject}\n</head>", 1)
    return f"{inject}\n{html}"  # fallback if no <head>


@app.get("/runs", response_class=HTMLResponse)
def runs_page() -> str:
    """U0 spike: a vanilla live-runs page proving the SSE seam in a browser. The
    production UI is React+Vite+Mantine (plan §4)."""
    html = (WEB_DIR / "runs.html").read_text()
    inject = f"<script>window.AUTH_TOKEN = {json.dumps(AUTH_TOKEN)};</script>"
    if "</head>" in html:
        return html.replace("</head>", f"{inject}\n</head>", 1)
    return f"{inject}\n{html}"


# ---- React U0 app (plan §4): FastAPI serves the built Vite bundle (no Node at
# runtime). Assets mounted under /app/assets; index.html served with the token
# injected (SPA fallback for client routes). `cd tool/webapp && npm run build`.
WEBAPP_DIST = Path(__file__).parent / "webapp" / "dist"
if (WEBAPP_DIST / "assets").is_dir():
    app.mount("/app/assets", StaticFiles(directory=str(WEBAPP_DIST / "assets")),
              name="webapp-assets")


@app.get("/app", response_class=HTMLResponse)
@app.get("/app/{full_path:path}", response_class=HTMLResponse)
def webapp(full_path: str = "") -> HTMLResponse:
    index = WEBAPP_DIST / "index.html"
    if not index.exists():
        return HTMLResponse(
            "<h1>Web app not built</h1><p>Build it: "
            "<code>cd tool/webapp &amp;&amp; npm install "
            "--registry=https://registry.npmjs.org/ &amp;&amp; npm run build</code>, "
            "then restart the server. (Or use the vanilla page at "
            "<a href='/runs'>/runs</a>.)</p>", status_code=503)
    html = index.read_text()
    inject = f"<script>window.AUTH_TOKEN = {json.dumps(AUTH_TOKEN)};</script>"
    if "</head>" in html:
        html = html.replace("</head>", f"{inject}\n</head>", 1)
    return HTMLResponse(html)


# ---- Job/run seam (plan §4.2): run_id + persisted status + live SSE log ----
def _sse(data: dict, event: str = "", id: object = None) -> str:
    out = ""
    if id is not None:
        out += f"id: {id}\n"
    if event:
        out += f"event: {event}\n"
    return out + f"data: {json.dumps(data)}\n\n"


def _job_demo(run_id: str, emit) -> dict:
    """Fast synthetic job — proves the SSE stream without a multi-minute build."""
    import time as _t
    n = 8
    for i in range(n):
        emit(f"[demo] step {i + 1}/{n} working…")
        _t.sleep(0.4)
    emit("[demo] complete")
    return {"exit": 0, "backend": "none"}


def _job_validate_repro(run_id: str, emit) -> dict:
    """Run the real reproducer validator (exec-backend auto-select), streaming
    every line live."""
    p = run_store.get_run(run_id)["params"]
    v = run_harness.validate_repro(
        p["worktree"], p["fqcn"], p["test_file"],
        trusted=bool(p.get("trusted", False)),
        network=p.get("network", "bridge"),
        lang=p.get("lang", "java"),
        log=lambda line: emit(line))
    return {"exit": v.exit_code(), "outcome": v.outcome.value,
            "tests_run": v.tests_run, "failures": v.failures, "errors": v.errors}


def _job_validate_fix(run_id: str, emit) -> dict:
    """Apply a candidate fix patch + re-run the isolated reproducer (fix gate)."""
    p = run_store.get_run(run_id)["params"]
    v = run_harness.validate_fix(
        p["worktree"], p["fqcn"], p["test_file"], p["patch"],
        trusted=bool(p.get("trusted", False)),
        network=p.get("network", "bridge"),
        lang=p.get("lang", "java"),
        log=lambda line: emit(line))
    return {"exit": v.exit_code(), "outcome": v.outcome.value,
            "tests_run": v.tests_run, "failures": v.failures, "errors": v.errors}


_ORCH_EXIT = {"fixed": 0, "fix-failed-after-retries": 1, "does-not-reproduce": 2}


def _job_orchestrate(run_id: str, emit) -> dict:
    """Self-correcting loop: reproduce → fix (→ LLM retry). Non-AI validators decide.

    Preferred: pass `finding_id` — this runs the SAME converged, scaffold-driven,
    multi-language engine as `/api/orchestrate` + the MCP tool (the UI uses this).
    Legacy: pass explicit worktree/fqcn/test_file/patch/lang for a one-off run."""
    p = run_store.get_run(run_id)["params"]
    fid = p.get("finding_id")
    if fid:
        r = pl.orchestrate_finding(
            fid, max_fix_attempts=int(p.get("max_retries", 2)),
            network=p.get("network") or None, log=lambda line: emit(line))
        oc = r.get("outcome")
        return {"exit": _ORCH_EXIT.get(oc, 3), "status_detail": oc,
                "validated": r.get("validated"), "attempts": r.get("attempts"),
                "fixed": oc == "fixed",
                "reproduced": oc in ("fixed", "fix-failed-after-retries"),
                "detail": r.get("detail") or oc}
    # legacy explicit-params path (no finding scaffold)
    missing = [k for k in ("worktree", "fqcn", "test_file", "patch") if not p.get(k)]
    if missing:
        raise ValueError(f"orchestrate needs finding_id, or all of {missing}")
    provider = None
    if p.get("finding_yaml"):
        import yaml
        import llm_fix_provider
        scaffold = yaml.safe_load(Path(p["finding_yaml"]).read_text()) or {}
        provider = llm_fix_provider.make_llm_fix_provider(
            scaffold, Path(p["test_file"]).read_text(),
            str(Path(p["patch"]).parent), log=lambda line: emit(line))
    res = run_harness.orchestrate(
        p["worktree"], p["fqcn"], p["test_file"], p["patch"],
        trusted=bool(p.get("trusted", False)),
        network=p.get("network", "bridge"),
        fix_provider=provider,
        max_retries=int(p.get("max_retries", 0)),
        lang=p.get("lang", "java"),
        log=lambda line: emit(line))
    return {"exit": res.exit_code(), "status_detail": res.status,
            "reproduced": res.reproduced, "fixed": res.fixed,
            "attempts": res.attempts, "detail": res.detail}


def _job_add_target(run_id: str, emit) -> dict:
    """Clone a repo by URL, detect its language, write the metadata sidecar."""
    p = run_store.get_run(run_id)["params"]
    res = tgt.add_target(p["url"], name=p.get("name") or None, sha=p.get("sha") or None,
                         trusted=bool(p.get("trusted", False)), log=lambda line: emit(line))
    return {"exit": 0, **res}


JOB_KINDS = {"demo": _job_demo, "validate-repro": _job_validate_repro,
             "validate-fix": _job_validate_fix, "orchestrate": _job_orchestrate,
             "add-target": _job_add_target}


@app.post("/api/runs")
async def create_run_ep(request: Request) -> dict:
    body = await request.json()
    kind = body.get("kind")
    params = body.get("params", {}) or {}
    fn = JOB_KINDS.get(kind)
    if not fn:
        raise HTTPException(400, f"unknown run kind: {kind!r} (have {sorted(JOB_KINDS)})")
    run_id = run_store.submit_job(kind, params, fn)
    return pl.envelope_success(run_id=run_id)


@app.get("/api/runs")
def list_runs_ep() -> dict:
    return pl.envelope_success(runs=run_store.list_runs(50))


@app.get("/api/runs/{run_id}")
def get_run_ep(run_id: str) -> dict:
    r = run_store.get_run(run_id)
    if r is None:
        raise HTTPException(404, f"unknown run: {run_id}")
    return pl.envelope_success(**r)


@app.get("/api/runs/{run_id}/stream")
async def stream_run_ep(run_id: str, request: Request, token: str = ""):
    r = run_store.get_run(run_id)
    if r is None:
        raise HTTPException(404, f"unknown run: {run_id}")
    loop = asyncio.get_running_loop()
    q: asyncio.Queue = asyncio.Queue()

    def sink(event: dict) -> None:
        loop.call_soon_threadsafe(q.put_nowait, event)

    last = int(request.headers.get("last-event-id", "0") or "0")

    async def gen():
        run_store.subscribe(run_id, sink)         # subscribe FIRST -> no gap
        replayed = last

        def drain_persisted():
            # yield any persisted log lines past `replayed` (closes the lost-tail
            # race: lines written between a snapshot and a terminal check).
            nonlocal replayed
            out = []
            for seq, stream, line in run_store.get_logs(run_id, after=replayed):
                replayed = seq
                out.append(_sse({"seq": seq, "stream": stream, "line": line},
                                event="log", id=seq))
            return out

        try:
            for frame in drain_persisted():        # replay buffer (late subscribers)
                yield frame
            cur = run_store.get_run(run_id)
            if cur and cur["status"] in run_store.TERMINAL:
                for frame in drain_persisted():    # flush tail before done
                    yield frame
                yield _sse({"status": cur["status"], "exit": cur["exit_code"]},
                           event="done")
                return
            while True:
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield ": ping\n\n"                # heartbeat: keep tunnels alive
                    continue
                if ev["type"] == "log":
                    if ev["seq"] <= replayed:         # dedup vs replayed
                        continue
                    replayed = ev["seq"]
                    yield _sse({"seq": ev["seq"], "stream": ev["stream"],
                                "line": ev["line"]}, event="log", id=ev["seq"])
                elif ev["type"] == "done":
                    for frame in drain_persisted():  # flush tail before done
                        yield frame
                    yield _sse(ev, event="done")
                    return
                else:
                    yield _sse(ev, event=ev.get("type", "msg"))
        finally:
            run_store.unsubscribe(run_id, sink)

    return StreamingResponse(
        gen(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no",
                 "Connection": "keep-alive"})


# ---- Findings (U2 board + detail, plan §4.3) ----
@app.get("/api/findings")
def list_findings_ep() -> dict:
    return pl.envelope_success(findings=fnd.list_findings())


@app.get("/api/findings/{fid}")
def get_finding_ep(fid: str) -> dict:
    f = fnd.get_finding(fid)
    if f is None:
        raise HTTPException(404, f"unknown finding: {fid}")
    return pl.envelope_success(**f)


@app.get("/api/findings/{fid}/pr-preview")
def pr_preview_ep(fid: str) -> dict:
    # READ-ONLY: assembles the PR + the identity gate; never pushes (hard gate).
    p = prmod.pr_preview(fid)
    if p is None:
        raise HTTPException(404, f"unknown finding: {fid}")
    return pl.envelope_success(**p)


# ---- gated-PR DRAFT queue (Phase 3 §12.6) — the human review queue; NEVER pushes ----
@app.get("/api/pr-drafts")
def list_pr_drafts_ep() -> dict:
    return pl.envelope_success(drafts=prdraft.list_drafts())


@app.get("/api/pr-drafts/{fid}")
def get_pr_draft_ep(fid: str) -> dict:
    d = prdraft.get_draft(fid)
    if d is None:
        raise HTTPException(404, f"no draft: {fid}")
    return pl.envelope_success(**d)


@app.post("/api/pr-drafts/{fid}")
async def queue_pr_draft_ep(fid: str, request: Request) -> dict:
    body = await _json_body(request)
    r = prdraft.queue_draft(fid, body.get("target") or "jackson-databind",
                            force=bool(body.get("force", False)))
    if not r.get("ok"):
        # 409 when it's just not a ready keeper (surface the blockers); else 400.
        raise HTTPException(409 if r.get("blockers") else 400,
                            r.get("error") or "; ".join(r.get("blockers", [])))
    return pl.envelope_success(**r["draft"])


@app.post("/api/pr-drafts/{fid}/decide")
async def decide_pr_draft_ep(fid: str, request: Request) -> dict:
    body = await _json_body(request)
    r = prdraft.decide_draft(fid, (body.get("decision") or "").strip(), note=body.get("note"))
    if not r.get("ok"):
        raise HTTPException(400, r.get("error") or "decide failed")
    return pl.envelope_success(**r["draft"])


# ---- Targets (U3 front-door, plan §3.7) ----
@app.get("/api/targets")
def list_targets_ep() -> dict:
    return pl.envelope_success(targets=tgt.list_targets())


@app.get("/api/targets/{name}")
def get_target_ep(name: str) -> dict:
    t = tgt.get_target(name)
    if t is None:
        raise HTTPException(404, f"unknown target: {name}")
    return pl.envelope_success(**t)


@app.post("/api/targets")
async def add_target_ep(request: Request) -> dict:
    body = await _json_body(request)
    url = (body.get("url") or "").strip()
    if not url or url.startswith("-"):
        raise HTTPException(400, "a valid repo URL is required")
    # Cloning is slow + streamed, so run it as a job; the UI watches its live log.
    run_id = run_store.submit_job("add-target", {
        "url": url, "name": (body.get("name") or "").strip(),
        "sha": (body.get("sha") or "").strip(), "trusted": bool(body.get("trusted", False)),
    }, _job_add_target)
    return pl.envelope_success(run_id=run_id)


@app.get("/api/status")
def status() -> dict:
    return pl.envelope_success(**pl.get_state())


@app.get("/api/artifacts")
def artifacts() -> dict:
    return pl.envelope_success(artifacts=pl.list_artifacts())


@app.get("/api/artifact/{name}")
def artifact(name: str) -> dict:
    a = pl.get_artifact(name)
    if a is None:
        raise HTTPException(404, f"unknown artifact: {name}")
    return pl.envelope_success(**a)


@app.post("/api/run/{step_id}")
async def run(step_id: str) -> dict:
    # P1-11: offload the (potentially multi-minute) subprocess to a worker
    # thread so /api/status polls don't block.
    result = await asyncio.to_thread(pl.run_step, step_id)
    if "error" in result:
        raise HTTPException(400, result["error"])
    return pl.envelope_success(**result)


@app.post("/api/write/{name}")
async def write(name: str, request: Request) -> dict:
    body = await request.body()
    if len(body) > _MAX_WRITE_BYTES:
        raise HTTPException(413, f"body too large ({len(body)} bytes; max {_MAX_WRITE_BYTES})")
    content = body.decode("utf-8")
    result = pl.write_file(name, content)
    if "error" in result:
        raise HTTPException(400, result["error"])
    return pl.envelope_success(**result)


# ============================================================================
# Stage 2 — claude-driver subagent endpoints
# ============================================================================

@app.get("/api/backtest/entries")
def backtest_entries() -> dict:
    return pl.envelope_success(entries=pl.list_backtest_entries())


@app.post("/api/subagent/explore")
async def subagent_explore() -> dict:
    result = await asyncio.to_thread(pl.run_explore_subagent)
    if not result.get("ok"):
        raise HTTPException(500, result.get("error", "unknown error"))
    return pl.envelope_success(**result)


# ---- batch helpers (shared by the backtest/hunt/repro /batch endpoints) ----
async def _json_body(request: Request) -> dict:
    if not await request.body():
        return {}
    try:
        return await request.json()
    except json.JSONDecodeError:
        raise HTTPException(400, "request body must be JSON")


def _bounded_parallel(value) -> int:
    try:
        return max(1, min(int(value), 10))
    except (TypeError, ValueError):
        raise HTTPException(400, "max_parallel must be an integer")


async def _run_batch(fn, *args) -> dict:
    result = await asyncio.to_thread(fn, *args)
    if not result.get("ok"):  # batch couldn't start at all (e.g. nothing to run)
        raise HTTPException(400, result.get("error", "batch could not run"))
    return pl.envelope_success(**result)  # per-item failures ride inside .results


# NOTE: each /batch route MUST be declared before its sibling /{param} route,
# else FastAPI matches "batch" as the param. Fans out subagents in parallel via
# pipeline.run_*_batch -> claude_driver.run_claude_batch.
@app.post("/api/subagent/backtest/batch")
async def subagent_backtest_batch(request: Request) -> dict:
    body = await _json_body(request)
    issue_nums = body.get("issue_nums")  # None => all prepared entries
    if issue_nums is not None and not isinstance(issue_nums, list):
        raise HTTPException(400, "issue_nums must be a list or omitted")
    return await _run_batch(
        pl.run_backtest_batch,
        [str(n) for n in issue_nums] if issue_nums is not None else None,
        _bounded_parallel(body.get("max_parallel", 4)),
    )


@app.post("/api/subagent/backtest/{issue_num}")
async def subagent_backtest(issue_num: str) -> dict:
    result = await asyncio.to_thread(pl.run_backtest_subagent, issue_num)
    if not result.get("ok"):
        raise HTTPException(500, result.get("error", "unknown error"))
    return pl.envelope_success(**result)


@app.post("/api/subagent/backtest/{issue_num}/label")
async def subagent_backtest_label(issue_num: str) -> dict:
    result = await asyncio.to_thread(pl.label_backtest_subagent, issue_num)
    if not result.get("ok"):
        raise HTTPException(500, result.get("error", "unknown error"))
    return pl.envelope_success(**result)


@app.post("/api/subagent/hunt/batch")
async def subagent_hunt_batch(request: Request) -> dict:
    body = await _json_body(request)
    passes = body.get("passes")  # list of [angle, pass_num]; None => 4 Day-4 passes
    if passes is not None and not isinstance(passes, list):
        raise HTTPException(400, "passes must be a list or omitted")
    try:
        parsed = [(p[0], int(p[1])) for p in passes] if passes else None
    except (TypeError, ValueError, IndexError, KeyError):
        raise HTTPException(400, "each passes item must be [angle, pass_num]")
    return await _run_batch(pl.run_hunt_batch, parsed,
                            _bounded_parallel(body.get("max_parallel", 4)))


@app.post("/api/subagent/hunt/{angle}/pass{pass_num}")
async def subagent_hunt(angle: str, pass_num: int) -> dict:
    result = await asyncio.to_thread(pl.run_hunt_subagent, angle, pass_num)
    if not result.get("ok"):
        raise HTTPException(500, result.get("error", "unknown error"))
    return pl.envelope_success(**result)


@app.post("/api/subagent/repro/batch")
async def subagent_repro_batch(request: Request) -> dict:
    body = await _json_body(request)
    ids = body.get("finding_ids")  # None => all scaffolds missing a .java
    if ids is not None and not isinstance(ids, list):
        raise HTTPException(400, "finding_ids must be a list or omitted")
    return await _run_batch(
        pl.run_repro_batch,
        [str(i) for i in ids] if ids is not None else None,
        _bounded_parallel(body.get("max_parallel", 4)),
    )


@app.post("/api/subagent/repro/{finding_id}")
async def subagent_repro(finding_id: str) -> dict:
    result = await asyncio.to_thread(pl.run_repro_subagent, finding_id)
    if not result.get("ok"):
        raise HTTPException(500, result.get("error", "unknown error"))
    return pl.envelope_success(**result)


@app.post("/api/subagent/fix/batch")
async def subagent_fix_batch(request: Request) -> dict:
    body = await _json_body(request)
    ids = body.get("finding_ids")  # None => all findings with a reproducer but no patch
    if ids is not None and not isinstance(ids, list):
        raise HTTPException(400, "finding_ids must be a list or omitted")
    return await _run_batch(
        pl.run_fix_batch,
        [str(i) for i in ids] if ids is not None else None,
        _bounded_parallel(body.get("max_parallel", 4)),
    )


@app.post("/api/subagent/fix/{finding_id}")
async def subagent_fix(finding_id: str) -> dict:
    result = await asyncio.to_thread(pl.run_fix_subagent, finding_id)
    if not result.get("ok"):
        raise HTTPException(500, result.get("error", "unknown error"))
    return pl.envelope_success(**result)


@app.post("/api/suggest-gates")
async def suggest_gates_ep() -> dict:
    """Deterministic advisory auto-fill of blank dedup/cwe gates (non-AI)."""
    result = await asyncio.to_thread(pl.suggest_gates)
    if not result.get("ok"):
        raise HTTPException(400, result.get("error", "suggest-gates could not run"))
    return pl.envelope_success(**result)


@app.post("/api/orchestrate")
async def orchestrate_ep(request: Request) -> dict:
    """Self-correcting reproduce→fix→validate→retry loop over findings."""
    body = await _json_body(request)
    ids = body.get("finding_ids")
    if ids is not None and not isinstance(ids, list):
        raise HTTPException(400, "finding_ids must be a list or omitted")
    # body may also carry model/effort to override the opus/high builder defaults.
    try:
        max_fix = int(body.get("max_fix_attempts", 2))
    except (TypeError, ValueError):
        raise HTTPException(400, "max_fix_attempts must be an integer")
    kw = {}
    if body.get("model"):
        kw["model"] = str(body["model"])
    if body.get("effort"):
        kw["effort"] = str(body["effort"])
    result = await asyncio.to_thread(
        pl.orchestrate,
        [str(i) for i in ids] if ids is not None else None,
        max(0, min(max_fix, 5)),
        body.get("worktree"), body.get("network"),
        **kw,
    )
    if not result.get("ok"):
        raise HTTPException(400, result.get("error", "orchestrate could not run"))
    return pl.envelope_success(**result)


# Static assets
app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")


def main() -> None:
    # P1-8: fail fast if claude CLI is missing/incompatible.
    try:
        import claude_driver
        info = claude_driver.check_cli()
        print(f"[server] claude CLI: {info['version']}", file=sys.stderr)
    except RuntimeError as e:
        print(f"[server] WARN: claude CLI check failed: {e}", file=sys.stderr)
        print(f"[server]   subagent endpoints will fail until resolved", file=sys.stderr)

    # P0-7: print the auth token loudly so curl users + the dashboard tab can
    # both reach the API. The token is regenerated each startup.
    print("", file=sys.stderr)
    print("=" * 60, file=sys.stderr)
    print(f"[server] AUTH_TOKEN = {AUTH_TOKEN}", file=sys.stderr)
    print(f"[server] use:  curl -H 'Authorization: Bearer {AUTH_TOKEN}' \\", file=sys.stderr)
    print(f"[server]            http://127.0.0.1:8765/api/status", file=sys.stderr)
    print(f"[server] (the dashboard HTML auto-injects this token into window.AUTH_TOKEN)", file=sys.stderr)
    print("=" * 60, file=sys.stderr)
    print("", file=sys.stderr)

    # access_log=False: the SSE stream carries the auth token as ?token= (the
    # EventSource API can't send headers), so the default access log would write
    # the live token to stdout on every connect. Disable it.
    uvicorn.run(app, host="127.0.0.1", port=8765, log_level="info", access_log=False)


if __name__ == "__main__":
    main()
