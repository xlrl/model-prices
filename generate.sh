#!/bin/bash
set -euo pipefail

cd "$(dirname "$0")"

uv run generate_price_chart.py --refresh "$@"
