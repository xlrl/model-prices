#!/bin/bash
set -euo pipefail

cd "$(dirname "$0")"

commit_and_push=0
args=()
for arg in "$@"; do
  if [ "$arg" = "--commit-and-push" ]; then
    commit_and_push=1
  else
    args+=("$arg")
  fi
done

uv run generate_price_chart.py --refresh "${args[@]}"

if [ "$commit_and_push" = 1 ]; then
  git add index.html feed.xml model_prices.csv data/
  if git diff --cached --quiet; then
    echo "No changes to commit."
  else
    git commit -m "Update prices"
    git push
  fi
fi
