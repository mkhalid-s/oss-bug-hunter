# JS reproducer sandbox (container path for untrusted targets). node on PATH.
FROM node:22-slim
ARG UID=1000
ARG GID=1000
RUN set -eux; \
    if getent passwd "${UID}" >/dev/null; then userdel -r "$(getent passwd "${UID}" | cut -d: -f1)" 2>/dev/null || true; fi; \
    if getent group "${GID}" >/dev/null; then groupdel "$(getent group "${GID}" | cut -d: -f1)" 2>/dev/null || true; fi; \
    groupadd -g "${GID}" repro; \
    useradd -m -u "${UID}" -g "${GID}" -s /bin/bash repro
WORKDIR /work
USER repro
