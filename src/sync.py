"""Orchestration: fetch -> integrate -> (delete old) -> upload -> policy.

This is the single pipeline the GitHub Action runs. It implements the user's
requirement: integrate lists, drop invalid/duplicate entries, delete the old
list + policy, then upload the streamlined result.
"""
from __future__ import annotations

import re

from loguru import logger

from . import settings
from .cloudflare import CloudflareClient, our_lists, our_policy
from .sources import fetch_all_domains, write_combined

# Cloudflare list IDs referenced inside a Gateway policy's `traffic` expression,
# e.g. "any(dns.domains[*] in $5435c6c2-5780-4695-b166-cbe87c266a9c)".
_LIST_REF_RE = re.compile(r"\$([0-9a-fA-F-]{8,})")


def chunk_domains(domains: set[str], size: int = settings.MAX_ITEMS_PER_LIST) -> list[list[str]]:
    items = sorted(domains)  # deterministic ordering
    return [items[i : i + size] for i in range(0, len(items), size)]


def _policy_list_ids(policy: dict) -> set[str]:
    """Extract the Cloudflare list IDs a Gateway policy references in `traffic`."""
    return set(_LIST_REF_RE.findall(policy.get("traffic", "")))


def delete_old(client: CloudflareClient) -> None:
    # 1) Delete OUR policy FIRST. Cloudflare refuses to delete a list that is
    #    still referenced by a policy ("List is in use at gateway policies"),
    #    so the policy must go before any list.
    policy = our_policy(client.get_rules())
    referenced: set[str] = set()
    if policy:
        logger.info(f"Deleting old policy {policy['name']} ({policy['id']})")
        referenced = _policy_list_ids(policy)
        try:
            client.delete_policy(policy["id"])
        except RuntimeError as exc:
            logger.warning(f"Could not delete policy (continuing): {exc}")
    else:
        logger.info("No existing policy to delete.")

    # 2) Delete our old lists: those matching our prefix AND any list the old
    #    policy referenced (this also cleans up lists created before we adopted
    #    the 'adblock-' prefix, e.g. '001', '002', ...).
    lists = our_lists(client.get_lists())
    to_delete = {l["id"] for l in lists} | referenced
    if not to_delete:
        logger.info("No old lists to delete.")
        return
    for lid in sorted(to_delete):
        logger.info(f"Deleting old list {lid}")
        try:
            client.delete_list(lid)
        except RuntimeError as exc:
            logger.warning(f"Could not delete list {lid} (continuing): {exc}")


def run_sync() -> None:
    domains = fetch_all_domains()
    write_combined(domains)

    if not domains:
        logger.error("No domains to upload. Aborting.")
        return

    chunks = chunk_domains(domains)
    logger.info(
        f"Prepared {len(domains)} domains across {len(chunks)} list(s) "
        f"(<= {settings.MAX_ITEMS_PER_LIST} items each)."
    )
    if len(chunks) > settings.MAX_LISTS:
        logger.warning(
            f"Number of lists ({len(chunks)}) exceeds CF_MAX_LISTS "
            f"({settings.MAX_LISTS}). Reduce sources or raise CF_MAX_ITEMS_PER_LIST."
        )

    if settings.DRY_RUN:
        logger.info(
            "[DRY RUN] Skipping Cloudflare writes. Would create "
            f"{len(chunks)} list(s) and one '{settings.POLICY_NAME}' policy."
        )
        return

    client = CloudflareClient()

    if settings.DELETE_OLD:
        delete_old(client)

    created_ids: list[str] = []
    expected: dict[str, int] = {}
    for i, chunk in enumerate(chunks, 1):
        name = f"{settings.LIST_PREFIX}{i:03d}"
        result = client.create_list(name, chunk)
        created_ids.append(result["id"])
        expected[result["id"]] = len(chunk)
        logger.info(f"Created list {name} with {len(chunk)} domains.")

    if settings.WAIT_FOR_LISTS:
        client.wait_for_lists(expected)

    client.create_policy(created_ids)
    logger.success(
        f"Done. {len(domains)} domains in {len(chunks)} list(s); "
        f"policy '{settings.POLICY_NAME}' created/updated."
    )
