"""v6 stage A: slug-first delta helpers, kept pure for testing.

Two honesty rules live here:

1. The detail recheck window anchors on ``detail_fetched_at`` - stamped
   only by JobStore.merge when a fresh detail record was actually
   fetched - and never on ``last_seen``. Lifecycle seen-marking and
   dead-link sweeps run every run; any timestamp they can touch would
   keep a delta-skipped posting "fresh" forever and its stored detail
   (title, description, translation) would never be re-validated.

2. Lifecycle coverage must be honest: a FAILED sitemap-index
   enumeration or a max_jobs-capped fetch is not evidence that a
   missing posting is gone.
"""
from datetime import date, timedelta

RECHECK_WINDOW_DAYS = 7


def known_fresh_ids(records, today=None, window_days=RECHECK_WINDOW_DAYS):
    """Map source -> set(source_id) whose stored DETAIL data is still
    inside the recheck window.

    Anchored on detail_fetched_at. Records written before the anchor
    existed fall back to last_seen: that is accurate for them because
    every pre-stage-A run merged fresh details for everything it saw.
    """
    today = today or date.today()
    cutoff = (today - timedelta(days=window_days)).isoformat()
    fresh = {}
    for rec in records:
        source_id = rec.get("source_id")
        if not source_id:
            continue
        anchor = rec.get("detail_fetched_at") or rec.get("last_seen") or ""
        if anchor >= cutoff:
            fresh.setdefault(rec.get("source"), set()).add(source_id)
    return fresh


def pair_covered(status, stats, fetched, max_jobs):
    """Whether one (source, country) pair's enumeration this run is
    honest lifecycle coverage evidence.

    - status must be ok/partial (failed or skipped pairs prove nothing);
    - a failed sitemap-index enumeration (index_failed) proves nothing,
      even though index_count is set to 0 for the empty result;
    - sitemap-index sources (index_count present) enumerate the FULL
      URL inventory every run, so the enumerated index is coverage even
      though detail fetches are delta-limited;
    - other sources are covered only when the fetch was not capped by
      max_jobs.
    """
    if status not in ("ok", "partial"):
        return False
    stats = stats or {}
    if stats.get("index_failed"):
        return False
    if stats.get("index_count") is not None:
        return True
    return fetched < max_jobs
