#!/usr/bin/env python3
"""Collect a daily snapshot of every public, non-fork repository.

Data lands in docs/data so GitHub Pages can serve it from the same root as the
page (Pages source = /docs).

Why this shape:
  * GitHub's traffic API (views / clones / referrers / popular paths) only
    exposes a rolling 14-day window and only to repo admins. Nothing keeps
    history for you. So the collector writes one immutable file per day and the
    page stitches those files into real multi-month curves. Run it daily and the
    history becomes permanent and immune to the 14-day cliff.
  * `stats/participation` returns 52 weeks of weekly commit counts in a single
    call, which is far cheaper than paging through /commits.
  * Every request degrades to null instead of aborting the run, so a single
    restricted repo cannot wipe out the day's snapshot.

Usage:
    python3 collect.py                 # write data/daily/<today>.json + index
    python3 collect.py --out DIR       # write somewhere else (used by tests)
    python3 collect.py --dry-run       # print the summary, write nothing
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import subprocess
import sys
from pathlib import Path

OWNER = "monkey-sking"
ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "docs" / "data"

# Repos whose LICENSE GitHub cannot classify; label them ourselves.
STATIC_LICENSE = {
    "ai-memory-hub": "Apache-2.0",
    "proximity-lock": "Apache-2.0",
    "TextGenie": "Apache-2.0",
    "feishu-doc-outline-enhancer": "Apache-2.0",
    "garmin-sync": "Apache-2.0",
    "browser-profile-launcher": "Apache-2.0",
    "orion-pwa": "Custom",
}


def gh(path: str, *, paginate: bool = False) -> dict | list | None:
    """GET a REST path. Returns None on any failure so one repo cannot kill a run."""
    cmd = ["gh", "api", path]
    if paginate:
        cmd.append("--paginate")
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        return None
    try:
        return json.loads(r.stdout) if r.stdout.strip() else None
    except json.JSONDecodeError:
        return None


def list_own_public_repos() -> list[str]:
    out = subprocess.run(
        ["gh", "repo", "list", OWNER, "--visibility", "public", "--limit", "200",
         "--json", "name,isFork"],
        capture_output=True, text=True, check=True,
    ).stdout
    return [r["name"] for r in json.loads(out) if not r["isFork"]]


def traffic(repo: str, kind: str) -> dict | None:
    """kind is 'views' or 'clones'. Both share the {count, uniques, <kind>[]} shape."""
    d = gh(f"repos/{OWNER}/{repo}/traffic/{kind}")
    if not isinstance(d, dict):
        return None
    daily = {}
    for row in d.get(kind, []):
        # timestamp is midnight UTC of the day the row covers
        day = row["timestamp"][:10]
        daily[day] = {"count": row["count"], "uniques": row["uniques"]}
    return {"total": d.get("count", 0), "uniques": d.get("uniques", 0), "daily": daily}


def collect_repo(name: str) -> dict:
    meta = gh(f"repos/{OWNER}/{name}") or {}
    lic = meta.get("license") or {}
    spdx = lic.get("spdx_id") or "none"
    if spdx == "NOASSERTION":
        spdx = STATIC_LICENSE.get(name, "Other")
    elif spdx == "none" and lic.get("name"):
        spdx = lic["name"]

    part = gh(f"repos/{OWNER}/{name}/stats/participation") or {}
    weekly = part.get("all") or []

    # Contributor count: one-by-one page plus the Link header's total.
    contrib = None
    r = subprocess.run(
        ["gh", "api", f"repos/{OWNER}/{name}/contributors?per_page=1&anon=1", "-i"],
        capture_output=True, text=True,
    )
    if r.returncode == 0:
        for line in r.stdout.splitlines():
            if line.lower().startswith("link:") and "last" in line:
                seg = line.split("&page=")[-1].split(">")[0]
                if seg.isdigit():
                    contrib = int(seg)
                break
            if line.lower().startswith("link:") and "last" not in line:
                pass
        if contrib is None:
            # No Link header means a single page -> count the array we got.
            body = r.stdout.split("\r\n\r\n", 1)[-1].split("\n\n", 1)[-1]
            try:
                arr = json.loads(body)
                contrib = len(arr) if isinstance(arr, list) else None
            except json.JSONDecodeError:
                contrib = None

    return {
        "name": name,
        "description": meta.get("description"),
        "language": meta.get("language"),
        "license": spdx,
        "stars": meta.get("stargazers_count", 0),
        "forks": meta.get("forks_count", 0),
        "watchers": meta.get("subscribers_count", 0),
        "open_issues": meta.get("open_issues_count", 0),
        "contributors": contrib,
        "size_kb": meta.get("size", 0),
        "topics": meta.get("topics", []),
        "homepage": meta.get("homepage"),
        "created_at": meta.get("created_at"),
        "pushed_at": meta.get("pushed_at"),
        "archived": bool(meta.get("archived")),
        "weekly_commits": weekly,
        "views": traffic(name, "views"),
        "clones": traffic(name, "clones"),
        "referrers": [
            {"ref": x.get("referrer"), "count": x.get("count"), "uniques": x.get("uniques")}
            for x in (gh(f"repos/{OWNER}/{name}/traffic/popular/referrers") or [])
        ][:8],
        "paths": [
            {"path": x.get("path"), "count": x.get("count"), "uniques": x.get("uniques")}
            for x in (gh(f"repos/{OWNER}/{name}/traffic/popular/paths") or [])
        ][:8],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(DATA))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--only", default="")
    args = ap.parse_args()
    out = Path(args.out)
    only = {s.strip() for s in args.only.split(",") if s.strip()}

    repos = list_own_public_repos()
    if only:
        repos = [r for r in repos if r in only]

    snapshot = {"generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                "owner": OWNER, "repos": {}}
    for i, name in enumerate(repos, 1):
        try:
            snapshot["repos"][name] = collect_repo(name)
        except Exception as e:  # noqa: BLE001 - one bad repo must not stop the run
            print(f"  ! {name}: {e}", file=sys.stderr)
        print(f"  [{i}/{len(repos)}] {name}", file=sys.stderr)

    total_stars = sum(r["stars"] for r in snapshot["repos"].values())
    total_views = sum((r["views"] or {}).get("total", 0) for r in snapshot["repos"].values())
    print(f"repos={len(snapshot['repos'])} stars={total_stars} views14d={total_views}")

    if args.dry_run:
        print(json.dumps({k: v["stars"] for k, v in snapshot["repos"].items()}, indent=1)[:800])
        return 0

    daily = out / "daily"
    daily.mkdir(parents=True, exist_ok=True)
    day = dt.date.today().isoformat()
    (daily / f"{day}.json").write_text(json.dumps(snapshot, ensure_ascii=False, indent=1))

    days = sorted(p.stem for p in daily.glob("*.json"))
    (out / "index.json").write_text(json.dumps({
        "generated_at": snapshot["generated_at"],
        "owner": OWNER,
        "days": days,
        "repos": sorted(snapshot["repos"]),
    }, ensure_ascii=False, indent=1))
    print(f"wrote {daily / (day + '.json')} ({len(days)} day(s) total)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
