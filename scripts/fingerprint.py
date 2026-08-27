#!/usr/bin/env python3
"""Content-fingerprint a built static site, in place.

Renames every file under the given asset roots to carry a hash of its own
contents -- media/logo.png becomes media/logo.4f3a2b19.png -- and rewrites
every reference to it in the HTML, CSS, JS and SVG that point at it. That is
what makes the immutable Cache-Control in deploy.yml safe: a key whose name
contains its own hash can never hold different bytes, so a browser may keep
it for a year without ever being wrong.

RUN THIS IN CI, NOT IN THE REPOSITORY. It rewrites files in place. The site
in the tree stays exactly what the build produced; the hashed copy exists
only for the length of the job that uploads it. Running it twice over the
same tree hashes the hashed names again -- harmless, but it means a fresh
checkout each time, which is what CI gives you.

    fingerprint.py SITE_DIR [ROOT ...]     # roots default to: assets media

WHAT IT REWRITES. Any reference that resolves to a file it fingerprinted,
found in href/src/srcset/poster/content/data-* attributes, in url() and
@import, and in the <link>/<image> forms SVG uses. Relative, root-relative
and ../ forms are all resolved against the file they appear in and rewritten
back into the same shape. A reference that resolves to something outside the
asset roots is left alone, so absolute URLs, mailto:, data: and page links
are never touched.

WHAT IT CANNOT REWRITE, because no amount of parsing gets there: a path held
as a bare string in JavaScript, or assembled at runtime from pieces. That
reference keeps the old name, and the old name is gone once the file is
renamed, so it 404s. Watch the "nothing appears to reference" list below for
the tell -- an asset only a script names shows up there -- or keep such
assets outside the roots so they are never renamed.
"""

import hashlib
import os
import posixpath
import re
import sys
from urllib.parse import urlsplit

# Files that reference other assets, so they must be rewritten before their
# own hash is taken -- a stylesheet's bytes change when the image it points
# at is renamed, and hashing it first would name it after bytes it no longer
# has.
REWRITABLE = {".css", ".js", ".mjs", ".svg"}

# Pages: rewritten, never renamed. Their names are the site's URLs.
PAGES = {".html", ".htm", ".xml", ".webmanifest", ".json"}

HASH_LEN = 8

# Deliberately generous about what to rewrite: over-matching costs nothing,
# because a candidate is only rewritten when it resolves to a file that was
# actually fingerprinted, and everything else falls through untouched.
#
# The flag is about reporting, not rewriting. A strict pattern holds a path
# and nothing else, so one that resolves to nothing is a broken link worth
# saying so about. content= and data-* hold free text at least as often as
# they hold a path -- content="width=device-width" is not a missing file --
# so they are read for rewriting and ignored for reporting.
CANDIDATES = [
    (
        re.compile(
            r"""\b(?:href|src|poster)\s*=\s*(["'])(?P<url>[^"']*)\1""",
            re.IGNORECASE,
        ),
        True,
    ),
    (
        re.compile(
            r"""\b(?:content|data-[\w-]+)\s*=\s*(["'])(?P<url>[^"']*)\1""",
            re.IGNORECASE,
        ),
        False,
    ),
    # url(...) and @import "..." in CSS
    (re.compile(r"""url\(\s*(["']?)(?P<url>[^"')]*)\1\s*\)""", re.IGNORECASE), True),
    (re.compile(r"""@import\s+(["'])(?P<url>[^"']*)\1""", re.IGNORECASE), True),
]

# srcset is a comma-separated list of "url descriptor" pairs, so it needs its
# own pass over the attribute's contents.
SRCSET = re.compile(r"""\bsrcset\s*=\s*(["'])(?P<list>[^"']*)\1""", re.IGNORECASE)


def digest(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:HASH_LEN]


def hashed_name(rel, h):
    """media/logo.png -> media/logo.4f3a2b19.png"""
    head, tail = posixpath.split(rel)
    stem, dot, ext = tail.rpartition(".")
    tail = f"{stem}.{h}.{ext}" if dot else f"{tail}.{h}"
    return posixpath.join(head, tail) if head else tail


def resolve(url, from_rel):
    """A reference as written -> the site-root-relative path it points at.

    Returns None for anything that is not a local path: absolute URLs,
    protocol-relative //host, data:, mailto:, and bare fragments.
    """
    if not url or url.startswith(("#", "//")):
        return None
    parts = urlsplit(url)
    if parts.scheme or parts.netloc:
        return None
    if not parts.path:
        return None
    if parts.path.startswith("/"):
        target = parts.path.lstrip("/")
    else:
        target = posixpath.join(posixpath.dirname(from_rel), parts.path)
    return posixpath.normpath(target)


def rewrite_one(url, from_rel, mapping):
    """Point one reference at the fingerprinted file, keeping its shape.

    A root-relative reference stays root-relative and a relative one stays
    relative to the file it sits in, so this works the same whether the site
    is served at a domain root or under a preview path.
    """
    target = resolve(url, from_rel)
    if target is None or target not in mapping:
        return None
    parts = urlsplit(url)
    new = mapping[target]
    if parts.path.startswith("/"):
        path = "/" + new
    else:
        path = posixpath.relpath(new, posixpath.dirname(from_rel) or ".")
        if parts.path.startswith("./"):
            path = "./" + path
    if parts.query:
        path += "?" + parts.query
    if parts.fragment:
        path += "#" + parts.fragment
    return path


