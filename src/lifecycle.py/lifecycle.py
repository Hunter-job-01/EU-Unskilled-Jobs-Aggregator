"""Bounded live-link checks for v6 lifecycle tracking.

The scrape is a delta: most stored records are not re-fetched every run,
so scrape presence alone cannot prove a posting is gone. This module
spot-checks a bounded number of stored records per run against their
live source and returns hard evidence only:

- EURES postings: the public per-id JSON endpoint 404s once the vacancy
  is removed; any other outcome is inconclusive or alive.
- Other sources: a hard 404/410 on the posting URL counts as dead.
  Soft-404s (200 with a generic page), redirects, 403s, throttling and
  network errors are INCONCLUSIVE and never count. Indeed domains are
  skipped entirely (datacenter traffic is blocked, so no answer is
  evidence).

A record is deactivated only after the configured number of consecutive
confirmed-dead checks (see JobStore.update_lifecycle), so one flaky
response never removes a job from the dashboard.
"""
from __future__ import annotations

import logging
import re

log = logging.getLogger(__name__)

EURES_PORTAL_RE = re.compile(
    r"https?://(?:www\.)?europa\.eu/eures/portal/jv-se/jv-details/([^/?#]+)")
EURES_DETAIL_API = "https://europa.eu/eures/api/jv-searchengine/public/jv/id/{}"
INDEED_HOST_RE = re.compile(r"(^|\.)indeed\.[a-z.]+(/|$)", re.I)


def checkable_url(url: str) -> bool:
    """True when a live check on this URL can yield trustworthy evidence."""
    if not url or not url.startswith("http"):
        return False
    if INDEED_HOST_RE.search(url):
        return False  # blocked from datacenters; every result is a lie
    return True


def check_live(http, record) -> object:
    """True = confirmed live, False = confirmed dead, None = inconclusive.

    Uses the client's session directly (no raise_for_status, no retry
    storm): a hard 404/410 is a definitive answer, not a transient
    failure, and must come back as a response.
    """
    import random
    from .scrapers.base import USER_AGENTS
    url = (record.get("url") or "").strip()
    if not checkable_url(url):
        return None
    m = EURES_PORTAL_RE.match(url)
    if m:
        url = EURES_DETAIL_API.format(m.group(1))
    try:
        resp = http.session.get(
            url, headers={"User-Agent": random.choice(USER_AGENTS)},
            timeout=http.timeout, allow_redirects=True, stream=True)
        if resp.status_code in (404, 410):
            return False
        if 200 <= resp.status_code < 400:
            return True
        return None
    except Exception as exc:  # noqa: BLE001 - inconclusive, never evidence
        log.info("live check inconclusive for %s: %s", url, exc)
        return None


def select_candidates(records, seen_uids, limit: int) -> list:
    """Records worth a live check this run: active, not re-scraped, with a
    checkable URL, highest score first (dashboard visibility order)."""
    candidates = []
    for uid, r in records.items():
        if uid in seen_uids:
            continue
        lc = r.get("lifecycle") or {}
        if lc.get("status", "active") != "active":
            continue
        if r.get("excluded"):
            continue
        if not checkable_url(r.get("url") or ""):
            continue
        candidates.append(r)
    candidates.sort(key=lambda r: (-(r.get("score") or 0),
                                   r.get("last_seen") or ""))
    return candidates[: max(0, int(limit))]


def run_dead_link_sweep(store, http, seen_uids, limit: int = 40):
    """Check up to ``limit`` stored records; return (dead_uids, live_uids)."""
    records = getattr(store, "_store", None) or store.load()
    dead, live = set(), set()
    for record in select_candidates(records, set(seen_uids), limit):
        outcome = check_live(http, record)
        if outcome is True:
            live.add(record.get("uid"))
        elif outcome is False:
            dead.add(record.get("uid"))
    if dead or live:
        log.info("dead-link sweep: %d confirmed live, %d confirmed dead",
                 len(live), len(dead))
    return dead, live
