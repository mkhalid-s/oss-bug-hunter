# Reproducer sandbox for Phase-0 Cell #1 (P1-17 fix, 2026-05-19).
#
# Goal: run a single JUnit test in isolation against a pinned worktree of
# jackson-databind, with no host filesystem write access and a private maven
# repo cache. Exit code surfaces directly; stdout/stderr is the test log.
#
# Why a separate sandbox at all:
#   - phase-0-scope.md §c and oss-bug-hunter-research.md §5.4 require
#     reproducers to run "in a clean Docker container". This is the cornerstone
#     of XBOW's "non-AI validators sit outside the AI" pattern — if reproducer
#     execution mixes with the dev host (or worse, with the hunting agent's
#     subprocess space), a poisoned test could touch host filesystem / creds.
#   - `mvn test` will resolve plugins and dependencies from upstream — same
#     supply-chain concern as `mvn package` (see P0-8). Sandboxing isolates it.
#
# Usage: see scripts/run-repro.sh

FROM maven:3.9-eclipse-temurin-17

# Run as a non-root user to limit the blast radius of any escape. The base image
# may already occupy the requested UID/GID (e.g. Ubuntu 24.04's default `ubuntu`
# user/group at 1000) — drop any pre-existing owner of UID/GID before creating
# `repro`, so the build is robust to whatever the base ships.
ARG UID=1000
ARG GID=1000
RUN set -eux; \
    if getent passwd "${UID}" >/dev/null; then \
      userdel -r "$(getent passwd "${UID}" | cut -d: -f1)" 2>/dev/null || true; \
    fi; \
    if getent group "${GID}" >/dev/null; then \
      groupdel "$(getent group "${GID}" | cut -d: -f1)" 2>/dev/null || true; \
    fi; \
    groupadd -g "${GID}" repro; \
    useradd -m -u "${UID}" -g "${GID}" -s /bin/bash repro

# Working dir lives under /work — bind-mounted by the caller (read-write so mvn
# can compile, but no host paths above /work are visible).
WORKDIR /work
RUN chown -R repro:repro /work

# Use a private maven repo INSIDE the image / volume rather than the host's
# ~/.m2 cache (avoids cross-contaminating the dev's main maven cache).
ENV MAVEN_OPTS="-Dmaven.repo.local=/work/.m2 -Xmx2g"

USER repro

# No CMD — the caller's `docker run` provides the test execution command.
