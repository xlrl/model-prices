#!/usr/bin/env bash
set -euo pipefail
PORT=${1:-8000}
cd "$(dirname "$0")"
echo "Serving at http://localhost:${PORT}"
python3 -m http.server "$PORT"