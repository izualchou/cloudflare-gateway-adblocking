"""CLI entry point.

Subcommands:
  sync     Full pipeline: fetch -> integrate -> delete old -> upload -> policy.
           (default when no subcommand is given)
  delete   Only delete our old lists + policy (useful for a clean slate).
  fetch    Only download + integrate sources and write blocklists/combined.txt
           (no Cloudflare calls; handy for local inspection or a dry run).
"""
from __future__ import annotations

import argparse
import sys

from loguru import logger

from . import settings
from .cloudflare import CloudflareClient, our_lists, our_policy
from .sources import fetch_all_domains, write_combined
from .sync import delete_old, run_sync


def _setup_logging(level: str) -> None:
    logger.remove()
    logger.add(
        sys.stderr,
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
            "<level>{level: <8}</level> | {message}"
        ),
        level=level,
    )


def cmd_sync(_args: argparse.Namespace) -> None:
    run_sync()


def cmd_delete(_args: argparse.Namespace) -> None:
    if settings.DRY_RUN:
        logger.info("[DRY RUN] Would delete old lists + policy, but not doing it.")
        return
    client = CloudflareClient()
    delete_old(client)
    logger.success("Old lists + policy removed.")


def cmd_fetch(_args: argparse.Namespace) -> None:
    domains = fetch_all_domains()
    write_combined(domains)
    logger.success(f"Fetched + integrated {len(domains)} domains (no Cloudflare calls).")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cloudflare-gateway-adblocking",
        description="Serverless adblocking via Cloudflare Zero Trust Gateway",
    )
    parser.add_argument("--log-level", "-l", default="INFO", help="Log level")
    parser.add_argument(
        "--account-id", "-a", help="Cloudflare account ID (or CLOUDFLARE_ACCOUNT_ID)"
    )
    parser.add_argument(
        "--token", "-t", help="Cloudflare API token (or CLOUDFLARE_TOKEN)"
    )
    sub = parser.add_subparsers(dest="subcommand")

    sub.add_parser("sync", help="Fetch, integrate, and upload (default)").set_defaults(
        func=cmd_sync
    )
    sub.add_parser("delete", help="Delete our old lists + policy").set_defaults(
        func=cmd_delete
    )
    sub.add_parser("fetch", help="Only fetch + integrate (no Cloudflare)").set_defaults(
        func=cmd_fetch
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    _setup_logging(getattr(args, "log_level", "INFO"))

    # Allow credential overrides via flags.
    if getattr(args, "account_id", None):
        settings.ACCOUNT_ID = args.account_id
    if getattr(args, "token", None):
        settings.TOKEN = args.token

    func = getattr(args, "func", None) or cmd_sync  # default to sync
    func(args)


if __name__ == "__main__":
    main()
