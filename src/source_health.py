"""Per source-country run health, persisted separately from job state
(source-expansion Phase 1).

Every scrape writes data/source_health.json alongside (never inside)
the job store. An unavailable, blocked or failed source stays visible in
coverage reporting instead of being silently omitted. The file is
bounded: one latest record per source|country plus a short rolling list
of run summaries.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone

log = logging.getLogger(__name__)

HEALTH_FILE = "source_health.json"
MAX_RECENT_RUNS = 30


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def record_from_result(result, kept: int, duration_sec: float) -> dict:
    """One health record from a ScrapeResult.

    ``kept`` is how many of the fetched jobs survived scoring/min-score
    in this run. Stats are whatever the adapter honestly measured;
    missing counters stay absent rather than invented.
    """
    return {
        "source": result.source,
        "country": result.country,
        "status": result.status,
        "fetched": result.fetched,
        "kept": kept,
        "errors": list(result.errors[:10]),
        "notes": list(result.notes[:10]),
        "stats": dict(getattr(result, "stats", {}) or {}),
        "duration_sec": round(float(duration_sec), 1),
    }


def load_health(data_dir: str) -> dict:
    path = os.path.join(data_dir, HEALTH_FILE)
    if not os.path.isfile(path):
        return {"latest_by_source_country": {}, "recent_runs": []}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            payload = json.load(fh)
        if isinstance(payload, dict):
            payload.setdefault("latest_by_source_country", {})
            payload.setdefault("recent_runs", [])
            return payload
    except (ValueError, OSError) as exc:
        log.warning("ignoring unreadable health file %s: %s", path, exc)
    return {"latest_by_source_country": {}, "recent_runs": []}


def update_health(data_dir: str, label: str, records) -> str:
    """Merge one run's records into data/source_health.json atomically."""
    from .storage import _atomic_write_json  # repo-standard atomic write

    health = load_health(data_dir)
    latest = health["latest_by_source_country"]
    stamp = _now()
    for record in records:
        record = dict(record, checked_at_utc=stamp)
        latest["{}|{}".format(record["source"], record["country"])] = record
    failures = sum(1 for r in records if r["status"] in ("failed", "partial"))
    summary = {"checked_at_utc": stamp, "label": label,
               "source_country_records": len(records), "failing": failures}
    recent = [summary] + list(health["recent_runs"])
    health = {"updated_utc": stamp, "latest_by_source_country": latest,
              "recent_runs": recent[:MAX_RECENT_RUNS]}
    path = os.path.join(data_dir, HEALTH_FILE)
    _atomic_write_json(path, health)
    return path
