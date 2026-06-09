# Cell #1 — validation runbook (run once Docker is up)

Pipeline is 17/17; calibration gate = PROCEED. The only thing left is executing
the Day-3 validators / orchestrator, which need the Docker daemon (was down).

## SETUP (host side, one-time) — docker-outside-of-docker
The host already runs Docker; expose it to this dev container via its socket
(NOT docker-in-docker, no --privileged). On the HOST:

1. Edit `/workspaces/GW/.devcontainer/devcontainer.json`, add:
       "features": { "ghcr.io/devcontainers/features/docker-outside-of-docker:1": {} }
   (mounts /var/run/docker.sock + grants the `node` user access; no daemon, no privileged.)

2. Find the HOST path that backs the container's workspace (paths differ → the
   host daemon needs host paths for `-v` and build context). On the HOST:
       docker inspect -f '{{range .Mounts}}{{.Source}} -> {{.Destination}}{{"\n"}}{{end}}' <devcontainer-id>
   Look for the mount whose Destination is `/workspaces/GW` (or `/workspaces`);
   its Source is the host path.

3. If the host path differs from the container path, set these in
   devcontainer.json `containerEnv` (the validators translate the bind paths):
       "REPRO_CONTAINER_PATH_PREFIX": "/workspaces",
       "REPRO_HOST_PATH_PREFIX": "<host path that maps to /workspaces>"
   (e.g. if host `/Users/me/dev/GW` -> container `/workspaces/GW`, then the
   prefix pair is `/workspaces` <-> `/Users/me/dev`. If the host path is literally
   `/workspaces/...`, skip this — translation is a no-op.)

4. Rebuild: VS Code Command Palette -> "Dev Containers: Rebuild Container".

## 0. Confirm Docker is reachable (inside the container, after rebuild)
    docker info        # must succeed

## 1. GOTCHA — first in-container run needs network for Maven deps
The sandbox defaults to `--network none`; the container's private `/work/.m2`
starts empty, so the first `mvn test` inside it can't resolve deps and will fail
("no test ran"). Use `--network bridge` for the FIRST run to warm `/work/.m2`
(it's bind-mounted, so it persists); later runs can drop back to `none`.
`bridge` is on the R6 allowlist; `host` needs `REPRO_ALLOW_HOST_NET=1`.

## 2a. Validate the EXISTING ec-1 artifacts (deterministic, no new AI/token cost)
    .venv/bin/python scripts/day3-hunt.py run-repros --network bridge   # runs ec-1.java -> reproducer gate
    .venv/bin/python scripts/day3-hunt.py run-fixes  --network bridge   # applies ec-1.patch + reruns -> fix gate
Expected: reproducer FAILS on HEAD (gate "pass" = bug reproduces); after the
patch the test PASSES (fix gate "pass" = fix works). Proves the loop end-to-end.

## 2b. OR run the full self-correcting loop (rebuilds reproducer+fix at opus/high)
    .venv/bin/python tool/pipeline.py orchestrate --ids ec-1 --network bridge

## 3. Re-aggregate reports
    .venv/bin/python scripts/day3-hunt.py validate
    # NOTE: day4-finalize.py report REGENERATES cell-1-report.md with BLANK
    # HUMAN sections — it will overwrite the filled Cost/Lessons/Recommendation.
    # Re-fill those after, or skip the report regen if you only need the gates.

## Reality check
ec-1 already failed self-consistency (1/3) → it is NOT a Phase-0 keeper even if it
validates. The value of running this is (a) proving the reproduce→fix loop works
end-to-end, and (b) re-running a fresh hunt now that the gate is PROCEED, to look
for a finding that BOTH reproduces AND survives 2-of-3 — the real Phase-0 goal.
