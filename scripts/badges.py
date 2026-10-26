#!/usr/bin/env python3
"""Idempotently insert identity badges into every public non-fork repo README.

Design decisions (see ../README.md for the survey that motivated them):
  * Only add badges that stay honest for a personal account: license, last commit,
    primary language. Stars/issues/contributors are added only where the number
    is actually flattering (stars >= MIN_STARS), because a row of "0" reads worse
    than no badge at all.
  * Badges live between <!-- badges:start --> / <!-- badges:end --> so re-running
    this script rewrites its own block instead of stacking duplicates.
  * A repo whose LICENSE GitHub cannot classify (spdx NOASSERTION) gets a *static*
    badge with the SPDX id we determined by reading the file, because the dynamic
    shields endpoint renders "not identifiable by github".
  * No license file at all -> no license badge (there is nothing to link to).

Usage:
    python3 badges.py --dry-run          # show planned edits, change nothing
    python3 badges.py --apply            # write via GitHub contents API
    python3 badges.py --apply --only ai-memory-hub,MacMessageBackup
"""

from __future__ import annotations

import argparse
import base64
import json
import re
import subprocess
import sys
from pathlib import Path

OWNER = "monkey-sking"
MIN_STARS = 3
START = "<!-- badges:start -->"
END = "<!-- badges:end -->"

# GitHub returns NOASSERTION for these because the LICENSE file has a custom
# copyright header. Determined by reading the first lines of each LICENSE.
STATIC_LICENSE = {
    "ai-memory-hub": "Apache--2.0",
    "proximity-lock": "Apache--2.0",
    "TextGenie": "Apache--2.0",
    "feishu-doc-outline-enhancer": "Apache--2.0",
    "garmin-sync": "Apache--2.0",
    "browser-profile-launcher": "Apache--2.0",
    # orion-pwa ships a bespoke license; say so rather than mislabel it.
    "orion-pwa": "Custom",
}

# Badge line shapes we treat as "this repo already shows a license badge".
LICENSE_HINT = re.compile(
    r"img\.shields\.io/(?:badge/license|github/license)|badge\.fury\.io|opensource\.org/licenses",
    re.I,
)


def gh(*args: str) -> str:
    r = subprocess.run(["gh", *args], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args)} failed: {r.stderr.strip()}")
    return r.stdout


def gh_json(*args: str):
    out = gh(*args)
    return json.loads(out) if out.strip() else None


def repo_list() -> list[dict]:
    repos = gh_json(
        "repo", "list", OWNER, "--visibility", "public", "--limit", "200",
        "--json", "name,isFork,stargazerCount",
    )
    return [r for r in repos if not r["isFork"]]


def describe(name: str) -> dict:
    d = gh_json("api", f"repos/{OWNER}/{name}") or {}
    lic = d.get("license") or {}
    rel = ""
    try:
        rel = gh("api", f"repos/{OWNER}/{name}/releases/latest", "--jq", ".tag_name").strip()
    except RuntimeError:
        rel = ""
    lang = (d.get("language") or "").strip()
    return {
        "name": name,
        "spdx": lic.get("spdx_id") or "none",
        "license_file": lic.get("path"),
        "language": lang,
        "release": rel or None,
        "stars": d.get("stargazers_count", 0),
        "branch": d.get("default_branch") or "main",
    }


def badge(alt: str, src: str, href: str) -> str:
    return f"[![{alt}]({src})]({href})"


def build_block(info: dict, readme: str) -> str:
    name = info["name"]
    repo_url = f"https://github.com/{OWNER}/{name}"
    parts: list[str] = []

    # --- license -----------------------------------------------------------
    spdx = info["spdx"]
    already = bool(LICENSE_HINT.search(readme))
    if spdx not in ("none", "NOASSERTION") and not already:
        parts.append(badge(
            "License",
            f"https://img.shields.io/github/license/{OWNER}/{name}?style=flat-square",
            f"{repo_url}/blob/{info['branch']}/{info['license_file'] or 'LICENSE'}",
        ))
    elif spdx == "NOASSERTION" and name in STATIC_LICENSE and not already:
        label = STATIC_LICENSE[name].replace("--", "--")
        parts.append(badge(
            "License",
            f"https://img.shields.io/badge/license-{label}-blue?style=flat-square",
            f"{repo_url}/blob/{info['branch']}/{info['license_file'] or 'LICENSE'}",
        ))

    # --- last commit -------------------------------------------------------
    parts.append(badge(
        "Last commit",
        f"https://img.shields.io/github/last-commit/{OWNER}/{name}?style=flat-square",
        f"{repo_url}/commits/{info['branch']}",
    ))

    # --- language ----------------------------------------------------------
    if info["language"]:
        parts.append(badge(
            info["language"],
            f"https://img.shields.io/github/languages/top/{OWNER}/{name}?style=flat-square",
            f"{repo_url}/search?l={info['language']}",
        ))

    # --- release (only where one exists) -----------------------------------
    if info["release"]:
        parts.append(badge(
            "Release",
            f"https://img.shields.io/github/v/release/{OWNER}/{name}?style=flat-square",
            f"{repo_url}/releases",
        ))

    # --- stars (only where the number helps) -------------------------------
    if info["stars"] >= MIN_STARS:
        parts.append(badge(
            "Stars",
            f"https://img.shields.io/github/stars/{OWNER}/{name}?style=flat-square",
            f"{repo_url}/stargazers",
        ))

    # --- approximate read counter -----------------------------------------
    # hits.sh counts every fetch of the badge. GitHub proxies README images
    # through camo and caches them, so this UNDER-counts real readers; it is a
    # rough activity signal, not analytics. See README for the caveat.
    parts.append(badge(
        "views",
        f"https://hits.sh/github.com/{OWNER}/{name}.svg?style=flat-square&label=views&color=0e75b6",
        repo_url,
    ))

    return START + "\n" + " ".join(parts) + "\n" + END


