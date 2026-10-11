#!/usr/bin/env python3
"""Builds Outpost's index.json.

Merges AppImageHub's feed with our curated apps, looks up each app's latest GitHub
release, and writes one index.json that the Outpost store reads. Clients never call
GitHub themselves, so they never hit its rate limits.

Uses only the Python standard library (3.11+ for tomllib).

    GITHUB_TOKEN=... python3 scripts/build_index.py --out site
"""

import argparse
import fnmatch
import hashlib
import json
import os
import re
import sys
import time
import tomllib
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = 1
FEED_URL = "https://appimage.github.io/feed.json"
FEED_ICON_BASE = "https://appimage.github.io/database/"
GRAPHQL_URL = "https://api.github.com/graphql"
USER_AGENT = "outpost-index"

# Repositories per GraphQL request. Each request costs about one point of GitHub's
# hourly budget no matter how many repos it asks about, as long as it stays small.
BATCH_SIZE = 50

# Older release files have no checksum from GitHub, so we download and hash them
# ourselves. This caps how much we download per run; the rest are filled in on
# later runs (the previous index remembers checksums we've already worked out).
HASH_BUDGET_BYTES = 2 * 1024**3

ARCH_PATTERNS = {
    "x86_64": re.compile(r"x86[_-]64|amd64|x64", re.I),
    "aarch64": re.compile(r"aarch64|arm64", re.I),
}
OTHER_ARCH = re.compile(r"armhf|armv7|i386|i686|x86[_-]32|ia32|riscv|ppc64|s390x", re.I)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", default="site", help="folder to write index.json into")
    parser.add_argument("--curated", default="curated", help="folder of curated app files")
    parser.add_argument("--follow", default="follow.toml", help="file listing GitHub accounts to include")
    parser.add_argument("--previous", help="URL or path of the last published index.json")
    parser.add_argument("--limit", type=int, help="only process this many apps (for testing)")
    parser.add_argument("--no-hash", action="store_true", help="skip downloading files to hash them")
    args = parser.parse_args()

    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        sys.exit("GITHUB_TOKEN is not set (try: GITHUB_TOKEN=$(gh auth token) ...)")

    curated = load_curated(Path(args.curated))
    followed = load_followed(Path(args.follow), token)
    feed = load_feed()
    apps = merge(curated + followed, feed)
    trusted = [app for app in apps if app["origin"] != "appimagehub"]
    if args.limit:
        apps = trusted + [app for app in apps if app["origin"] == "appimagehub"][: args.limit]
    log(f"{len(curated)} curated + {len(followed)} followed + {len(feed)} AppImageHub apps -> {len(apps)} after dedupe")

    releases = fetch_releases([app["repo"] for app in apps], token)
    previous = load_previous(args.previous)
    budget = 0 if args.no_hash else HASH_BUDGET_BYTES

    entries = []
    for app in apps:
        release = releases.get(app["repo"].lower())
        if not release:
            continue
        assets = pick_assets(release["assets"], app.get("asset_patterns"))
        if not assets:
            continue
        if app["origin"] == "followed" and not app["icon_url"]:
            app["icon_url"] = find_icon(app["repo"], token)
        for asset in assets.values():
            if asset["sha256"] is None:
                asset["sha256"] = previous.get(asset["url"])
        entries.append(build_entry(app, release, assets))

    # Curated apps get first claim on the hashing budget.
    for entry in entries:
        for asset in entry["assets"].values():
            if asset["sha256"] is None and asset["size"] <= budget:
                asset["sha256"] = sha256_of_url(asset["url"])
                budget -= asset["size"]

    entries.sort(key=lambda entry: (not entry["verified"], entry["name"].lower()))
    index = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "apps": entries,
    }

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "index.json").write_text(json.dumps(index, indent=1, ensure_ascii=False))

    unhashed = sum(1 for e in entries for a in e["assets"].values() if a["sha256"] is None)
    log(f"wrote {out / 'index.json'}: {len(entries)} installable apps, {unhashed} files still without a checksum")


