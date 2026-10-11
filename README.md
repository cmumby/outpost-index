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
      "origin": "curated",
      "latest_version": "1.18.2",
      "assets": {
        "x86_64": { "url": "…", "sha256": "…", "size": 66471104 }
      }
    }
  ]
}
```

- `origin` is `curated` or `appimagehub`. Curated apps are listed first.
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
