"""Cloudflare Gateway API client with built-in rate-limit + retry handling.

Rate-limit strategy (the user explicitly asked to avoid Cloudflare throttling):
  * A global minimum spacing (MIN_INTERVAL) between ANY two API requests.
  * On HTTP 429 or 5xx: honor the `Retry-After` header if present, otherwise
    exponential backoff with jitter (capped).
  * Gateway list writes are asynchronous; while a write is still processing the
    API returns 429 (error 10040). Our retry loop absorbs this transparently.
  * All list/rule reads are paginated so we never miss resources to delete.
"""
from __future__ import annotations

import time
from email.utils import parsedate_to_datetime
from typing import Any

import requests
from loguru import logger

from . import settings


class CloudflareClient:
    def __init__(self, account_id: str = "", token: str = ""):
        self.account_id = account_id or settings.ACCOUNT_ID
        self.token = token or settings.TOKEN
        if not self.account_id or not self.token:
            raise RuntimeError(
                "Missing Cloudflare credentials. Set CLOUDFLARE_ACCOUNT_ID and "
                "CLOUDFLARE_TOKEN (env / GitHub Secrets)."
            )
        self.base = f"https://api.cloudflare.com/client/v4/accounts/{self.account_id}"
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
                "User-Agent": settings.USER_AGENT,
            }
        )
        self._last_request = 0.0

    # ------------------------------------------------------------------ #
    # Rate limiting + retry core
    # ------------------------------------------------------------------ #
    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_request
        wait = settings.MIN_INTERVAL - elapsed
        if wait > 0:
            time.sleep(wait)

    def request(
        self, method: str, path: str, json: Any = None, params: dict | None = None
    ) -> dict:
        url = self.base + path
        for attempt in range(settings.MAX_RETRIES + 1):
            self._throttle()
            try:
                resp = self.session.request(
                    method,
                    url,
                    json=json,
                    params=params,
                    timeout=settings.REQUEST_TIMEOUT,
                )
            except requests.RequestException as exc:
                logger.warning(
                    f"Network error on {method} {path}: {exc}; retry {attempt + 1}"
                )
                self._backoff(attempt)
                continue
            self._last_request = time.monotonic()

            if resp.status_code in (200, 201):
                return resp.json()

            if resp.status_code == 429 or 500 <= resp.status_code < 600:
                self._handle_throttle(resp, attempt)
                continue

            # Any other non-2xx is fatal.
            logger.error(
                f"Fatal API error {resp.status_code} on {method} {path}: {resp.text}"
            )
            raise RuntimeError(f"Cloudflare API {resp.status_code}: {resp.text}")

        raise RuntimeError(f"Exhausted retries for {method} {path}")

    def _handle_throttle(self, resp: requests.Response, attempt: int) -> None:
        retry_after = resp.headers.get("Retry-After")
        if retry_after:
            delay = self._parse_retry_after(retry_after)
            if delay is not None:
                logger.warning(
                    f"Rate limited (429). Retry-After={delay:.0f}s; sleeping."
                )
                time.sleep(min(delay, settings.BACKOFF_CAP * 5))
                return
        delay = min(settings.BACKOFF_CAP, settings.BACKOFF_BASE * (2 ** attempt))
        delay += (delay * 0.2) * __import__("random").uniform(0, 1)  # jitter
        logger.warning(
            f"Rate limited / 5xx. Backing off {delay:.1f}s (attempt {attempt + 1})."
        )
        time.sleep(delay)

    @staticmethod
    def _parse_retry_after(value: str) -> float | None:
        value = value.strip()
        try:
            return float(value)
        except ValueError:
            pass
        try:
            return max(0.0, parsedate_to_datetime(value).timestamp() - time.time())
        except (TypeError, ValueError):
            return None

    # ------------------------------------------------------------------ #
    # Read (paginated)
    # ------------------------------------------------------------------ #
    def get_lists(self) -> list[dict]:
        items: list[dict] = []
        page = 1
        while True:
            data = self.request(
                "GET", "/gateway/lists", params={"page": page, "per_page": 100}
            )
            items.extend(data.get("result", []))
            info = data.get("result_info", {})
            if info.get("page", 1) >= info.get("total_pages", 1):
                break
            page += 1
        return items

    def get_rules(self) -> list[dict]:
        items: list[dict] = []
        page = 1
        while True:
            data = self.request(
                "GET", "/gateway/rules", params={"page": page, "per_page": 100}
            )
            items.extend(data.get("result", []))
            info = data.get("result_info", {})
            if info.get("page", 1) >= info.get("total_pages", 1):
                break
            page += 1
        return items

    # ------------------------------------------------------------------ #
    # Write
    # ------------------------------------------------------------------ #
    def create_list(self, name: str, items: list[str]) -> dict:
        body = {
            "name": name,
            "type": "DOMAIN",
            "description": "Managed by cloudflare-gateway-adblocking",
            "items": [{"value": v} for v in items],
        }
        return self.request("POST", "/gateway/lists", json=body)["result"]

    def delete_list(self, list_id: str) -> None:
        self.request("DELETE", f"/gateway/lists/{list_id}")

    def create_policy(self, list_ids: list[str]) -> dict:
        traffic = " or ".join(f"any(dns.domains[*] in ${lid})" for lid in list_ids)
        body = {
            "name": settings.POLICY_NAME,
            "description": "Block ad / tracking / malware domains (managed automatically)",
            "action": "block",
            "traffic": traffic,
            "enabled": True,
            "filters": ["dns"],
        }
        return self.request("POST", "/gateway/rules", json=body)["result"]

    def delete_policy(self, rule_id: str) -> None:
        self.request("DELETE", f"/gateway/rules/{rule_id}")

    # ------------------------------------------------------------------ #
    # Async write handling
    # ------------------------------------------------------------------ #
    def wait_for_lists(self, expected: dict[str, int]) -> bool:
        """Poll until every created list reports the expected item count.

        Gateway processes list writes asynchronously; waiting here avoids the
        account-wide write lock (429/10040) when we create the policy next.
        """
        deadline = time.monotonic() + settings.WAIT_TIMEOUT
        while time.monotonic() < deadline:
            try:
                data = self.request(
                    "GET", "/gateway/lists", params={"per_page": 100}
                )
            except RuntimeError:
                time.sleep(settings.WAIT_POLL_INTERVAL)
                continue
            current = {l["id"]: l.get("num_items", 0) for l in data.get("result", [])}
            if all(current.get(lid) == exp for lid, exp in expected.items()):
                logger.info("All lists finished processing.")
                return True
            logger.info("Lists still processing; waiting before creating policy...")
            time.sleep(settings.WAIT_POLL_INTERVAL)
        logger.warning("Timed out waiting for lists; proceeding anyway.")
        return False


# ---------------------------------------------------------------------- #
# Helpers to identify OUR resources only (never touch the user's others)
# ---------------------------------------------------------------------- #
def our_lists(all_lists: list[dict]) -> list[dict]:
    return [
        l
        for l in all_lists
        if l.get("name", "").startswith(settings.LIST_PREFIX)
        and l.get("type") == "DOMAIN"
    ]


def our_policy(rules: list[dict]) -> dict | None:
    for r in rules:
        if r.get("name") == settings.POLICY_NAME:
            return r
    return None
