#!/usr/bin/env bash
# Take today's snapshot and publish it.
#
# Deliberately dumb: no rebase, no force, no stash. GitHub's traffic history is a
# 14-day rolling window, so a snapshot that fails to land is data lost forever -
# which makes "fail loudly and leave the tree alone" the only safe behaviour.
# If the remote moved ahead, this exits non-zero and the cron wrapper reports it;
# a human resolves it. See amh-lessons §2.1 (a rebase once ate 16 local commits).
set -uo pipefail

cd "$(dirname "$0")/.." || exit 1

GIT=/usr/bin/git
[ -x "$GIT" ] || GIT=git

fail() { echo "FAIL: $*" >&2; exit 1; }

command -v python3 >/dev/null || fail "python3 not on PATH"
command -v gh >/dev/null || fail "gh not on PATH"

# 1. Snapshot. collect.py writes docs/data/daily/<today>.json and index.json.
out=$(python3 scripts/collect.py 2>&1) || fail "collect.py failed: ${out##*$'\n'}"
echo "$out"

# 2. Nothing new (a re-run on the same day produces identical bytes).
"$GIT" add docs/data || fail "git add failed"
if "$GIT" diff --cached --quiet; then
  echo "无变化：今天的快照与已发布内容一致"
  exit 0
fi

# 3. Commit + push.
day=$(date +%F)
"$GIT" -c user.name="monkey-sking" \
       -c user.email="monkey-sking@users.noreply.github.com" \
       commit -q -m "data: ${day} snapshot" || fail "commit failed"

if ! push_out=$("$GIT" push -q origin HEAD:main 2>&1); then
  # Do not rebase real work. Un-stage nothing either: the commit is local and
  # recoverable. One exception, added 2026-11-01 after this failed because a
  # different session had pushed dev commits the night before: when every
  # unpublished local commit only touches docs/data (auto-generated snapshots),
  # replaying them is safe. Anything else still fails loudly for a human.
  "$GIT" fetch -q origin 2>/dev/null
  if [ -n "$("$GIT" diff --name-only origin/main...HEAD 2>/dev/null | grep -v '^docs/data/' || true)" ]; then
    fail "push rejected, 本地已提交但未发布（本地提交含非 docs/data 改动，不自动 rebase）：${push_out##*$'\n'}"
  fi
  if ! "$GIT" rebase origin/main >/dev/null 2>&1; then
    "$GIT" rebase --abort >/dev/null 2>&1
    fail "push rejected 且 rebase 冲突（已回滚，未发布）：${push_out##*$'\n'}"
  fi
  if ! push_out=$("$GIT" push -q origin HEAD:main 2>&1); then
    fail "rebase 后 push 仍失败，本地已提交但未发布：${push_out##*$'\n'}"
  fi
fi

echo "已发布 ${day} 快照：$(git show --stat --oneline HEAD | tail -1)"
