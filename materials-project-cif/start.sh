#!/usr/bin/env bash
set -eo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
if [[ -n "${MP_CIF_PYTHON:-}" ]]; then
    exec "$MP_CIF_PYTHON" download_cif.py "$@"
elif [[ -x .venv/bin/python ]]; then
    exec .venv/bin/python download_cif.py "$@"
elif command -v python3 >/dev/null 2>&1; then
    exec python3 download_cif.py "$@"
else
    exec python download_cif.py "$@"
fi
