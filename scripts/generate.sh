#!/usr/bin/env bash
set -euo pipefail
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
project_dir="$(cd -- "$script_dir/.." && pwd)"
python_bin="${PYTHON_EXECUTABLE:-$project_dir/.venv/bin/python}"
if [[ ! -x "$python_bin" ]]; then
    if [[ -n "${PYTHON_EXECUTABLE:-}" ]]; then
        printf '%s\n' 'PYTHON_EXECUTABLE must name an executable file.' >&2
        exit 2
    fi
    python_bin="$(command -v python3.11 || command -v python3.12 || command -v python3)"
fi
exec "$python_bin" "$project_dir/cognito_jmeter.py" generate "$@"
