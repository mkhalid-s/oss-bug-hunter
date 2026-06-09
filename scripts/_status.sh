#!/usr/bin/env bash
# Status display for the Cell #1 pipeline.
# P0-5 fix (2026-05-19): this is now a thin shim — the canonical step DAG
# lives in tool/pipeline.py::PIPELINE. Delegating here eliminates the prior
# triplication between Makefile, _status.sh, and pipeline.py.
#
# Pass --no-color to suppress ANSI codes (e.g. for non-TTY consumers).

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"  # P0-2: relocatable
VENV_PY="${PROJECT_ROOT}/.venv/bin/python"
[[ -x "${VENV_PY}" ]] || VENV_PY="python3"

use_ansi="True"
if [[ "${1:-}" == "--no-color" ]] || [[ ! -t 1 ]]; then
  use_ansi="False"
fi

"${VENV_PY}" -c "
import sys
sys.path.insert(0, '${PROJECT_ROOT}/tool')
sys.path.insert(0, '${PROJECT_ROOT}/scripts')
import pipeline
for line in pipeline.status_lines(use_ansi=${use_ansi}):
    print(line)
"
