"""Canonical URL/path normalization for categories.

The core identity of a category on SoundImports is its *canonical URL path*,
e.g. ``/en/home-audio/speakers/bookshelf-speakers/``. A bare URL slug such as
``speakers`` is NOT globally unique: it can appear under multiple branches.

Rules applied to build the canonical form:

* trailing slash always present
* one leading slash
* query strings, fragments and sessions stripped
* double and trailing slashes collapsed
* URL encoding percent-notation resolved where it is a literal (unreserved) char
* lowercase only for the path segments that are ASCII
* ``/en`` interactive language prefix preserved
"""

from __future__ import annotations

import re
import unicodedata
from typing import Optional
from urllib.parse import unquote, urlsplit


def normalize_category_path(url_or_path: str, base_url: Optional[str] = None) -> str:
    """Return a single canonical ``/en/.../`` path for a category URL/path.

    Accepts absolute URLs, absolute and relative paths, and web links. If a
    ``base_url`` is supplied, relative links are resolved against it first.
    """
    if not url_or_path:
        return ""

    raw = url_or_path.strip()
    if not raw:
        return ""

    if base_url and not raw.startswith(("http://", "https://")):
        from urllib.parse import urljoin

        raw = urljoin(base_url.rstrip("/") + "/", raw)

    split = urlsplit(raw)
    path = split.path

    # Drop session-id components that lightspeed appends (e.g. ;sid123)
    path = re.sub(r";[^/]+", "", path)
    path = path.replace("\\", "/")
    path = re.sub(r"/{2,}", "/", path)
    raw_segments = [seg for seg in path.split("/") if seg and seg not in (".", "..")]

    if not raw_segments:
        return "/"

    # Decode each segment independently so an encoded slash (%2F) can never
    # change the hierarchy. Unicode is normalized to stable ASCII slugs.
    cleaned = []
    for seg in raw_segments:
        seg = unquote(seg)
        seg = unicodedata.normalize("NFKD", seg)
        seg = seg.encode("ascii", "ignore").decode("ascii").lower()
        seg = re.sub(r"[^a-z0-9\-_~.]", "-", seg)
        seg = re.sub(r"-{2,}", "-", seg)
        seg = seg.strip("-")
        if seg:
            cleaned.append(seg)

    if not cleaned:
        return "/"

    return "/" + "/".join(cleaned) + "/"


def category_slug_from_path(canonical_path: str) -> str:
    """Return the final path segment of a canonical path (the leaf slug)."""
    path = canonical_path.strip("/")
    if not path:
        return ""
    return path.rsplit("/", 1)[-1]


def parent_path(canonical_path: str) -> Optional[str]:
    """Return the canonical path of the parent node, or None for a root node."""
    path = canonical_path.strip("/")
    if not path:
        return None
    parts = path.split("/")
    if len(parts) <= 1:
        return None
    parent_parts = parts[:-1]
    return "/" + "/".join(parent_parts) + "/"


def path_level(canonical_path: str) -> int:
    """Number of path segments, including the language prefix."""
    path = canonical_path.strip("/")
    return len([p for p in path.split("/") if p])


def path_segments(canonical_path: str):
    """Return the path segments excluding the language prefix, in order."""
    path = canonical_path.strip("/")
    if not path:
        return []
    return [p for p in path.split("/") if p]
