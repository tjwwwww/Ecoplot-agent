#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
source .venv/bin/activate
export PYTHONUNBUFFERED=1
uvicorn api:app --host 0.0.0.0 --port "${API_PORT:-8000}"
