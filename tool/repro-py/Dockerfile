# Python reproducer sandbox (container-execution path for untrusted targets).
# pytest preinstalled so the in-container `python -m pytest` works offline.
FROM python:3.12-slim
RUN pip install --no-cache-dir pytest

# Non-root user matching the host UID/GID so the bind-mounted /work is writable.
# (Drop any pre-existing owner of the UID/GID first — robust like tool/repro.)
ARG UID=1000
ARG GID=1000
RUN set -eux; \
    if getent passwd "${UID}" >/dev/null; then userdel -r "$(getent passwd "${UID}" | cut -d: -f1)" 2>/dev/null || true; fi; \
    if getent group "${GID}" >/dev/null; then groupdel "$(getent group "${GID}" | cut -d: -f1)" 2>/dev/null || true; fi; \
    groupadd -g "${GID}" repro; \
    useradd -m -u "${UID}" -g "${GID}" -s /bin/bash repro
WORKDIR /work
USER repro
# Caller provides the command (python -m pytest <selector> …).