# --- Inputs ---------------------------------------------------------------------


def load_curated(folder):
    """One TOML file per app; the file name (without .toml) is the app's id."""
    apps = []
    for path in sorted(folder.glob("*.toml")):
        with path.open("rb") as file:
            data = tomllib.load(file)
        missing = {"name", "summary", "source_repo"} - data.keys()
        if missing:
            sys.exit(f"{path}: missing {', '.join(sorted(missing))}")
        apps.append(
            {
                "id": path.stem,
                "name": data["name"],
                "summary": data["summary"],
                "description": data.get("description", data["summary"]).strip(),
                "icon_url": data.get("icon_url"),
                "screenshot_urls": data.get("screenshots", []),
                "categories": data.get("categories", []),
                "repo": repo_from_url(data["source_repo"]),
                "verified": data.get("verified", False),
                "featured": data.get("featured", False),
                "origin": "curated",
                "asset_patterns": data.get("assets"),
            }
        )
    return apps


def load_followed(path, token):
    """Every public repo under the GitHub accounts in follow.toml. Repos that don't
    publish AppImages drop out later, when their releases are looked up."""
    if not path.exists():
        return []
    with path.open("rb") as file:
        owners = tomllib.load(file).get("owners", [])
    apps = []
    for owner in owners:
        cursor = None
        while True:
            data = graphql(
                """query($login: String!, $after: String) {
                  repositoryOwner(login: $login) {
                    repositories(first: 100, after: $after, isFork: false, privacy: PUBLIC, ownerAffiliations: OWNER) {
                      nodes { nameWithOwner name description isArchived }
                      pageInfo { hasNextPage endCursor }
                    }
                  }
                }""",
                token,
                {"login": owner, "after": cursor},
            )
            if not data.get("repositoryOwner"):
                log(f"follow.toml: no GitHub account called {owner}")
                break
            repos = data["repositoryOwner"]["repositories"]
            for repo in repos["nodes"]:
                if not repo["isArchived"]:
                    apps.append(followed_app(repo))
            if not repos["pageInfo"]["hasNextPage"]:
                break
            cursor = repos["pageInfo"]["endCursor"]
    return apps


def followed_app(repo):
    """Name and summary from the repo's description. Descriptions like
    "FilmCraft: An open-source video editor" give both."""
    description = (repo["description"] or "").strip()
    name, summary = repo["name"].replace("-", " ").replace("_", " ").title(), description
    label, colon, rest = description.partition(":")
    if colon and label.replace(" ", "").lower() in repo["name"].replace("-", "").lower():
        name, summary = label.strip(), rest.strip()
    return {
        "id": make_id(repo["name"]),
        "name": name,
        "summary": capitalize(summarize(summary)) or name,
        "description": description,
        "icon_url": None,
        "screenshot_urls": [],
        "categories": [],
        "repo": repo["nameWithOwner"],
        "verified": True,
        "origin": "followed",
    }


def load_feed():
    """AppImageHub's catalogue. We keep the apps that publish on GitHub."""
    feed = get_json(FEED_URL)
    apps = []
    for item in feed["items"]:
        repo = next((link["url"] for link in item.get("links") or [] if link["type"] == "GitHub"), None)
        if not repo:
            continue
        description = (item.get("description") or "").strip()
        icons = item.get("icons") or []
        apps.append(
            {
                "id": make_id(item["name"]),
                "name": item["name"],
                "summary": summarize(description) or item["name"],
                "description": description,
                "icon_url": FEED_ICON_BASE + icons[0] if icons else None,
                "screenshot_urls": [
                    FEED_ICON_BASE + shot for shot in item.get("screenshots") or [] if isinstance(shot, str)
                ],
                # Some feed entries have null categories.
                "categories": [c for c in item.get("categories") or [] if isinstance(c, str)],
                "repo": repo,
                "verified": False,
                "origin": "appimagehub",
            }
        )
    return apps


