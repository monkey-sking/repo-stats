# repo-stats

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

`scripts/badges.py` inserts a managed block into every public, non-fork README:

```markdown
<!-- badges:start -->
...badges...
<!-- badges:end -->
```

Re-running rewrites only that block, so it is safe to run repeatedly.

```bash
python3 scripts/badges.py --dry-run     # preview
python3 scripts/badges.py --apply
python3 scripts/badges.py --apply --only ai-memory-hub
```

### What gets added, and why only that

A survey of 40 mainstream repositories (got, axios, express, bun, deno, vscode,
react, uptime-kuma, immich, n8n, dify, ollama, fastapi, ripgrep, lazygit, …)
showed what people actually put in a README header:

| Badge | Repos out of 40 | Used here |
|---|---|---|
| package version (npm/pypi/crates) | 15 | no — most repos here are not published |
| community chat (Discord/Twitter) | 11 | no — nothing to link to |
| download counts | 10 | no — same reason |
| CI status | 7 | no — only a couple of repos have workflows |
| **stars** | **6** | only when `stars >= 3` |
| **license** | 5 | yes |
| issues | 5 | no — `0 open` reads worse than nothing |
| release | 4 | only where a release exists |
| coverage | 4 | no — no coverage service configured |
| contributors | 3 | no — `1` reads worse than nothing |
| **commit activity** | 3 | yes (weekly chart lives on the dashboard) |
| **last commit** | 2 | yes |
| **language** | — | yes |
| visitor counter | **0** | yes, with a caveat |

The rule behind the omissions: **never render a number that flatters nobody.**
Thirty-plus repos with `0` stars in a row look worse than a clean header.

### The visitor counter caveat

The `views` badge comes from [hits.sh](https://hits.sh). GitHub proxies README
images through its `camo` CDN and caches them, so the badge increments only when
camo re-fetches — it **under-counts real readers** and is not comparable to the
`traffic/views` numbers on the dashboard. Treat it as a rough activity signal.

It replaced [komarev.com/ghpvc](https://github.com/antonkomarev/github-profile-views-counter),
which was verified dead in October 2026: three consecutive requests for a fresh
username all returned `0`.

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
