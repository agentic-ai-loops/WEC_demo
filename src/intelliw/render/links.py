"""Relative links (docs/design/04-design.md, principle 4) and the link check."""

import posixpath
import re
from collections.abc import Iterable, Mapping
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote

from intelliw.render.errors import Problem

_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
_CSS_URL = re.compile(r"""url\(\s*(['"]?)(.*?)\1\s*\)""", re.IGNORECASE)
_LINK_ATTRS = {"href", "src", "action", "poster"}


def relative_url(from_file: str, to: str) -> str:
    """The link from the output file `from_file` to the site path `to`.

    Site paths are relative to the site root, without a leading `/`; a path ending in
    `/` (or empty, the root) names a folder's page and the link ends in `/` too.
    """
    if to.startswith("/"):
        raise ValueError(f"site paths have no leading '/': {to!r}")
    folder = to == "" or to.endswith("/")
    rel = posixpath.relpath(to.rstrip("/") or ".", posixpath.dirname(from_file) or ".")
    if folder:
        return "./" if rel == "." else rel + "/"
    return rel


class _Links(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        for name, value in attrs:
            if value is None:
                continue
            if name in _LINK_ATTRS:
                self.links.append(value)
            elif name == "srcset":
                self.links.extend(c.split()[0] for c in value.split(",") if c.split())


def links_in(name: str, text: str) -> list[str]:
    """The links in an html or css file (css `url()` also inside html)."""
    found = [m.group(2) for m in _CSS_URL.finditer(text)]
    if name.endswith(".html"):
        parser = _Links()
        parser.feed(text)
        found = parser.links + found
    return found


def _target(from_file: str, link: str) -> str | None:
    """The output file a relative link points to; None if it leaves the site."""
    path = unquote(link.split("#", 1)[0].split("?", 1)[0])
    if path == "":
        return from_file
    resolved = posixpath.normpath(posixpath.join(posixpath.dirname(from_file), path))
    if resolved == ".." or resolved.startswith("../"):
        return None
    if path.endswith("/") or resolved == ".":
        return posixpath.normpath(posixpath.join(resolved, "index.html"))
    return resolved


def check_links(files: Mapping[str, bytes | Path], sources: Mapping[str, str]) -> list[Problem]:
    """Root-relative links and relative links to missing files, in html and css output."""
    problems: list[Problem] = []
    present = set(files)
    for name, content in sorted(files.items()):
        if not name.endswith((".html", ".css")):
            continue
        raw = content.read_bytes() if isinstance(content, Path) else content
        text = raw.decode("utf-8", errors="replace")
        where = f"{name} (from {sources[name]})"
        for link in _unique(links_in(name, text)):
            link = link.strip()
            if not link or link.startswith("#") or link.startswith("//") or _SCHEME.match(link):
                continue
            if link.startswith("/"):
                problems.append(Problem(where, f"root-relative link {link!r}; use url()"))
                continue
            target = _target(name, link)
            if target is None:
                problems.append(Problem(where, f"link {link!r} points outside the site"))
            elif target not in present and posixpath.join(target, "index.html") not in present:
                problems.append(Problem(where, f"link {link!r}: {target} is not in the site"))
    return problems


def _unique(items: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(items))