def rewrite_text(text, from_rel, mapping, seen=None):
    """Rewrite every resolvable reference in one file's text."""
    edits = []  # (start, end, replacement) over the original string

    for pattern, strict in CANDIDATES:
        for m in pattern.finditer(text):
            url = m.group("url")
            if seen is not None and strict:
                t = resolve(url, from_rel)
                if t is not None:
                    seen.add(t)
            new = rewrite_one(url, from_rel, mapping)
            if new is not None and new != url:
                edits.append((m.start("url"), m.end("url"), new))

    for m in SRCSET.finditer(text):
        base = m.start("list")
        for entry in re.finditer(r"[^,\s][^,]*", m.group("list")):
            bits = entry.group(0).split(None, 1)
            url = bits[0]
            if seen is not None:
                t = resolve(url, from_rel)
                if t is not None:
                    seen.add(t)
            new = rewrite_one(url, from_rel, mapping)
            if new is not None and new != url:
                start = base + entry.start() + entry.group(0).index(url)
                edits.append((start, start + len(url), new))

    if not edits:
        return text
    # Apply back to front so earlier offsets stay valid.
    for start, end, new in sorted(edits, reverse=True):
        text = text[:start] + new + text[end:]
    return text


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def write(path, text):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def main(argv):
    if len(argv) < 2:
        sys.stderr.write(__doc__)
        return 2

    site = argv[1]
    roots = argv[2:] or ["assets", "media"]

    if not os.path.isdir(site):
        sys.stderr.write(f"fingerprint: no such directory: {site}\n")
        return 1

    # Every file under the roots, site-root-relative, split by whether it
    # references other assets.
    leaves, rewritable = [], []
    for root in roots:
        base = os.path.join(site, root)
        if not os.path.isdir(base):
            print(f"fingerprint: no {root}/ in {site}, skipping")
            continue
        for dirpath, _, filenames in os.walk(base):
            for name in sorted(filenames):
                full = os.path.join(dirpath, name)
                rel = os.path.relpath(full, site).replace(os.sep, "/")
                bucket = rewritable if os.path.splitext(name)[1].lower() in REWRITABLE else leaves
                bucket.append(rel)

    if not leaves and not rewritable:
        print("fingerprint: nothing to fingerprint")
        return 0

    mapping = {}
    referenced = set()

    def rename(rel):
        full = os.path.join(site, rel)
        new_rel = hashed_name(rel, digest(full))
        os.rename(full, os.path.join(site, new_rel))
        mapping[rel] = new_rel

    # 1. Leaves first: nothing points out of them, so their bytes are final.
    for rel in sorted(leaves):
        rename(rel)

    # 2. Then the rewritable assets, each once everything it references has
    #    been renamed -- which is what lets one stylesheet @import another.
    pending = set(rewritable)
    while pending:
        ready = []
        for rel in sorted(pending):
            text = read(os.path.join(site, rel))
            deps = set()
            rewrite_text(text, rel, {}, seen=deps)
            if not (deps & pending) - {rel}:
                ready.append(rel)
        if not ready:
            # A reference cycle between stylesheets. Break it by taking the
            # rest in one pass; the hashes stay correct, only the ordering
            # guarantee is lost.
            print(f"fingerprint: reference cycle among {len(pending)} files, breaking it")
            ready = sorted(pending)
        for rel in ready:
            full = os.path.join(site, rel)
            write(full, rewrite_text(read(full), rel, mapping, seen=referenced))
            rename(rel)
            pending.discard(rel)

    # 3. Finally the pages, which are rewritten but never renamed: their
    #    names are the site's URLs.
    broken, pages = [], 0
    for dirpath, _, filenames in os.walk(site):
        for name in sorted(filenames):
            if os.path.splitext(name)[1].lower() not in PAGES:
                continue
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, site).replace(os.sep, "/")
            text = read(full)
            seen = set()
            new = rewrite_text(text, rel, mapping, seen=seen)
            referenced |= seen
            for target in sorted(seen):
                # Only references aimed into the asset roots. A link to a
                # page -- /about/, or any pretty URL with no file extension
                # -- is not this script's business and is routinely fine.
                if target.split("/", 1)[0] not in roots:
                    continue
                if target not in mapping and not os.path.exists(os.path.join(site, target)):
                    broken.append((rel, target))
            if new != text:
                write(full, new)
            pages += 1

    print(f"fingerprint: {len(mapping)} assets hashed, {pages} pages rewritten")

    # Both of these are worth saying out loud. An unreferenced asset is dead
    # weight being uploaded and paid for; a broken reference is a 404 the
    # deploy is about to ship, and it is easy to miss on a page that still
    # looks nearly right.
    orphans = sorted(set(mapping) - referenced)
    if orphans:
        print(f"fingerprint: {len(orphans)} asset(s) nothing appears to reference:")
        for rel in orphans:
            print(f"  {rel}")
    for rel, target in broken:
        print(f"::warning file={rel}::references {target}, which does not exist")

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
