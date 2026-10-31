"""Runtime configuration for the Cloudflare Gateway adblock sync.

All knobs are overridable via environment variables so the same code works
locally and inside GitHub Actions (secrets + repo variables).
"""
from __future__ import annotations

import os
from pathlib import Path

_BASE_DIR = Path(__file__).resolve().parent.parent


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _env_bool(name: str, default: bool) -> bool:
    return os.environ.get(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


# --- Cloudflare credentials (provided by GitHub Secrets) ---
ACCOUNT_ID = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "")
TOKEN = os.environ.get("CLOUDFLARE_TOKEN", "")

# --- Naming: only resources we own (prefixed / named like this) are touched ---
LIST_PREFIX = os.environ.get("CF_LIST_PREFIX", "adblock-")
POLICY_NAME = os.environ.get("CF_POLICY_NAME", "Block Ads")

# --- Sizing / Cloudflare limits ---
# Gateway list capacity: 1,000 items/list (Standard), 5,000 (Enterprise).
# Per-account list count and total-domain caps are also enforced by Cloudflare.
MAX_ITEMS_PER_LIST = _env_int("CF_MAX_ITEMS_PER_LIST", 1000)
MAX_TOTAL_ITEMS = _env_int("CF_MAX_TOTAL_ITEMS", 300_000)
MAX_LISTS = _env_int("CF_MAX_LISTS", 300)

# --- Rate limiting / retry ---
# Minimum spacing between any two API requests (seconds).
MIN_INTERVAL = _env_float("CF_MIN_INTERVAL", 1.0)
MAX_RETRIES = _env_int("CF_MAX_RETRIES", 6)
BACKOFF_BASE = _env_float("CF_BACKOFF_BASE", 1.0)
BACKOFF_CAP = _env_float("CF_BACKOFF_CAP", 60.0)
REQUEST_TIMEOUT = _env_int("CF_REQUEST_TIMEOUT", 30)

# --- Paths ---
SOURCES_FILE = os.environ.get("CF_SOURCES_FILE", str(_BASE_DIR / "sources.txt"))
WHITELIST_FILE = os.environ.get("CF_WHITELIST_FILE", str(_BASE_DIR / "whitelist.txt"))
COMBINED_OUTPUT = os.environ.get("CF_COMBINED_OUTPUT", str(_BASE_DIR / "blocklists" / "combined.txt"))

# --- Behavior ---
DRY_RUN = _env_bool("DRY_RUN", False)
DELETE_OLD = _env_bool("CF_DELETE_OLD", True)
WAIT_FOR_LISTS = _env_bool("CF_WAIT_FOR_LISTS", True)
WAIT_TIMEOUT = _env_int("CF_WAIT_TIMEOUT", 600)          # seconds
WAIT_POLL_INTERVAL = _env_int("CF_WAIT_POLL_INTERVAL", 15)

USER_AGENT = "cloudflare-gateway-adblocking (+https://github.com/izualchou/cloudflare-gateway-adblocking)"
REQUEST_HEADERS = {"User-Agent": USER_AGENT}
