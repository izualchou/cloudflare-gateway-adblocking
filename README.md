# Cloudflare Gateway Adblocking (auto-updated)

Serverless ad-blocking via **Cloudflare Zero Trust Gateway DNS**, kept up to date
automatically with a **GitHub Actions** workflow. The workflow fetches curated
blocklists, **integrates them into one clean list** (dropping invalid + duplicate
entries), **deletes the previous lists/policy**, and **uploads the streamlined
result** — all while respecting Cloudflare's API rate limits.

This repo is a fork/redesign of
[slashtechno/cloudflare-gateway-adblocking](https://github.com/slashtechno/cloudflare-gateway-adblocking)
focused on an automated, rate-limit-safe pipeline.

---

## How it works

```
sources.txt ──► fetch ──► parse (hosts/domains/adblock)
                               │
whitelist.txt ─────────────────┤
                               ▼
                    normalize + validate (Cloudflare rules)
                               │
                               ▼
                    dedupe + subtract whitelist + cap
                               │
                               ▼
        delete OLD lists + policy  ──►  create chunked lists  ──►  create DNS policy
```

1. **Fetch** each URL in `sources.txt` (format auto-detected: `hosts`, `domains`, `adblock`).
2. **Integrate**: normalize every entry to a valid hostname, drop entries that
   violate Cloudflare Gateway list rules, dedupe, and subtract `whitelist.txt`.
3. **Delete old**: remove only *our* policy (*Block Ads*) **first**, then *our*
   lists (name prefix `adblock-`, plus any list the old policy referenced). The
   policy must go before the lists — Cloudflare refuses to delete a list that is
   still referenced by a policy. This avoids stale + new data coexisting and
   blowing the list quota.
4. **Upload**: create lists in chunks (≤ `CF_MAX_ITEMS_PER_LIST`, default 1000),
   wait for Cloudflare's asynchronous write to finish, then create one DNS
   `block` policy that references all lists.

## Source list selection

`sources.txt` ships with a balanced, low-false-positive default set that combines
an international baseline with strong Chinese / Asian coverage:

| Source | Format | Why |
| --- | --- | --- |
| StevenBlack/hosts (ads+malware) | hosts | Well-known, well-maintained international baseline |
| Anti-AD | domains | Strong coverage of Chinese / Asian ad & tracking domains |

Larger / alternative sources (Hagezi Pro, Peter Lowe, OISD, AdGuard, EasyList)
are included as **commented** lines you can uncomment. Because Cloudflare free
Zero Trust caps you at ~**300,000** total blocked domains (1,000 per list,
~300 lists), enable extras mindfully — the sync step auto-truncates past
`CF_MAX_TOTAL_ITEMS` (default 300,000).

## Cloudflare constraints handled

- **1,000 entries / list** (Standard plan; 5,000 on Enterprise) → lists are chunked.
- **Exact hostnames only** — no wildcards (`*.example.com`), no scheme/port/path.
  Entries that don't conform are dropped during integration.
- **No duplicates** — dedupe is case-insensitive and ignores a trailing dot
  (exactly how Cloudflare de-duplicates, including after Punycode).
- **Asynchronous writes** — Cloudflare returns `429 / error 10040` while a write
  is still processing; the client **waits and retries** automatically.
- **Rate limits** — every API call is spaced by `CF_MIN_INTERVAL` (default 1s)
  and honors `Retry-After` / exponential backoff with jitter. The Lists API
  allows 30,000 requests / 12h and 600 req/min; this stays far below both.

## Setup

### 1. Cloudflare API token
Create a token with **Account › Zero Trust › Edit** (Read+Edit). Copy the
Account ID from the Zero Trust dashboard sidebar.

### 2. GitHub Secrets
In the repo: **Settings › Secrets and variables › Actions › New repository secret**
- `CLOUDFLARE_ACCOUNT_ID`
- `CLOUDFLARE_TOKEN`

Optional repo **Variables** (same path, "Variables" tab) to override defaults:
`CF_MAX_ITEMS_PER_LIST`, `CF_MAX_TOTAL_ITEMS`, `CF_MIN_INTERVAL`, `CF_LIST_PREFIX`,
`CF_POLICY_NAME`.

### 3. Run it
The workflow runs **daily** (04:17 UTC), on **push** to the listed paths, and
manually via **Actions › Run workflow**. It uploads `blocklists/combined.txt`
as a build artifact for inspection.

## Run locally

```bash
pip install -r requirements.txt

# Integrate only (no Cloudflare calls) — writes blocklists/combined.txt
python -m src fetch

# Dry run: full pipeline but skip Cloudflare writes
DRY_RUN=true python -m src sync

# Real run (needs credentials in env / .env)
export CLOUDFLARE_ACCOUNT_ID=... CLOUDFLARE_TOKEN=...
python -m src sync

# Remove our lists + policy
python -m src delete
```

## Configuration (environment variables)

| Variable | Default | Purpose |
| --- | --- | --- |
| `CLOUDFLARE_ACCOUNT_ID` / `CLOUDFLARE_TOKEN` | – | API credentials (required for sync/delete) |
| `CF_LIST_PREFIX` | `adblock-` | Only lists with this prefix are managed/deleted |
| `CF_POLICY_NAME` | `Block Ads` | Name of the DNS block policy |
| `CF_MAX_ITEMS_PER_LIST` | `1000` | Items per list (5000 on Enterprise) |
| `CF_MAX_TOTAL_ITEMS` | `300000` | Hard cap on total blocked domains |
| `CF_MAX_LISTS` | `300` | Safety cap on number of lists |
| `CF_MIN_INTERVAL` | `1.0` | Seconds between API requests (raise if throttled) |
| `CF_MAX_RETRIES` | `6` | Retries on 429 / 5xx |
| `CF_BACKOFF_BASE` / `CF_BACKOFF_CAP` | `1.0` / `60` | Exponential backoff bounds (s) |
| `CF_WAIT_FOR_LISTS` | `true` | Wait for async list writes before policy creation |
| `DRY_RUN` | `false` | Integrate only, skip Cloudflare writes |
| `CF_DELETE_OLD` | `true` | Delete previous lists + policy before upload |

## Notes
- The DNS policy applies to devices routed through Cloudflare Gateway (WARP or
  custom Gateway DNS location).
- Only resources we created (prefix `adblock-`, policy `Block Ads`) are ever
  touched, so your other Gateway lists/rules are safe.
