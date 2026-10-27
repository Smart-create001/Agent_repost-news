#!/usr/bin/env bash
set -euo pipefail

# Run from the repository root. The workflow checks out main without shallow history.
state_file=seen_posts.json
base_commit=$(git rev-parse HEAD)
git add -- "$state_file"
if git diff --cached --quiet -- "$state_file"; then
  echo 'State unchanged; no commit or push needed.'
  exit 0
fi

git config user.name 'github-actions[bot]'
git config user.email '41898282+github-actions[bot]@users.noreply.github.com'
git commit -m 'Persist agent delivery state' -- "$state_file"

# Do not merge or reset state when the remote moved. The workflow uploads the
# local state file as an artifact on failure for manual reconciliation.
git fetch --no-tags origin main
remote_commit=$(git rev-parse refs/remotes/origin/main)
if [[ "$remote_commit" != "$base_commit" ]]; then
  echo 'Remote main changed during the run; refusing to overwrite newer state.' >&2
  exit 3
fi
if ! git push origin HEAD:main; then
  echo 'State push failed; local state requires manual reconciliation.' >&2
  exit 4
fi
echo 'State commit pushed successfully.'
