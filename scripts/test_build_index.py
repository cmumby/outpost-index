"""Tests for build_index.py's text cleanup. Run with:

    python3 -m unittest discover scripts
"""

import re
import unittest
from unittest import mock

import build_index
from build_index import QueryTooBig, fetch_releases, plain_line, summarize, tidy_text


class TidyText(unittest.TestCase):
    def test_paragraphs_become_markdown_paragraphs(self):
        # From the "8b" entry.
        self.assertEqual(
            tidy_text("<p>8B reads and edits profiles.</p>\n<p>8B is an independent project.</p>"),
            "8B reads and edits profiles.\n\n8B is an independent project.",
        )

    def test_lists_become_bullets_with_entities_and_spacing_fixed(self):
        # From the "android_file_transfer" entry.
        self.assertEqual(
            tidy_text(
                "<p>Features:</p><ul>\n  <li>Simple Qt UI.</li>\n  <li>FUSE wrapper (If you&apos;d prefer),\n"
                "             supporting partial reads.</li>\n</ul>"
            ),
            "Features:\n\n- Simple Qt UI.\n- FUSE wrapper (If you'd prefer), supporting partial reads.",
        )

    def test_numbered_and_nested_lists(self):
        self.assertEqual(
            tidy_text("<ol><li>One</li><li>Two<ul><li>Inner</li></ul></li></ol>"),
            "1. One\n2. Two\n  - Inner",
        )

    def test_inline_marks_and_links(self):
        self.assertEqual(
            tidy_text('<p>Run <code>app --help</code> or see <a href="https://x.org">the <b>site</b></a>.</p>'),
            "Run `app --help` or see [the **site**](https://x.org).",
        )
        # Links that can't open from Outpost keep just their text.
        self.assertEqual(tidy_text('<p>Read <a href="docs/USAGE.md">the guide</a></p>'), "Read the guide")

    def test_markdown_inside_html_is_kept(self):
        # From the "bench_cli" entry.
        self.assertEqual(tidy_text("<p>Benchmarks written in `Fortran`.</p>"), "Benchmarks written in `Fortran`.")

    def test_plain_text_is_only_unescaped(self):
        self.assertEqual(tidy_text("Tom &amp; Jerry: 1 < 2"), "Tom & Jerry: 1 < 2")
        self.assertEqual(tidy_text("Line one\n\n- a bullet"), "Line one\n\n- a bullet")

    def test_scripts_and_empty_marks_vanish(self):
        self.assertEqual(tidy_text("<p>Hi<script>alert(1)</script><b></b></p>"), "Hi")


class PlainLine(unittest.TestCase):
    def test_summaries_lose_all_markup(self):
        # The "neovim" summary showed its <p> in the store.
        self.assertEqual(
            plain_line("<p>Neovim is a refactor of the Vim text editor designed to encourage new features,…"),
            "Neovim is a refactor of the Vim text editor designed to encourage new features,…",
        )
        self.assertEqual(plain_line("Use **fast** `code`, see [docs](https://x.org)"), "Use fast code, see docs")

    def test_summary_of_a_list_only_description(self):
        description = "<ul><li>Simple Qt UI with progress dialogs.</li><li>No file size limits.</li></ul>"
        self.assertEqual(summarize(plain_line(tidy_text(description))), "Simple Qt UI with progress dialogs")


class FetchReleases(unittest.TestCase):
    def test_splits_batches_github_says_are_too_heavy(self):
        # GitHub refuses queries for more than 10 repos here, and "heavy/app" always.
        def fake_graphql(query, token):
            repos = re.findall(r'repository\(owner: "(\w+)", name: "(\w+)"\)', query)
            if len(repos) > 10 or ("heavy", "app") in repos:
                raise QueryTooBig("Resource limits for this query exceeded.")
            release = {"tagName": "v1", "publishedAt": "", "url": "", "description": "", "releaseAssets": {"nodes": []}}
            return {f"r{i}": {"stargazerCount": 0, "latestRelease": release} for i in range(len(repos))}

        repos = [f"owner/app{n}" for n in range(30)] + ["heavy/app"]
        with mock.patch.object(build_index, "graphql", fake_graphql), mock.patch.object(build_index, "log"):
            releases = fetch_releases(repos, "token")
        self.assertEqual(len(releases), 30, "every repo but the one GitHub always refuses")
        self.assertNotIn("heavy/app", releases)


if __name__ == "__main__":
    unittest.main()
