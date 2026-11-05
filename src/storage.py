"""Git-friendly storage: one deduplicated active_jobs.json, an
overwritten CSV export, a rolling pruned-UID tombstone registry, and a
run_status.json audit trail.

No daily snapshots: active_jobs.json is the single evolving store,
pruned of jobs older than the retention window on every run. Pruned
UIDs are remembered in a bounded tombstone registry so a still-live
posting re-scraped after pruning is not re-ingested as brand new -
otherwise day-31 re-ingestion would reset first_seen and re-trigger
downstream notifications (the prune / re-ingestion loop). The registry
is deliberately bounded (default: last 5,000 UIDs / 60 days): once a
tombstone expires or is evicted, an old still-live posting CAN be
re-ingested once on a later run. That is the accepted trade-off for a
small, git-friendly file.
"""
from __future__ import annotations

import csv
import json
import logging
import os
import tempfile
from datetime import date, datetime, timedelta, timezone

from .models import Job

log = logging.getLogger(__name__)

CSV_FIELDS = [
    "uid", "score", "title", "title_en", "company", "country", "tier",
    "location", "posted_date", "source", "url", "signals",
    "matched_keywords", "first_seen", "last_seen",
]


def _today() -> date:
    return datetime.now(timezone.utc).date()


def _parse_day(value):
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _atomic_write_json(path: str, payload) -> None:
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2, sort_keys=True)
            fh.write("\n")
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


