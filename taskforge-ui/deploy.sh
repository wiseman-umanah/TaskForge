#!/usr/bin/env bash
# deploy.sh — build the frontend and push to the gh-pages branch
# Usage: ./deploy.sh
# Requires: git, pnpm, and a GitHub remote named "origin"

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
UI_DIR="$REPO_ROOT/taskforge-ui"
DIST_DIR="$UI_DIR/dist"

echo "▶ Building frontend…"
cd "$UI_DIR"
pnpm build

echo "▶ Deploying to gh-pages branch…"
cd "$REPO_ROOT"

# Create a temporary git worktree on the gh-pages branch
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

# Ensure gh-pages branch exists
if ! git ls-remote --exit-code --heads origin gh-pages &>/dev/null; then
  echo "  Creating gh-pages branch…"
  git switch --orphan gh-pages
  git commit --allow-empty -m "init gh-pages"
  git push -u origin gh-pages
  git switch -
fi

git worktree add "$TMP_DIR" gh-pages
cp -r "$DIST_DIR"/. "$TMP_DIR/"

cd "$TMP_DIR"
git add -A
git commit -m "deploy: $(date -u '+%Y-%m-%d %H:%M') UTC" --allow-empty
git push origin gh-pages

echo ""
echo "✓ Deployed! Your site will be live at:"
echo "  https://$(git remote get-url origin | sed 's/.*github.com[:/]//' | sed 's/\.git$//' | sed 's|/|.github.io/|')"