# A line that is nothing but shield/hits images, optionally wrapped in links.
BADGE_LINE = re.compile(
    r"^\s*(?:\[!\[[^\]]*\]\([^)]*\)\]\([^)]*\)\s*|!\[[^\]]*\]\([^)]*\)\s*)+$"
)


def _is_badge_line(line: str) -> bool:
    if not BADGE_LINE.match(line):
        return False
    return "img.shields.io" in line or "hits.sh" in line or "badge.fury.io" in line


FENCE = re.compile(r"^\s{0,3}(`{3,}|~{3,})")


def _fence_mask(lines: list[str]) -> set[int]:
    """Line indices that belong to a fenced code block, fence markers included."""
    mask: set[int] = set()
    marker: str | None = None
    for i, ln in enumerate(lines):
        m = FENCE.match(ln)
        if marker is None:
            if m:
                marker = m.group(1)[0] * 3
                mask.add(i)
        else:
            mask.add(i)
            if m and m.group(1).startswith(marker):
                marker = None
    return mask


def _block_span(lines: list[str]) -> tuple[int, int] | None:
    """Locate the real managed block, ignoring markers quoted inside fences.

    Documentation that *describes* this convention contains the markers inside a
    ```markdown fence. Matching those would rewrite the example instead of the
    badge block (observed on this project's own README), so skip fenced lines.
    """
    masked = _fence_mask(lines)
    start = next(
        (i for i, ln in enumerate(lines) if ln.strip() == START and i not in masked), None
    )
    if start is None:
        return None
    end = next(
        (i for i in range(start + 1, len(lines)) if lines[i].strip() == END and i not in masked),
        None,
    )
    return (start, end) if end is not None else None


def splice(readme: str, block: str) -> str:
    """Insert or replace the managed badge block, leaving everything else alone."""
    lines = readme.split("\n")

    span = _block_span(lines)
    if span is not None:
        s, e = span
        return "\n".join(lines[:s] + [block] + lines[e + 1 :])

    # Skip a YAML front-matter block if present.
    start = 0
    if lines and lines[0].strip() == "---":
        for i in range(1, min(len(lines), 40)):
            if lines[i].strip() == "---":
                start = i + 1
                break

    # Find the first H1.
    h1 = next((i for i in range(start, len(lines)) if lines[i].startswith("# ")), None)
    if h1 is None:
        # No H1: prepend the block.
        return block + "\n\n" + readme

    # Walk past the H1 and any pre-existing hand-written badge rows so our block
    # joins the same visual group instead of starting a second one below it.
    anchor = h1
    i = h1 + 1
    while i < len(lines):
        if not lines[i].strip():
            i += 1
            continue
        if _is_badge_line(lines[i]):
            anchor = i
            i += 1
            continue
        break

    head = lines[: anchor + 1]
    tail = lines[anchor + 1 :]
    # Drop leading blank lines from the tail so we control spacing.
    while tail and not tail[0].strip():
        tail.pop(0)
    return "\n".join(head) + "\n\n" + block + "\n\n" + "\n".join(tail)


def read_readme(name: str) -> tuple[str, str] | tuple[None, None]:
    """Return (text, sha) or (None, None) when the repo has no README."""
    try:
        d = gh_json("api", f"repos/{OWNER}/{name}/readme")
    except RuntimeError:
        return None, None
    if not d:
        return None, None
    return base64.b64decode(d["content"]).decode("utf-8"), d["sha"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--only", default="")
    args = ap.parse_args()
    if not (args.apply or args.dry_run):
        ap.error("pass --dry-run or --apply")
    only = {s.strip() for s in args.only.split(",") if s.strip()}

    repos = repo_list()
    if only:
        repos = [r for r in repos if r["name"] in only]

    changed = skipped = failed = 0
    for r in repos:
        name = r["name"]
        try:
            text, sha = read_readme(name)
        except RuntimeError as e:
            print(f"FAIL {name}: {e}")
            failed += 1
            continue
        if text is None:
            print(f"SKIP {name}: no README")
            skipped += 1
            continue

        info = describe(name)
        block = build_block(info, text)
        new = splice(text, block)
        if new == text:
            print(f"SKIP {name}: already up to date")
            skipped += 1
            continue

        b0 = _block_span(text.split("\n"))
        print(f"{'EDIT' if args.apply else 'PLAN'} {name}: "
              f"block@{'replace' if b0 else 'insert'} "
              f"({len(block)}B, {new.count('img.shields.io') + new.count('hits.sh')} badge urls)")
        if args.dry_run:
            changed += 1
            continue

        payload = {
            "message": "docs(readme): add license / last-commit / language badges",
            "content": base64.b64encode(new.encode("utf-8")).decode("ascii"),
            "sha": sha,
            "branch": info["branch"],
        }
        proc = subprocess.run(
            ["gh", "api", "-X", "PUT", f"repos/{OWNER}/{name}/contents/README.md",
             "--input", "-"],
            input=json.dumps(payload), capture_output=True, text=True,
        )
        if proc.returncode != 0:
            print(f"FAIL {name}: {proc.stderr.strip()[:300]}")
            failed += 1
        else:
            changed += 1

    print(f"\n{'planned' if args.dry_run else 'applied'}: {changed}, skipped: {skipped}, failed: {failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
