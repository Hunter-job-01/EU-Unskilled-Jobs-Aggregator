"""Apply manually reviewed title fills to the store at pipeline time.

Why this exists: the store (data/active_jobs.json) is several MB, too
large to edit safely through the only publication channel we have (the
GitHub web editor). Reviewed manual fills are small, so they live in a
small file (data/reviewed_title_fills.json) and the pipeline - which
already commits data/ on every run - applies them to the store. Future
reviewed batches only ever touch the small file.

Safety rules:
- The whole step is fail-safe: a missing or malformed fills file is a
  logged no-op, never a run failure.
- A fill is tied to the exact reviewed evidence: expected_title (and
  expected_url when given) must match the current record, otherwise the
  fill is skipped - a changed source invalidates the old review, the
  same principle as the source_review carry-forward in storage.py.
- Existing non-empty values are never overwritten by a different fill
  value; a conflict is logged and skipped for re-review. A fill that
  carries "overwrite": true is that re-review's resolution: the reviewer
  has seen the conflict and the reviewed value replaces the existing
  one, logged as an overwrite.
- Only reviewed manual fills belong in the file. Unreviewed model
  output stays unpublished (pipeline policy 2026-11-03).
"""
from __future__ import annotations

import json
import logging
import os

log = logging.getLogger(__name__)

FILLS_FILE = "reviewed_title_fills.json"
TITLE_FIELDS = ("title_en", "title_ar")


def _load_fills(path: str) -> list:
    if not os.path.isfile(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as fh:
            payload = json.load(fh)
    except (ValueError, OSError) as exc:
        log.warning("reviewed fills: ignoring unreadable %s: %s", path, exc)
        return []
    fills = payload.get("fills") if isinstance(payload, dict) else None
    if not isinstance(fills, list):
        log.warning("reviewed fills: %s has no 'fills' list - ignoring", path)
        return []
    return [f for f in fills if isinstance(f, dict) and f.get("uid")]


def apply_reviewed_fills(store, path: str = None) -> dict:
    """Apply reviewed fills to a JobStore's in-memory store.

    Runs after merge/prune and before save/export/digest. Idempotent:
    re-applying an already-applied fill is a no-op. Returns counters.
    """
    path = path or os.path.join(store.data_dir, FILLS_FILE)
    fills = _load_fills(path)
    counts = {"applied": 0, "noop": 0, "absent": 0, "mismatch": 0,
              "conflict": 0, "overwritten": 0}
    if not fills:
        return counts
    records = getattr(store, "_store", None)
    if records is None:
        records = store.load()
        store._store = records
    for fill in fills:
        uid = fill["uid"]
        record = records.get(uid)
        if record is None:
            counts["absent"] += 1
            log.info("reviewed fill %s: record not in store - skipped", uid)
            continue
        expected_title = fill.get("expected_title")
        if expected_title is not None and (record.get("title") or "") != expected_title:
            counts["mismatch"] += 1
            log.warning("reviewed fill %s: title changed since review - skipped", uid)
            continue
        expected_url = fill.get("expected_url")
        if expected_url is not None and (record.get("url") or "") != expected_url:
            counts["mismatch"] += 1
            log.warning("reviewed fill %s: url changed since review - skipped", uid)
            continue
        changed = False
        overwrite = fill.get("overwrite") is True
        for field in TITLE_FIELDS:
            value = (fill.get(field) or "").strip()
            if not value:
                continue
            current = (record.get(field) or "").strip()
            if not current:
                record[field] = value
                changed = True
            elif current != value:
                if overwrite:
                    record[field] = value
                    changed = True
                    counts["overwritten"] += 1
                    log.info("reviewed fill %s: reviewed overwrite replaced %s",
                             uid, field)
                else:
                    counts["conflict"] += 1
                    log.warning("reviewed fill %s: existing %s differs - kept existing", uid, field)
        source_review = fill.get("source_review")
        if isinstance(source_review, dict) and source_review:
            if record.get("source_review") != source_review:
                record["source_review"] = source_review
                changed = True
            if (source_review.get("status") == "destination_mismatch"
                    and (record.get("summary_ar") or "").strip()):
                # The flag withholds body-derived claims in the UI; the
                # stored summary is itself such a claim from the pre-drift
                # body, so drop it here - the CSV export and any other
                # consumer must not keep presenting it. Re-derivable by a
                # fresh review, so the deletion is bounded.
                record["summary_ar"] = ""
                changed = True
                log.info("reviewed fill %s: cleared stale summary_ar under destination_mismatch", uid)
        if fill.get("translated") and (
                (record.get("title_en") or "").strip()
                or (record.get("title_ar") or "").strip()):
            if record.get("translated") is not True:
                record["translated"] = True
                changed = True
        counts["applied" if changed else "noop"] += 1
    if counts["applied"]:
        log.info("reviewed fills applied: %d (noop=%d absent=%d mismatch=%d conflict=%d overwritten=%d)",
                 counts["applied"], counts["noop"], counts["absent"],
                 counts["mismatch"], counts["conflict"], counts["overwritten"])
    return counts