def merge(trusted, feed):
    """Curated apps win, then followed ones, then AppImageHub's. An app is dropped if
    it shares a repo or id with one we've already got. Curated and followed apps
    borrow AppImageHub's icon and screenshots if they don't have their own."""
    by_repo = {app["repo"].lower(): app for app in feed}
    for app in trusted:
        hub = by_repo.get(app["repo"].lower())
        if hub and not app["icon_url"]:
            app["icon_url"] = hub["icon_url"]
        if hub and not app["screenshot_urls"]:
            app["screenshot_urls"] = hub["screenshot_urls"]

    apps = []
    seen_repos = set()
    seen_ids = set()
    for app in trusted + feed:
        if app["repo"].lower() in seen_repos or app["id"] in seen_ids:
            continue
        seen_repos.add(app["repo"].lower())
        seen_ids.add(app["id"])
        apps.append(app)
    return apps


def load_previous(location):
    """Checksums from the last published index, keyed by download URL."""
    if not location:
        return {}
    try:
        if location.startswith("http"):
            index = get_json(location)
        else:
            index = json.loads(Path(location).read_text())
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as error:
        log(f"no previous index ({error}); starting fresh")
        return {}
    return {
        asset["url"]: asset["sha256"]
        for app in index.get("apps", [])
        for asset in app.get("assets", {}).values()
        if asset.get("sha256")
    }


# --- GitHub ---------------------------------------------------------------------


def fetch_releases(repos, token):
    """Latest (non-prerelease) release for each repo, keyed by lowercase owner/name."""
    releases = {}
    for start in range(0, len(repos), BATCH_SIZE):
        batch = repos[start : start + BATCH_SIZE]
        fields = []
        for i, repo in enumerate(batch):
            owner, name = repo.split("/", 1)
            fields.append(
                f"r{i}: repository(owner: {json.dumps(owner)}, name: {json.dumps(name)}) {{"
                " nameWithOwner stargazerCount latestRelease { tagName publishedAt"
                " releaseAssets(first: 100) { nodes { name size downloadUrl digest } } } }"
            )
        data = graphql("query { " + " ".join(fields) + " }", token)
        for i, repo in enumerate(batch):
            result = data.get(f"r{i}")
            release = result and result["latestRelease"]
            if release:
                releases[repo.lower()] = {
                    "tag": release["tagName"],
                    "published_at": release["publishedAt"],
                    "stars": result["stargazerCount"],
                    "assets": release["releaseAssets"]["nodes"],
                }
        log(f"  looked up {min(start + BATCH_SIZE, len(repos))}/{len(repos)} repos")
    return releases


def graphql(query, token, variables=None):
    request = urllib.request.Request(
        GRAPHQL_URL,
        data=json.dumps({"query": query, "variables": variables or {}}).encode(),
        headers={"Authorization": f"bearer {token}", "User-Agent": USER_AGENT},
    )
    for attempt in range(6):
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                body = json.load(response)
            break
        except urllib.error.HTTPError as error:
            # 403/429 are GitHub's "slow down" limits; 502/504 mean the query took
            # too long on their end. Both are worth waiting out and retrying.
            if error.code not in (403, 429, 502, 504) or attempt == 5:
                sys.exit(f"GitHub API error {error.code}: {error.read().decode(errors='replace')[:500]}")
            wait = int(error.headers.get("retry-after") or 30 * 2**attempt)
            log(f"  GitHub said {error.code}; waiting {wait}s before retrying")
            time.sleep(wait)
    # Deleted or renamed repos come back as errors alongside the data; that's fine.
    for error in body.get("errors", []):
        if error.get("type") != "NOT_FOUND":
            sys.exit(f"GitHub API error: {error.get('message')}")
    return body.get("data") or {}


