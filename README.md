# Outpost Index

The catalogue behind [Outpost](https://github.com/cmumby/outpost), an app store for
AppImages on Linux.

Every hour, a GitHub Action merges [AppImageHub](https://appimage.github.io)'s feed
with the hand-picked apps in [`curated/`](curated), looks up each app's latest GitHub
release, and publishes the result to:

**https://cmumby.github.io/outpost-index/index.json**

The Outpost store only ever reads that file. It never calls GitHub itself.

## Adding your app

Open a pull request adding `curated/<your-app-id>.toml`:

```toml
name = "Photocraft"
summary = "A fast photo editor"           # one line, shown in search results
description = """
A longer description for the app's page.
"""
source_repo = "https://github.com/owner/photocraft"
categories = ["Graphics"]                 # freedesktop.org categories
icon_url = "https://example.com/icon.png" # optional; square PNG or SVG
screenshots = [                           # optional; PNG or JPEG, shown in this order
  "https://example.com/screenshot-1.png",
]

# Optional: which release files to use, if the architecture isn't in the file names.
[assets]
x86_64 = "Photocraft-*-x86_64.AppImage"
aarch64 = "Photocraft-*-aarch64.AppImage"
```

Your app needs to publish `.AppImage` files on its GitHub Releases page. Outpost uses
the latest non-prerelease release.

`verified = true` is set by maintainers once we've confirmed the repository is the
app's official home.

## Following a developer

`follow.toml` lists GitHub accounts whose apps are all included:

```toml
owners = ["storytold"]
```

Every public repo under those accounts whose latest release has AppImages shows up
in the store, marked as verified. The name and summary come from the repo
description ("FilmCraft: An open-source video editor" gives both), and the icon is
found in the repo's files. A file in `curated/` for the same repo overrides all of it.

## Featured apps

Curated apps with `featured = true` appear in Featured on Outpost's home page. The
New & Updated and Popular sections are worked out by Outpost from each app's latest
release date and GitHub stars, so they need no upkeep.

## index.json

```json
{
  "schema_version": 1,
  "generated_at": "2026-10-09T12:00:00Z",
  "apps": [
    {
      "id": "localsend",
      "name": "LocalSend",
      "summary": "Share files to nearby devices",
      "description": "…",
      "icon_url": "…",
      "screenshot_urls": ["…"],
      "categories": ["Network", "Utility"],
      "source_repo": "https://github.com/localsend/localsend",
      "verified": true,
      "featured": true,
      "origin": "curated",
      "stars": 93998,
      "latest_version": "1.18.2",
      "released_at": "2026-08-21T09:12:44Z",
      "assets": {
        "x86_64": { "url": "…", "sha256": "…", "size": 66471104 }
      }
    }
  ]
}
```

- `origin` is `curated`, `followed` or `appimagehub`. Verified apps are listed first.
- `screenshot_urls` may be empty. Curated apps without their own use AppImageHub's.
- `sha256` comes from GitHub when it has one. Otherwise the build downloads the file
  and hashes it, a couple of gigabytes per run, so for a while after a big change
  some files can be `null`. Clients should treat those as unverified.
- Apps are only listed if their latest GitHub release has an AppImage.

## Running it locally

Needs Python 3.11+ and nothing else.

```bash
GITHUB_TOKEN=$(gh auth token) python3 scripts/build_index.py --out site --limit 50
```

`--limit 50` processes the curated apps plus 50 from AppImageHub, which is quick.
`--no-hash` skips downloading files to hash them.

## Keeping it running

GitHub pauses scheduled workflows in repositories with no activity for 60 days. If
the index stops updating, re-enable the workflow from the Actions tab.
