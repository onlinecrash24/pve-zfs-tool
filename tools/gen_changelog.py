#!/usr/bin/env python3
"""Regenerate CHANGELOG.md from the published GitHub releases.

The release notes are authored once, in the annotated git tag, and the release
workflow publishes them from there. This pulls the same text back into the repo
so the history is readable from a checkout or a source tarball, with no network
and no browser.

That makes CHANGELOG.md a derived file. Editing it by hand is pointless -- the
next run overwrites it. Fix the release notes on GitHub (or the tag) instead,
then run this again:

    gh api repos/onlinecrash24/pve-zfs-tool/releases --paginate | python tools/gen_changelog.py

The JSON arrives on stdin rather than being fetched here on purpose: invoking
`gh` from Python picks up a different HOME on Windows and fails to find the
credentials, so the pipe is both simpler and one less thing to get wrong.

Maintainer tool; not imported by the app and not exercised by the test suite.
"""

import json
import os
import re
import sys

REPO = "onlinecrash24/pve-zfs-tool"
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "CHANGELOG.md")

HEADER = """\
# Changelog

Every release of this project, newest first. Generated from the GitHub
releases by `tools/gen_changelog.py` -- edits here are overwritten, so change
the release notes (or the annotated tag they come from) instead.

Full history and container images: <https://github.com/{repo}/releases>
""".format(repo=REPO)


def fetch():
    """Read the releases JSON from stdin, newest first.

    `gh --paginate` concatenates one JSON array per page rather than merging
    them, so a repo past the first page arrives as `[...][...]`. Parsing the
    whole stream as a single document would raise on the second bracket and
    lose every release beyond page one, so split first.
    """
    # The API returns UTF-8, but stdin defaults to the console codepage on
    # Windows -- without this the em dashes in the notes arrive as mojibake
    # and get written back out that way.
    try:
        sys.stdin.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    s = sys.stdin.read().strip()
    if not s:
        sys.exit(f"no input -- pipe `gh api repos/{REPO}/releases --paginate` into this")
    # raw_decode, not a regex: a release body contains brackets of its own, so
    # any pattern-based split cuts a page in half and either raises or -- worse
    # -- silently keeps whatever parsed.
    decoder, idx, releases = json.JSONDecoder(), 0, []
    while idx < len(s):
        page, idx = decoder.raw_decode(s, idx)
        releases.extend(page)
        while idx < len(s) and s[idx].isspace():
            idx += 1
    return releases


def demote(body):
    """Push the body's own headings down one level.

    Release notes open with `## Something`, and the version itself is a `##`
    here -- without this every release body would break out of its own section
    in any renderer that builds a table of contents.
    """
    return re.sub(r"^(#{1,5}) ", r"#\1 ", body or "", flags=re.M)


def main():
    releases = fetch()
    if not releases:
        sys.exit("no releases returned -- is gh authenticated?")

    parts = [HEADER]
    for r in releases:
        tag = r.get("tag_name") or "?"
        date = (r.get("published_at") or "")[:10]
        body = demote((r.get("body") or "").strip())
        heading = f"## {tag}" + (f" -- {date}" if date else "")
        parts.append(heading + "\n\n" + (body if body else "_No notes._"))

    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n\n".join(parts).rstrip() + "\n")
    print(f"{OUT}: {len(releases)} releases")


if __name__ == "__main__":
    main()
