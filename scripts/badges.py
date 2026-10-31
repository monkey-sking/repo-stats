#!/usr/bin/env python3
"""Idempotently sync truthful, project-aware README badges across public repos.

The managed block is the only part this script edits. Existing hand-written
badges outside it remain untouched. Re-running is safe.

Usage:
    python3 scripts/badges.py --dry-run
    python3 scripts/badges.py --apply
    python3 scripts/badges.py --apply --only TextGenie,DuoStage
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import json
import re
import subprocess
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

OWNER = "monkey-sking"
START = "<!-- badges:start -->"
END = "<!-- badges:end -->"
WORKFLOW_MAX_AGE_DAYS = 45

# GitHub reports NOASSERTION for licenses with custom copyright headers.
# These were checked against the actual LICENSE files.
STATIC_LICENSE = {
    "ai-memory-hub": "Apache--2.0",
    "proximity-lock": "Apache--2.0",
    "TextGenie": "Apache--2.0",
    "feishu-doc-outline-enhancer": "Apache--2.0",
    "garmin-sync": "Apache--2.0",
    "browser-profile-launcher": "Apache--2.0",
    "orion-pwa": "Custom",
}

BADGE_LINK = re.compile(r"\[!\[([^\]]*)\]\(([^)]*)\)\]\(([^)]*)\)")
FENCE = re.compile(r"^\s{0,3}(`{3,}|~{3,})")
LANGUAGE_LABELS = {
    "license", "license: mit", "license: apache-2.0", "license: custom",
    "last commit", "language", "release", "latest release", "stars", "forks",
    "views", "npm version", "npm downloads", "pypi version", "pypi downloads",
    "ci", "tests", "build", "build & deploy", "build & test", "security scan",
    "codeql",
}


def gh(*args: str) -> str:
    p = subprocess.run(["gh", *args], capture_output=True, text=True)
    if p.returncode:
        raise RuntimeError(f"gh {' '.join(args)} failed: {p.stderr.strip()}")
    return p.stdout


def gh_json(*args: str):
    raw = gh(*args)
    return json.loads(raw) if raw.strip() else None


def optional_gh_json(*args: str):
    try:
        return gh_json(*args)
    except Exception:
        return None


def repo_list() -> list[dict]:
    rows = gh_json(
        "repo", "list", OWNER, "--visibility", "public", "--limit", "200",
        "--json", "name,isFork",
    )
    return [r for r in rows if not r.get("isFork")]


def http_json(url: str):
    req = Request(url, headers={"User-Agent": "repo-stats-badge-sync/1.0"})
    try:
        with urlopen(req, timeout=12) as response:
            return json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, ValueError):
        return None


def package_info(name: str) -> dict | None:
    """Return npm metadata only when package.json is public and npm confirms it."""
    raw = optional_gh_json("api", f"repos/{OWNER}/{name}/contents/package.json")
    if not isinstance(raw, dict) or not raw.get("content"):
        return None
    try:
        package = json.loads(base64.b64decode(raw["content"]).decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    package_name = package.get("name")
    if not package_name or package.get("private") is True:
        return None
    registry = http_json(f"https://registry.npmjs.org/{quote(package_name, safe='')}")
    if not isinstance(registry, dict) or not registry.get("dist-tags", {}).get("latest"):
        return None
    return {"name": package_name, "version": registry["dist-tags"]["latest"]}


def workflow_info(name: str, branch: str) -> dict | None:
    """Choose a recent, real CI/build/test/security run on the default branch."""
    payload = optional_gh_json("api", f"repos/{OWNER}/{name}/actions/runs?per_page=50")
    if not isinstance(payload, dict):
        return None
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=WORKFLOW_MAX_AGE_DAYS)
    candidates = []
    for run in payload.get("workflow_runs", []):
        path = run.get("path") or ""
        if not path.startswith(".github/workflows/"):
            continue
        if run.get("head_branch") != branch:
            continue
        filename = Path(path).name
        stem = filename.lower()
        if not re.search(r"(ci|test|build|check|security|codeql|lint)", stem):
            continue
        if re.search(r"(release|deploy|sync|schedule)", stem) and "build" not in stem:
            continue
        created = run.get("created_at")
        try:
            when = dt.datetime.fromisoformat(created.replace("Z", "+00:00"))
        except (AttributeError, ValueError):
            continue
        if when < cutoff:
            continue
        candidates.append((when, run, filename))
    if not candidates:
        return None
    _, run, filename = max(candidates, key=lambda item: item[0])
    title = (run.get("name") or "").lower()
    if "security" in title or "secret" in title:
        label = "Security Scan"
    elif "codeql" in title:
        label = "CodeQL"
    elif "deploy" in title:
        label = "Build & Deploy"
    elif "test" in title or "core ci" in title:
        label = "Tests"
    elif "build" in title:
        label = "Build"
    else:
        label = "CI"
    return {"file": filename, "label": label}


def describe(name: str) -> dict:
    meta = gh_json("api", f"repos/{OWNER}/{name}") or {}
    license_data = meta.get("license") or {}
    branch = meta.get("default_branch") or "main"
    release = optional_gh_json("api", f"repos/{OWNER}/{name}/releases/latest")
    release_tag = release.get("tag_name") if isinstance(release, dict) else None
    return {
        "name": name,
        "branch": branch,
        "language": (meta.get("language") or "").strip(),
        "license_spdx": license_data.get("spdx_id") or "none",
        "license_path": license_data.get("path"),
        "stars": meta.get("stargazers_count", 0),
        "forks": meta.get("forks_count", 0),
        "release": release_tag,
        "release_assets": len(release.get("assets", [])) if isinstance(release, dict) else 0,
        "workflow": workflow_info(name, branch),
        "npm": package_info(name),
    }


def badge(alt: str, src: str, href: str) -> str:
    return f"[![{alt}]({src})]({href})"


def fence_mask(lines: list[str]) -> set[int]:
    masked: set[int] = set()
    marker: str | None = None
    for i, line in enumerate(lines):
        m = FENCE.match(line)
        if marker is None:
            if m:
                marker = m.group(1)[0] * 3
                masked.add(i)
        else:
            masked.add(i)
            if m and m.group(1).startswith(marker):
                marker = None
    return masked


def block_span(lines: list[str]) -> tuple[int, int] | None:
    masked = fence_mask(lines)
    start = next((i for i, line in enumerate(lines)
                  if line.strip() == START and i not in masked), None)
    if start is None:
        return None
    end = next((i for i in range(start + 1, len(lines))
                if lines[i].strip() == END and i not in masked), None)
    return (start, end) if end is not None else None


def extra_badges(readme: str, info: dict) -> list[str]:
    """Keep non-managed badges already placed inside the managed block."""
    lines = readme.split("\n")
    span = block_span(lines)
    if span is None:
        return []
    s, e = span
    old = "\n".join(lines[s + 1:e])
    extras = []
    for alt, src, href in BADGE_LINK.findall(old):
        label = alt.strip().lower()
        source = src.lower()
        if "hits.sh" in source:
            continue
        if (label in LANGUAGE_LABELS or label.startswith("license")
                or label == (info.get("language") or "").lower()
                or "img.shields.io/github/" in source
                or "img.shields.io/npm/" in source
                or "img.shields.io/pypi/" in source
                or "img.shields.io/badge/license" in source):
            continue
        extras.append(badge(alt, src, href))
    return extras


def outside_managed(readme: str) -> str:
    lines = readme.split("\n")
    span = block_span(lines)
    if span is None:
        return readme
    start, end = span
    return "\n".join(lines[:start] + lines[end + 1:])


def build_block(info: dict, readme: str) -> str:
    name = info["name"]
    branch = info["branch"]
    root = f"https://github.com/{OWNER}/{name}"
    outside = outside_managed(readme)
    parts: list[str] = []

    # Community signals: always show the live values, including zero.
    parts.append(badge(
        "Stars", f"https://img.shields.io/github/stars/{OWNER}/{name}?style=flat-square",
        f"{root}/stargazers",
    ))
    parts.append(badge(
        "Forks", f"https://img.shields.io/github/forks/{OWNER}/{name}?style=flat-square",
        f"{root}/network/members",
    ))
    parts.append(badge(
        "Last commit", f"https://img.shields.io/github/last-commit/{OWNER}/{name}?style=flat-square",
        f"{root}/commits/{branch}",
    ))

    # License: only when GitHub can identify it or we have checked the custom license.
    spdx = info["license_spdx"]
    license_path = info["license_path"] or "LICENSE"
    license_already = re.search(r"img\.shields\.io/(?:github/license|badge/license|npm/l/)", outside, re.I)
    if spdx not in ("none", "NOASSERTION") and not license_already:
        parts.append(badge(
            "License", f"https://img.shields.io/github/license/{OWNER}/{name}?style=flat-square",
            f"{root}/blob/{branch}/{license_path}",
        ))
    elif name in STATIC_LICENSE and not license_already:
        label = STATIC_LICENSE[name]
        parts.append(badge(
            "License", f"https://img.shields.io/badge/license-{label}-blue?style=flat-square",
            f"{root}/blob/{branch}/{license_path}",
        ))

    # Language is GitHub's detected primary language, not a guessed stack.
    language_already = re.search(r"img\.shields\.io/github/languages/top|img\.shields\.io/badge/(?:JavaScript|TypeScript|Python|Go|Swift|Rust|PowerShell|Ruby|C%23|C\+\+|Shell|Bash|HTML|CSS)", outside, re.I)
    if info["language"] and not language_already:
        language = info["language"]
        parts.append(badge(
            language, f"https://img.shields.io/github/languages/top/{OWNER}/{name}?style=flat-square",
            f"{root}/search?l={quote(language)}",
        ))

    # A release badge appears only when a real latest release exists.
    if info["release"] and not re.search(r"img\.shields\.io/github/v/release", outside, re.I):
        parts.append(badge(
            "Latest Release", f"https://img.shields.io/github/v/release/{OWNER}/{name}?style=flat-square",
            f"{root}/releases/latest",
        ))
        if info["release_assets"] and not re.search(r"img\.shields\.io/github/downloads", outside, re.I):
            parts.append(badge(
                "Downloads", f"https://img.shields.io/github/downloads/{OWNER}/{name}/total?style=flat-square",
                f"{root}/releases",
            ))

    # Package badges appear only after the public npm registry confirms the package.
    if info["npm"]:
        package = info["npm"]["name"]
        package_url = f"https://www.npmjs.com/package/{package}"
        if not re.search(r"img\.shields\.io/npm/v/", outside, re.I):
            parts.append(badge(
                "npm version", f"https://img.shields.io/npm/v/{package}?style=flat-square",
                package_url,
            ))
        if not re.search(r"img\.shields\.io/npm/dm/", outside, re.I):
            parts.append(badge(
                "npm downloads", f"https://img.shields.io/npm/dm/{package}?style=flat-square",
                package_url,
            ))

    # CI badge only when a matching workflow has a recent run on the default branch.
    workflow = info["workflow"]
    if workflow and not re.search(r"/actions/workflows/[^ )]+/badge\.svg", outside, re.I):
        filename = workflow["file"]
        parts.append(badge(
            workflow["label"],
            f"https://github.com/{OWNER}/{name}/actions/workflows/{filename}/badge.svg?branch={quote(branch)}",
            f"{root}/actions/workflows/{filename}",
        ))

    # Preserve project-specific hand-written badges, but not stale visitor counters.
    parts.extend(extra_badges(readme, info))
    return START + "\n" + " ".join(parts) + "\n" + END


def splice(readme: str, block: str) -> str:
    lines = readme.split("\n")
    span = block_span(lines)
    if span is not None:
        s, e = span
        return "\n".join(lines[:s] + [block] + lines[e + 1:])

    # Skip YAML front matter.
    start = 0
    if lines and lines[0].strip() == "---":
        for i in range(1, min(len(lines), 40)):
            if lines[i].strip() == "---":
                start = i + 1
                break
    h1 = next((i for i in range(start, len(lines)) if lines[i].startswith("# ")), None)
    if h1 is None:
        return block + "\n\n" + readme
    return "\n".join(lines[:h1 + 1]) + "\n\n" + block + "\n\n" + "\n".join(lines[h1 + 1:]).lstrip("\n")


def read_readme(name: str) -> tuple[str, str] | tuple[None, None]:
    data = optional_gh_json("api", f"repos/{OWNER}/{name}/readme")
    if not isinstance(data, dict) or not data.get("content"):
        return None, None
    try:
        return base64.b64decode(data["content"]).decode("utf-8"), data["sha"]
    except (ValueError, UnicodeDecodeError):
        return None, None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--only", default="")
    args = parser.parse_args()
    if not (args.apply or args.dry_run):
        parser.error("pass --dry-run or --apply")

    only = {item.strip() for item in args.only.split(",") if item.strip()}
    repos = repo_list()
    if only:
        repos = [repo for repo in repos if repo["name"] in only]

    changed = skipped = failed = 0
    for index, repo in enumerate(repos, 1):
        name = repo["name"]
        try:
            readme, sha = read_readme(name)
            if readme is None:
                print(f"SKIP {name}: no readable README")
                skipped += 1
                continue
            info = describe(name)
            new_readme = splice(readme, build_block(info, readme))
            if new_readme == readme:
                print(f"SKIP {name}: already up to date")
                skipped += 1
                continue

            print(f"{'EDIT' if args.apply else 'PLAN'} [{index}/{len(repos)}] {name}")
            if args.dry_run:
                changed += 1
                continue

            payload = {
                "message": "docs(readme): sync project-aware badges",
                "content": base64.b64encode(new_readme.encode("utf-8")).decode("ascii"),
                "sha": sha,
                "branch": info["branch"],
            }
            result = subprocess.run(
                ["gh", "api", "-X", "PUT", f"repos/{OWNER}/{name}/contents/README.md", "--input", "-"],
                input=json.dumps(payload), capture_output=True, text=True,
            )
            if result.returncode:
                print(f"FAIL {name}: {result.stderr.strip()[:300]}")
                failed += 1
            else:
                changed += 1
        except Exception as error:
            print(f"FAIL {name}: {error}")
            failed += 1

    mode = "planned" if args.dry_run else "applied"
    print(f"\n{mode}: {changed}, skipped: {skipped}, failed: {failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
