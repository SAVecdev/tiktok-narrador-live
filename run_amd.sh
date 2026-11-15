#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"
if [[ ! -x .venv-amd/bin/python ]]; then
  echo "Falta el entorno ROCm. Consulta la sección 'GPU AMD en Linux' del README.md." >&2
  exit 1
fi
exec .venv-amd/bin/python run.py "$@"
