"""Fetch upstream blocklists, parse them into candidate domains, then integrate.

Supported upstream formats (auto-detected, or forced via sources.txt):
  * hosts   -> "0.0.0.0 domain", "127.0.0.1 domain", "::1 domain"
  * domains -> one bare domain per line
  * adblock -> "||domain^", "||domain^$third-party", etc. (Adblock Plus syntax)
"""
from __future__ import annotations

import os
import re

import requests
from loguru import logger

from . import settings
from .domain_utils import build_unique_domains

_HOSTS_RE = re.compile(r"^(?:0\.0\.0\.0|127\.0\.0\.1|::1)\s+(\S+)")
_ADBLOCK_RE = re.compile(r"^\|\|([a-z0-9.\-]+)\^?")
_INLINE_COMMENT_RE = re.compile(r"\s+#.*$")


def load_sources() -> list[tuple[str, str]]:
    """Read sources.txt: one '<url> [format]' per line, '#' comments allowed."""
    sources: list[tuple[str, str]] = []
    path = settings.SOURCES_FILE
    if not os.path.exists(path):
        logger.warning(f"Sources file not found: {path}")
        return sources
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            url = parts[0]
            fmt = parts[1] if len(parts) > 1 else "auto"
            sources.append((url, fmt))
    logger.info(f"Loaded {len(sources)} source(s) from {path}")
    return sources


def load_whitelist() -> set[str]:
    wl: set[str] = set()
    path = settings.WHITELIST_FILE
    if not os.path.exists(path):
        return wl
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            dom = line.split("#")[0].strip().lower().rstrip(".")
            if dom:
                wl.add(dom)
    return wl


def fetch_content(url: str) -> str:
    resp = requests.get(
        url, headers=settings.REQUEST_HEADERS, timeout=settings.REQUEST_TIMEOUT
    )
    resp.raise_for_status()
    # Some hosts send gzip/deflate transparently; requests handles it.
    return resp.text


def _detect_format(lines) -> str:
    for ln in lines:
        if ln.startswith("||"):
            return "adblock"
        if _HOSTS_RE.match(ln):
            return "hosts"
    return "domains"


def parse_blocklist(text: str, fmt: str = "auto") -> set[str]:
    """Parse raw blocklist text into a set of candidate domain strings."""
    lines = text.splitlines()
    if fmt == "auto":
        fmt = _detect_format(lines)

    out: set[str] = set()
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("!"):
            continue

        if fmt == "adblock":
            # Skip exception rules and element-hiding/css rules.
            if line.startswith("@@") or "##" in line or "#@#" in line:
                continue
            m = _ADBLOCK_RE.match(line)
            if m:
                out.add(m.group(1))
            continue

        if fmt == "hosts":
            m = _HOSTS_RE.match(line)
            if m:
                out.add(m.group(1))
            continue

        # Plain domain list: take the first token, drop an inline comment.
        dom = _INLINE_COMMENT_RE.sub("", line).strip().split()[0] if line.split() else ""
        if dom:
            out.add(dom)
    return out


def fetch_all_domains() -> set[str]:
    """Fetch every configured source, parse, validate, dedupe, subtract whitelist.

    This is the "integrate the lists, drop invalid + duplicate entries" step.
    """
    sources = load_sources()
    if not sources:
        logger.error("No sources configured. Edit sources.txt and re-run.")
        return set()

    raw: set[str] = set()
    for url, fmt in sources:
        try:
            text = fetch_content(url)
        except Exception as exc:  # noqa: BLE001 - keep going if one source fails
            logger.error(f"Failed to fetch {url}: {exc}")
            continue
        parsed = parse_blocklist(text, fmt)
        logger.info(f"Fetched {url}: {len(parsed)} candidate domains (format={fmt})")
        raw |= parsed

    logger.info(f"Combined {len(raw)} raw candidate domains from all sources.")

    clean, dropped = build_unique_domains(raw)

    wl = load_whitelist()
    if wl:
        before = len(clean)
        clean -= wl
        logger.info(f"Applied whitelist: removed {before - len(clean)} domains.")

    if len(clean) > settings.MAX_TOTAL_ITEMS:
        logger.warning(
            f"Final list ({len(clean)}) exceeds MAX_TOTAL_ITEMS "
            f"({settings.MAX_TOTAL_ITEMS}); truncating to stay within quota."
        )
        clean = set(sorted(clean)[: settings.MAX_TOTAL_ITEMS])

    logger.info(
        f"Integration complete: {len(raw)} raw -> {dropped} invalid dropped -> "
        f"{len(clean)} final unique domains."
    )
    return clean


def write_combined(domains: set[str]) -> None:
    out = settings.COMBINED_OUTPUT
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        for d in sorted(domains):
            fh.write(d + "\n")
    logger.info(f"Wrote {len(domains)} domains to {out}")
