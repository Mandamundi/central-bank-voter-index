#!/usr/bin/env bash
# Commit one project's data/ and output/ and push. Usage: commit.sh <project folder> <message>
# The jobs run one after another and touch different folders, so rebasing onto the previous job's push never conflicts.
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
git config user.name "github-actions[bot]"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
git add "$1/data" "$1/output"
if git diff --cached --quiet; then
  echo "No changes in $1"
  exit 0
fi
git commit -m "$2 $(date -u +%Y-%m-%d)"
for attempt in 1 2 3; do
  git pull --rebase origin "${GITHUB_REF_NAME:-main}" && git push && exit 0
  sleep 5
done
exit 1
