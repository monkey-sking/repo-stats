# repo-stats

<!-- badges:start -->
[![License](https://img.shields.io/github/license/monkey-sking/repo-stats?style=flat-square)](https://github.com/monkey-sking/repo-stats/blob/main/LICENSE) [![Last commit](https://img.shields.io/github/last-commit/monkey-sking/repo-stats?style=flat-square)](https://github.com/monkey-sking/repo-stats/commits/main) [![Python](https://img.shields.io/github/languages/top/monkey-sking/repo-stats?style=flat-square)](https://github.com/monkey-sking/repo-stats/search?l=Python) [![Stars](https://img.shields.io/github/stars/monkey-sking/repo-stats?style=flat-square)](https://github.com/monkey-sking/repo-stats/stargazers) [![Forks](https://img.shields.io/github/forks/monkey-sking/repo-stats?style=flat-square)](https://github.com/monkey-sking/repo-stats/network/members)
<!-- badges:end -->

Daily statistics for every public repository under [`monkey-sking`](https://github.com/monkey-sking) —
stars, forks, traffic (views / unique visitors / clones), referrers, popular paths
and commit activity — rendered as one static page.

**Live page:** https://monkey-sking.github.io/repo-stats/

## Why this exists

GitHub's traffic endpoints (`/traffic/views`, `/traffic/clones`, `/traffic/popular/*`)
have two hard limits that make them useless for long-term tracking:

1. They only return a **rolling 14-day window**.
2. They are only readable by someone with **push access** to the repo.

So there is no way to answer "how did this repo trend over three months?" from the
API alone, and no way to show the numbers on a public README. This project fixes
that by taking a snapshot every day and committing it, turning a 14-day window
into permanent history.

```
every day → collect.py → docs/data/daily/YYYY-MM-DD.json → git push → GitHub Pages
```

## Layout

```
scripts/collect.py        fetch one snapshot (repos + traffic + participation)
scripts/badges.py         idempotently manage README identity badges
docs/index.html           self-contained dashboard (no CDN, no build step)
docs/data/daily/*.json    one immutable snapshot per day
docs/data/index.json      which days exist + repo list
.github/workflows/        optional cloud fallback (needs a PAT, see below)
```

## Running it

Requires the [`gh` CLI](https://cli.github.com/) authenticated with `repo` scope.

```bash
python3 scripts/collect.py          # write today's snapshot
python3 scripts/collect.py --dry-run
python3 scripts/collect.py --only ai-memory-hub,MacMessageBackup
```

Then serve locally:

```bash
cd docs && python3 -m http.server 8791
# → http://127.0.0.1:8791/
```

### Daily automation

The collector is designed to run from a plain cron/launchd job on a machine that
already has `gh` logged in — no secrets to provision:

```bash
cd /path/to/repo-stats
python3 scripts/collect.py && \
  git add docs/data && \
  git diff --cached --quiet || git commit -m "data: $(date +%F) snapshot" && \
  git push
```

Because traffic history is only ever accumulated forward, **the run must happen at
least once every 14 days**; miss that and the days in between are lost forever
(they will simply be absent from the charts).

## README badges

`scripts/badges.py` maintains project-aware badges for public, non-fork repositories.
It updates only the managed block:

```markdown
<!-- badges:start -->
...badges...
<!-- badges:end -->
```

### Badge selection rules

| Project evidence | Badge added |
|---|---|
| Every public source repository | Stars, Forks, Last commit |
| GitHub can identify the license, or a checked custom-license mapping exists | License |
| GitHub detects a primary language | Language |
| A real GitHub Release exists | Latest Release; Downloads when it has assets |
| `package.json` is not private and npm registry confirms the package | npm version, monthly downloads |
| A CI / test / build / security workflow has a run on the default branch within 45 days | That workflow's live status |

No guessed coverage, passing tests, supported platforms, or package versions.
Visitor counters are intentionally excluded because GitHub's image proxy cache
makes them an unreliable count of real README readers. Existing project-specific
badges outside the managed block are left alone.

### Run locally

Requires GitHub CLI authenticated with read access to the account's public
repositories and write access to the README files being updated.

```bash
python3 scripts/badges.py --dry-run
python3 scripts/badges.py --apply
python3 scripts/badges.py --apply --only TextGenie,DuoStage
```

### Weekly GitHub Actions sync

`.github/workflows/sync-badges.yml` supports manual runs and a weekly schedule.
To let it update all public repositories, add a repository secret named
`BADGE_SYNC_TOKEN` to **repo-stats**. Use a fine-grained PAT scoped to the
public repositories you want managed, with:

- **Metadata: Read-only**
- **Contents: Read and write**
- **Actions: Read-only** (to detect recent CI runs)

The workflow skips safely when the secret is absent. A manual run can use
`dry_run=true` to preview changes without writing. The sync is idempotent:
unchanged READMEs do not receive new commits.

## Cloud fallback (optional)

`collect.yml` exists for the case where the machine running the local cron is
unavailable. It is `workflow_dispatch`-only because `GITHUB_TOKEN` can only read
traffic for the repository it runs in — tracking *other* repos requires a
fine-grained PAT with **Administration: read** on each repo, stored as the
`STATS_TOKEN` secret. If that secret is absent the workflow exits successfully
without doing anything.

## Note on forks

Only non-fork repositories are collected. A fork's traffic belongs to the
upstream project, and badge-ifying a fork's README mostly adds noise.
