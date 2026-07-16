#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/h5"
export PORT="${H5_PORT:-5173}"
export API_BASE="${API_BASE:-http://127.0.0.1:8000}"
node server.js
