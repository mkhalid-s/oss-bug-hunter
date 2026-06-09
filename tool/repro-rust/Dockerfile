# Rust reproducer sandbox (container-execution path for untrusted targets).
# `cargo` is on PATH, so the in-container argv equals the local one.
FROM rust:1
ARG UID=1000
ARG GID=1000
RUN set -eux; \
    if getent passwd "${UID}" >/dev/null; then userdel -r "$(getent passwd "${UID}" | cut -d: -f1)" 2>/dev/null || true; fi; \
    if getent group "${GID}" >/dev/null; then groupdel "$(getent group "${GID}" | cut -d: -f1)" 2>/dev/null || true; fi; \
    groupadd -g "${GID}" repro; \
    useradd -m -u "${UID}" -g "${GID}" -s /bin/bash repro
WORKDIR /work
ENV CARGO_HOME=/tmp/cargo CARGO_TARGET_DIR=/tmp/target
USER repro
