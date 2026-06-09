"""Execution-backend abstraction for the sandbox validators.

Plan §3.1/§10: validators must run *everywhere* — a macOS laptop (Docker
Desktop), inside a daemonless Linux devcontainer (rootless podman, or a
trust-gated local fallback), and on plain Linux/CI. This module hides the
substrate behind one interface so `run_harness.py` and the adapters don't care
which engine actually executed the test.

Auto-select order: docker -> podman(rootless) -> local.

`local` is TRUST-GATED: it never runs untrusted target code (supports_untrusted()
is False). On Linux it *tries* `unshare --net` for network isolation; where the
kernel forbids that (hardened/unprivileged containers — empirically the case in
this devcontainer), it degrades to "offline-runner mode": the caller's argv is
already offline (e.g. `mvn -o`), so no network is touched, plus a `prlimit`
memory cap and a loud reduced-isolation warning. macOS has no namespaces/cgroups,
so local there is always offline+ulimit, trusted-only.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from typing import Callable, Optional, Sequence

LogSink = Callable[[str], None]


class BackendError(RuntimeError):
    pass


@dataclass
class RunSpec:
    argv: Sequence[str]
    cwd: str                                  # working dir (host path; container backends mount it)
    network: str = "none"                     # none|bridge
    mem_limit_mb: Optional[int] = 2048
    name: Optional[str] = None                # handle for kill()/cancel
    env: Optional[dict] = None
    mounts: list = field(default_factory=list)  # [(host, container)] for container backends
    image: Optional[str] = None               # container backends only
    # local backend: True means argv already avoids network (offline) so network=none
    # is satisfiable without kernel isolation.
    offline_argv: bool = False


def _host_bind(path: str) -> str:
    """Docker-outside-of-docker: when the in-container CLI talks to the HOST
    daemon, `-v` sources must be HOST paths. Rewrite a leading
    REPRO_CONTAINER_PATH_PREFIX (default /workspaces) to REPRO_HOST_PATH_PREFIX.
    No-op when unset (native docker / podman, or host path == container path)."""
    ap = os.path.abspath(path)
    hp = os.environ.get("REPRO_HOST_PATH_PREFIX", "")
    cp = os.environ.get("REPRO_CONTAINER_PATH_PREFIX", "/workspaces")
    return hp + ap[len(cp):] if hp and ap.startswith(cp) else ap


def _pump(proc: "subprocess.Popen", log: Optional[LogSink]) -> None:
    assert proc.stdout is not None
    for line in proc.stdout:
        if log:
            log(line.rstrip("\n"))


class ExecBackend:
    name = "base"

    def detect(self) -> bool:
        raise NotImplementedError

    def supports_untrusted(self) -> bool:
        raise NotImplementedError

    def build_image(self, dockerfile_dir: str, tag: str,
                    build_args: Optional[dict] = None, log: Optional[LogSink] = None) -> None:
        pass

    def run(self, spec: RunSpec, log: Optional[LogSink] = None,
            on_start: Optional[Callable[["subprocess.Popen"], None]] = None) -> int:
        raise NotImplementedError

    def kill(self, name: str) -> None:
        pass

    # shared: spawn, stream output line-by-line, return rc. start_new_session so the
    # whole process group is killable (P0-11 parity).
    def _spawn(self, argv: Sequence[str], cwd: Optional[str], env: Optional[dict],
               log: Optional[LogSink], on_start) -> int:
        proc = subprocess.Popen(
            list(argv), cwd=cwd, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1, start_new_session=True,
        )
        if on_start:
            on_start(proc)
        _pump(proc, log)
        return proc.wait()


class _ContainerBackend(ExecBackend):
    cli = ""
    extra_run: list = []

    def detect(self) -> bool:
        if not shutil.which(self.cli):
            return False
        try:
            return subprocess.run([self.cli, "info"],
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                  timeout=12).returncode == 0
        except Exception:
            return False

    def supports_untrusted(self) -> bool:
        return True

    def build_image(self, dockerfile_dir, tag, build_args=None, log=None):
        argv = [self.cli, "build", "-t", tag]
        for k, v in (build_args or {}).items():
            argv += ["--build-arg", f"{k}={v}"]
        argv.append(dockerfile_dir)
        rc = self._spawn(argv, None, None, log, None)
        if rc != 0:
            raise BackendError(f"{self.cli} build failed (rc={rc})")

    def run(self, spec: RunSpec, log=None, on_start=None) -> int:
        if not spec.image:
            raise BackendError("container backend requires spec.image")
        argv = [self.cli, "run", "--rm"]
        if spec.name:
            argv += ["--name", spec.name]                 # B4: cancellable
        argv += [f"--network={spec.network}"]
        if spec.mem_limit_mb:
            argv += [f"--memory={spec.mem_limit_mb}m"]     # §3.5: bounded OOM
        argv += self.extra_run
        for host, cont in spec.mounts:
            argv += ["-v", f"{_host_bind(host)}:{cont}:rw"]
        argv += ["-w", spec.cwd, spec.image]
        argv += list(spec.argv)
        return self._spawn(argv, None, None, log, on_start)

    def kill(self, name: str) -> None:
        if name:
            subprocess.run([self.cli, "kill", name],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class DockerBackend(_ContainerBackend):
    name = "docker"
    cli = "docker"


class PodmanBackend(_ContainerBackend):
    name = "podman"
    cli = "podman"
    # G2: map the in-container user to the invoking host user so a rootless
    # bind-mount (-v ...:rw) is writable by the build's UID/GID. Without this,
    # rootless podman maps uid 1000 to a high subuid and mvn writes fail.
    extra_run = ["--userns=keep-id"]


class LocalBackend(ExecBackend):
    """No container. TRUSTED targets only. Network isolation via unshare if the
    kernel allows; otherwise offline-runner mode (argv must be offline)."""
    name = "local"
    _net_capable: Optional[bool] = None
    _reg_lock = threading.Lock()
    _registry: dict = {}   # name -> pgid (for kill)

    def detect(self) -> bool:
        return os.name == "posix"   # always available as a last resort

    def supports_untrusted(self) -> bool:
        return False                # B3/G3: never runs untrusted code

    @classmethod
    def _can_unshare_net(cls) -> bool:
        if cls._net_capable is None:
            try:
                cls._net_capable = subprocess.run(
                    ["unshare", "-rn", "true"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    timeout=5).returncode == 0
            except Exception:
                cls._net_capable = False
        return cls._net_capable

    def run(self, spec: RunSpec, log=None, on_start=None) -> int:
        argv = list(spec.argv)
        if spec.network == "none":
            if self._can_unshare_net():
                argv = ["unshare", "-rn"] + argv
            elif not spec.offline_argv and log:
                log("[local] WARNING: kernel forbids unshare --net and argv is not "
                    "marked offline — REDUCED ISOLATION (trusted target only).")
        if spec.mem_limit_mb:
            argv = ["prlimit", f"--as={spec.mem_limit_mb * 1024 * 1024}"] + argv

        env = dict(spec.env or os.environ)

        def _record(proc):
            if spec.name:
                with self._reg_lock:
                    self._registry[spec.name] = os.getpgid(proc.pid)
            if on_start:
                on_start(proc)

        try:
            return self._spawn(argv, spec.cwd, env, log, _record)
        finally:
            if spec.name:
                with self._reg_lock:
                    self._registry.pop(spec.name, None)

    def kill(self, name: str) -> None:
        import signal
        with self._reg_lock:
            pgid = self._registry.get(name)
        if pgid:
            try:
                os.killpg(pgid, signal.SIGTERM)
            except ProcessLookupError:
                pass


_ALL = [DockerBackend(), PodmanBackend(), LocalBackend()]


def select_backend(trusted: bool, prefer: Optional[str] = None) -> ExecBackend:
    """Pick the best usable backend. `local` only for trusted targets."""
    order = _ALL
    if prefer:
        order = [b for b in _ALL if b.name == prefer] + [b for b in _ALL if b.name != prefer]
    for b in order:
        if not b.detect():
            continue
        if not b.supports_untrusted() and not trusted:
            continue
        return b
    raise BackendError(
        "no usable execution backend (docker/podman unavailable; local refused "
        "because target is untrusted)")


if __name__ == "__main__":
    trusted = "--trusted" in sys.argv
    for b in _ALL:
        ok = b.detect()
        print(f"{b.name:8} detect={ok!s:5} untrusted={b.supports_untrusted()}")
    try:
        chosen = select_backend(trusted=trusted)
        print(f"-> selected (trusted={trusted}): {chosen.name}")
    except BackendError as e:
        print(f"-> {e}")