class JobStore:
    def __init__(self, storage_cfg: dict, base_dir: str = "."):
        self.cfg = storage_cfg
        self.data_dir = os.path.join(base_dir, storage_cfg.get("data_dir", "data"))
        os.makedirs(self.data_dir, exist_ok=True)
        self.active_path = os.path.join(
            self.data_dir, storage_cfg.get("active_jobs_file", "active_jobs.json"))
        self.csv_path = os.path.join(
            self.data_dir, storage_cfg.get("export_csv", "latest_export.csv"))
        self.status_path = os.path.join(
            self.data_dir, storage_cfg.get("run_status_file", "run_status.json"))
        self.retention_days = int(storage_cfg.get("retention_days", 30))
        # Tombstone registry: UIDs pruned by prune(), remembered so merge()
        # ignores them if the still-live posting is scraped again.
        self.tombstone_path = os.path.join(
            self.data_dir, storage_cfg.get("tombstone_file", "pruned_uids.json"))
        self.tombstone_max_uids = int(storage_cfg.get("tombstone_max_uids", 5000))
        self.tombstone_days = int(storage_cfg.get("tombstone_days", 60))
        self._tombstones = self._load_tombstones()
        if not os.path.isfile(self.tombstone_path):
            _atomic_write_json(self.tombstone_path, self._tombstones)

    # -- tombstone registry --------------------------------------------
    def _load_tombstones(self) -> dict:
        """uid -> ISO date it was pruned. Malformed files start empty."""
        if not os.path.isfile(self.tombstone_path):
            return {}
        try:
            with open(self.tombstone_path, "r", encoding="utf-8") as fh:
                payload = json.load(fh)
        except (ValueError, OSError) as exc:
            log.warning("ignoring unreadable tombstone file %s: %s",
                        self.tombstone_path, exc)
            return {}
        if not isinstance(payload, dict):
            return {}
        return {str(u): str(d) for u, d in payload.items()
                if isinstance(u, str) and _parse_day(d)}

    def _record_tombstones(self, uids, today: date) -> None:
        """Remember pruned UIDs, expiring old entries and capping size."""
        for uid in uids:
            self._tombstones[uid] = today.isoformat()
        expiry = today - timedelta(days=self.tombstone_days)
        self._tombstones = {
            uid: day for uid, day in self._tombstones.items()
            if (_parse_day(day) or expiry) >= expiry
        }
        if len(self._tombstones) > self.tombstone_max_uids:
            newest = sorted(
                self._tombstones.items(),
                key=lambda kv: (kv[1], kv[0]),
                reverse=True,
            )[: self.tombstone_max_uids]
            self._tombstones = dict(newest)
        _atomic_write_json(self.tombstone_path, self._tombstones)

    # -- active jobs ---------------------------------------------------
    def load(self) -> dict:
        if not os.path.isfile(self.active_path):
            return {}
        with open(self.active_path, "r", encoding="utf-8") as fh:
            payload = json.load(fh)
        return payload if isinstance(payload, dict) else {}

    def merge(self, jobs, today: date = None):
        """Insert new / refresh existing records. Returns (added, updated).

        Jobs whose uid sits in the tombstone registry (pruned earlier)
        are skipped: a still-live posting re-scraped after pruning must
        not come back as a brand-new record.
        """
        today = today or _today()
        store = getattr(self, "_store", None)
        if store is None:
            store = self.load()
        added = updated = skipped_tombstoned = 0
        for job in jobs:
            if job.uid in self._tombstones:
                skipped_tombstoned += 1
                continue
            record = job.to_dict()
            existing = store.get(job.uid)
            if existing is None:
                record["first_seen"] = today.isoformat()
                record["last_seen"] = today.isoformat()
                store[job.uid] = record
                added += 1
            else:
                record["first_seen"] = existing.get("first_seen") or today.isoformat()
                record["last_seen"] = today.isoformat()
                if ((existing.get("source_review") or {}).get("status") != "destination_mismatch"
                        and len(existing.get("description") or "") > len(record.get("description") or "")):
                    record["description"] = existing["description"]
                # Notification state belongs to the stored record: a
                # re-scrape must never reset an already-sent digest flag.
                record["notified_telegram"] = bool(existing.get("notified_telegram", False))
                record["notified_at"] = existing.get("notified_at")
                # Translation fields belong to the stored record too: a
                # re-scrape must never erase a verified translation when
                # this run did not re-translate (quota stop, throttle,
                # missing key). Only carry forward while the source title
                # is unchanged - a changed title invalidates its old
                # translation and must go back through the pipeline.
                if (existing.get("title") or "") == (record.get("title") or ""):
                    for field in ("title_en", "title_ar", "summary_ar"):
                        if field == "summary_ar" and (existing.get("source_review") or {}).get("status") == "destination_mismatch":
                            continue  # never revive claims derived from the wrong body
                        if not (record.get(field) or "").strip() and (existing.get(field) or "").strip():
                            record[field] = existing[field]
                # A source mismatch review is tied to exact title/URL/body,
                # not permanently to the UID. New evidence must be reviewed anew.
                review = existing.get("source_review") or {}
                if (review.get("status") == "destination_mismatch"
                        and all((existing.get(k) or "") == (getattr(job, k, "") or "")
                                for k in ("title", "url", "description", "source", "source_id"))):
                    record["source_review"] = review
                store[job.uid] = record
                updated += 1
        if skipped_tombstoned:
            log.info("merge skipped %d tombstoned (previously pruned) job(s)",
                     skipped_tombstoned)
        self._store = store
        return added, updated

    def prune(self, today: date = None) -> int:
        """Drop records whose posting is older than the retention window
        and record their UIDs in the tombstone registry."""
        today = today or _today()
        store = getattr(self, "_store", None)
        if store is None:
            store = self.load()
        cutoff = today - timedelta(days=self.retention_days)
        keep = {}
        for uid, record in store.items():
            day = _parse_day(record.get("posted_date")) or _parse_day(record.get("first_seen"))
            if day is None or day >= cutoff:
                keep[uid] = record
        removed = [uid for uid in store if uid not in keep]
        if removed:
            self._record_tombstones(removed, today)
        self._store = keep
        return len(removed)

    def save(self) -> int:
        store = getattr(self, "_store", None)
        if store is None:
            store = self.load()
        _atomic_write_json(self.active_path, store)
        return len(store)

    # -- notification state (Phase 4 Telegram digest) --------------------
    def get_unnotified_top_jobs(self, min_score: int, limit: int = 15,
                                countries=None) -> list:
        """Active, unexcluded records not yet sent in a Telegram digest,
        at or above ``min_score``, highest score first. Dict copies.
        ``countries`` (optional iterable of ISO codes) restricts the
        selection, e.g. the digest's focus_countries setting."""
        store = getattr(self, "_store", None)
        if store is None:
            store = self.load()
        wanted = {str(c).strip().upper() for c in countries} if countries else None
        records = [
            r for r in store.values()
            if not r.get("excluded")
            and not r.get("notified_telegram")
            and (r.get("score") or 0) >= min_score
            and (wanted is None or (r.get("country") or "").upper() in wanted)
        ]
        records.sort(key=lambda r: (-(r.get("score") or 0), r.get("posted_date") or ""))
        return [dict(r) for r in records[:limit]]

    def mark_notified(self, uids) -> int:
        """Mark stored jobs as sent in a Telegram digest.

        The marks are written to active_jobs.json atomically as part of
        this call, so a later merge or process never loses them. Note
        this does NOT make a digest exactly-once: a crash between the
        actual send and this call can still duplicate a notification.
        Bots should call mark_notified immediately after a successful
        send to keep that window small. Returns how many records were
        marked; unknown UIDs are ignored.
        """
        store = getattr(self, "_store", None)
        if store is None:
            store = self.load()
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        marked = 0
        for uid in uids:
            record = store.get(uid)
            if record is None:
                continue
            record["notified_telegram"] = True
            record["notified_at"] = now
            marked += 1
        if marked:
            self._store = store
            self.save()
        return marked

    # -- exports ---------------------------------------------------------
    def export_csv(self, tiers: dict) -> int:
        store = getattr(self, "_store", None)
        if store is None:
            store = self.load()
        tier_lookup = {c: t for t, members in tiers.items() for c in members}
        records = sorted(
            store.values(),
            key=lambda r: (-(r.get("score") or 0), r.get("posted_date") or ""),
        )
        with open(self.csv_path, "w", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=CSV_FIELDS)
            writer.writeheader()
            for r in records:
                writer.writerow({
                    "uid": r.get("uid", ""),
                    "score": r.get("score", 0),
                    "title": r.get("title", ""),
                    "title_en": r.get("title_en", ""),
                    "company": r.get("company", ""),
                    "country": r.get("country", ""),
                    "tier": tier_lookup.get(r.get("country"), ""),
                    "location": r.get("location", ""),
                    "posted_date": r.get("posted_date") or "",
                    "source": r.get("source", ""),
                    "url": r.get("url", ""),
                    "signals": ";".join(r.get("signals") or []),
                    "matched_keywords": ";".join(r.get("matched_keywords") or []),
                    "first_seen": r.get("first_seen", ""),
                    "last_seen": r.get("last_seen", ""),
                })
        return len(records)

    # -- run status ------------------------------------------------------
    def write_run_status(self, status: dict) -> None:
        previous = {}
        if os.path.exists(self.status_path):
            with open(self.status_path, encoding="utf-8") as fh:
                previous = json.load(fh)
        # Keep the latest honest health check for every country in the same
        # required persistent file, while top-level totals stay last-run only.
        latest = dict(previous.get("latest_by_country", {}))
        for country, sources in status.get("countries", {}).items():
            latest[country] = {"checked_at_utc": status.get("last_run_utc"),
                               "sources": sources}
        status["latest_by_country"] = latest
        _atomic_write_json(self.status_path, status)
