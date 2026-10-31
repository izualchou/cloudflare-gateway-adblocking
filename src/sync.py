"""Orchestration: fetch -> integrate -> (delete old) -> upload -> policy.

This is the single pipeline the GitHub Action runs. It implements the user's
requirement: integrate lists, drop invalid/duplicate entries, delete the old
list + policy, then upload the streamlined result.
"""
from __future__ import annotations

from loguru import logger

from . import settings
from .cloudflare import CloudflareClient, our_lists, our_policy
from .sources import fetch_all_domains, write_combined


def chunk_domains(domains: set[str], size: int = settings.MAX_ITEMS_PER_LIST) -> list[list[str]]:
    items = sorted(domains)  # deterministic ordering
    return [items[i : i + size] for i in range(0, len(items), size)]


def delete_old(client: CloudflareClient) -> None:
    lists = our_lists(client.get_lists())
    for l in lists:
        logger.info(f"Deleting old list {l['name']} ({l['id']})")
        client.delete_list(l["id"])

    policy = our_policy(client.get_rules())
    if policy:
        logger.info(f"Deleting old policy {policy['name']} ({policy['id']})")
        client.delete_policy(policy["id"])
    else:
        logger.info("No existing policy to delete.")


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