def find_icon(repo, token):
    """Looks through a repo's files for its app icon, preferring the standard Linux
    icon folders (hicolor/256x256/apps and friends)."""
    request = urllib.request.Request(
        f"https://api.github.com/repos/{repo}/git/trees/HEAD?recursive=1",
        headers={"Authorization": f"bearer {token}", "User-Agent": USER_AGENT},
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            paths = [item["path"] for item in json.load(response)["tree"] if item["type"] == "blob"]
    except (OSError, urllib.error.URLError, KeyError, json.JSONDecodeError):
        return None

    def score(path):
        lower = path.lower()
        if not lower.endswith((".png", ".svg")) or re.search(r"favicon|android|ios/|macos|windows|test", lower):
            return None
        for rank, folder in enumerate(["hicolor/256x256/apps/", "hicolor/512x512/apps/", "hicolor/scalable/apps/", "hicolor/128x128/apps/"]):
            if folder in lower:
                return rank
        if re.search(r"(^|/)(app[-_]?)?icons?[-_/]", lower) or "/icon." in lower or "logo" in lower:
            return 10
        return None

    ranked = sorted((s, len(path), path) for path in paths if (s := score(path)) is not None)
    return f"https://raw.githubusercontent.com/{repo}/HEAD/{ranked[0][2]}" if ranked else None


# --- Building entries -----------------------------------------------------------


def pick_assets(assets, patterns=None):
    """Picks one AppImage per architecture from a release's files."""
    appimages = [a for a in assets if a["name"].lower().endswith(".appimage")]
    chosen = {}
    for arch, pattern in ARCH_PATTERNS.items():
        if patterns:
            matches = [a for a in appimages if arch in patterns and fnmatch.fnmatch(a["name"], patterns[arch])]
        else:
            matches = [a for a in appimages if pattern.search(a["name"])]
        if matches:
            chosen[arch] = min(matches, key=lambda a: len(a["name"]))

    # Many projects don't put the architecture in the file name; those are almost
    # always x86_64 builds.
    if "x86_64" not in chosen and not patterns:
        untagged = [
            a for a in appimages
            if not OTHER_ARCH.search(a["name"]) and not any(p.search(a["name"]) for p in ARCH_PATTERNS.values())
        ]
        if untagged:
            chosen["x86_64"] = min(untagged, key=lambda a: len(a["name"]))

    return {
        arch: {
            "url": asset["downloadUrl"],
            "sha256": asset["digest"].removeprefix("sha256:") if (asset.get("digest") or "").startswith("sha256:") else None,
            "size": asset["size"],
        }
        for arch, asset in chosen.items()
    }


def build_entry(app, release, assets):
    return {
        "id": app["id"],
        "name": app["name"],
        "summary": app["summary"],
        "description": app["description"],
        "icon_url": app["icon_url"],
        "screenshot_urls": app["screenshot_urls"],
        "categories": app["categories"],
        "source_repo": f"https://github.com/{app['repo']}",
        "verified": app["verified"],
        "featured": app.get("featured", False),
        "origin": app["origin"],
        "stars": release["stars"],
        "latest_version": release["tag"].removeprefix("v"),
        "released_at": release["published_at"],
        "assets": assets,
    }


def sha256_of_url(url):
    log(f"  hashing {url}")
    digest = hashlib.sha256()
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            while chunk := response.read(1024 * 1024):
                digest.update(chunk)
    except (OSError, urllib.error.URLError) as error:
        log(f"  could not hash {url}: {error}")
        return None
    return digest.hexdigest()


# --- Helpers --------------------------------------------------------------------


def repo_from_url(url):
    match = re.fullmatch(r"https://github\.com/([^/]+/[^/]+?)(?:\.git)?/?", url)
    if not match:
        sys.exit(f"source_repo must be a GitHub URL like https://github.com/owner/repo, got {url}")
    return match.group(1)


def make_id(name):
    return re.sub(r"[^a-z0-9._-]+", "-", name.lower()).strip("._-") or "app"


def summarize(description, limit=90):
    """First sentence of a description, cut down to fit on one line."""
    first = re.split(r"(?<=[.!?])\s", description, maxsplit=1)[0].rstrip(".")
    return first if len(first) <= limit else first[: limit - 1].rsplit(" ", 1)[0] + "…"


def capitalize(text):
    return text[:1].upper() + text[1:]


def get_json(url):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.load(response)


def log(message):
    print(message, file=sys.stderr)


if __name__ == "__main__":
    main()
