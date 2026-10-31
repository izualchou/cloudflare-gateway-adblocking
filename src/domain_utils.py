"""Domain normalization & validation against Cloudflare Gateway DOMAIN-list rules.

Cloudflare Gateway DOMAIN lists require exact hostnames:
  * lowercase, no scheme (http://), no port, no path, no query
  * no wildcard (*.example.com is rejected)
  * labels of 1-63 chars; total length <= 253 chars
  * valid RFC-1123 hostname characters only (letters, digits, hyphen)
  * must look like an FQDN (contains a dot) and not be a private/reserved name
  * duplicate entries (also after Punycode conversion) are rejected
"""
from __future__ import annotations

import re

# Valid hostname: labels separated by dots, each label 1-63 chars, total <= 253.
_HOSTNAME_RE = re.compile(
    r"^(?=.{1,253}$)"  # overall length
    r"(?!-)"            # label not starting with hyphen
    r"(?:[a-z0-9]|-){1,63}"  # first label
    r"(?<!-)"           # label not ending with hyphen
    r"(?:\.(?!-)(?:[a-z0-9]|-){1,63}(?<!-))*$"  # subsequent labels
)

# Explicitly rejected / private / reserved TLDs and names.
_RESERVED = {
    "localhost", "local", "localhost.localdomain", "broadcasthost",
    "localdomain", "example", "example.com", "example.net", "example.org",
    "test", "invalid", "home", "lan", "corp", "intranet", "internal", "router",
}
_RESERVED_SUFFIXES = (".local", ".localhost", ".example", ".test", ".invalid",
                      ".home", ".lan", ".corp", ".internal")


def _is_ipv4(value: str) -> bool:
    parts = value.split(".")
    if len(parts) != 4:
        return False
    for p in parts:
        if not p.isdigit() or not 0 <= int(p) <= 255:
            return False
    return True


def normalize_domain(raw: str) -> str | None:
    """Return a Cloudflare-compatible domain, or None if it must be dropped."""
    if not raw:
        return None

    d = raw.strip().lower()

    # Strip a trailing dot (common in some feeds) and surrounding whitespace.
    d = d.rstrip(".")

    # Some feeds nest the loopback IP inside the domain field, e.g. a hosts line
    # "0.0.0.0 0.0.0.0.example.com". Strip any redundant leading IP prefix so we
    # recover the real domain instead of blocking a malformed string.
    for prefix in ("0.0.0.0.", "127.0.0.1.", "::1."):
        while d.startswith(prefix):
            d = d[len(prefix):]
    if not d:
        return None

    # Reject anything that is clearly not a bare hostname.
    if (
        not d
        or "/" in d          # has a path or scheme
        or ":" in d          # has a port or is IPv6
        or "@" in d          # email-style
        or "*" in d          # wildcard (not supported by Gateway)
        or " " in d
        or "\t" in d
        or ".." in d         # consecutive dots
        or "?" in d
        or "#" in d
        or "%" in d
        or "'" in d
        or '"' in d
    ):
        return None

    # Strip a leading scheme if one sneaked through (e.g. "https://x").
    if d.startswith("http://") or d.startswith("https://"):
        d = d.split("://", 1)[1]
    if "/" in d or ":" in d:
        return None

    # Drop non-ASCII (IDN). Cloudflare would Punycode these; to avoid
    # duplicate-after-punycode surprises we skip them. Adjust if you need IDN.
    try:
        d.encode("ascii")
    except UnicodeEncodeError:
        return None

    # Must contain a dot: we block FQDNs, not bare TLDs or single labels.
    if "." not in d:
        return None

    # Reject domains whose first label is purely numeric (e.g. "0.example.com",
    # "1.ackzany.com"). These are malformed artifacts in some source feeds and are
    # never legitimate ad/tracking hostnames.
    if d.split(".")[0].isdigit():
        return None

    if d in _RESERVED or d.endswith(_RESERVED_SUFFIXES):
        return None

    # Reject IP addresses (we only block hostnames here).
    if _is_ipv4(d):
        return None

    if not _HOSTNAME_RE.match(d):
        return None

    return d


def build_unique_domains(candidates) -> tuple[set[str], int]:
    """Normalize + validate + dedupe a stream of raw candidate strings.

    Returns (clean_set, dropped_count). Dedup is case-insensitive and ignores
    a single trailing dot, which is exactly how Cloudflare de-duplicates.
    """
    clean: set[str] = set()
    dropped = 0
    for raw in candidates:
        nd = normalize_domain(raw)
        if nd is None:
            dropped += 1
            continue
        clean.add(nd)
    return clean, dropped
