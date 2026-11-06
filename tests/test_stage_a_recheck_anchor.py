"""v6 stage A follow-up: the detail recheck window anchors on
detail_fetched_at, never on last_seen.

Lifecycle seen-marking and dead-link sweeps run every run; a recheck
window keyed on any timestamp they can touch would keep a
delta-skipped posting "fresh" forever, and its stored detail (title,
description, translation) would never be re-validated.
detail_fetched_at is written only by JobStore.merge - when a fresh
detail record was actually fetched - so seen-marking never moves it.
"""
from datetime import date, timedelta

from src.delta import RECHECK_WINDOW_DAYS, known_fresh_ids, pair_covered
from src.models import Job
from src.scrapers.base import ScraperError
from src.scrapers.covebo import CoveboScraper
from src.storage import JobStore


def _rec(source="covebo", source_id="v-1", **kw):
    rec = {"source": source, "source_id": source_id}
    rec.update(kw)
    return rec


def test_window_anchors_on_detail_fetched_at_not_last_seen():
    day0 = date(2026, 11, 1)
    rec = _rec(detail_fetched_at=day0.isoformat(),
               last_seen=day0.isoformat())
    # Seven consecutive runs: seen-marking advances last_seen each run;
    # the posting stays fresh while the DETAIL anchor is in the window.
    for offset in range(1, RECHECK_WINDOW_DAYS + 1):
        today = day0 + timedelta(days=offset)
        rec["last_seen"] = today.isoformat()  # seen-marking each run
        fresh = known_fresh_ids([rec], today=today)
        assert "v-1" in fresh.get("covebo", set()), today
    # Run eight: the anchor is outside the window even though last_seen
    # was refreshed on the previous run - the posting rotates back in
    # for a bounded-staleness detail recheck.
    today = day0 + timedelta(days=RECHECK_WINDOW_DAYS + 1)
    rec["last_seen"] = today.isoformat()
    fresh = known_fresh_ids([rec], today=today)
    assert "v-1" not in fresh.get("covebo", set())


def test_pre_stage_a_records_fall_back_to_last_seen():
    # Records written before the anchor existed: last_seen is accurate
    # for them because every pre-stage-A run merged fresh details for
    # everything it saw.
    today = date(2026, 11, 6)
    recent = _rec(source_id="a", last_seen="2026-11-04")
    old = _rec(source_id="b", last_seen="2026-10-20")
    fresh = known_fresh_ids([recent, old], today=today)
    assert fresh.get("covebo") == {"a"}


def _store(tmp_path):
    cfg = {"data_dir": "data", "active_jobs_file": "active_jobs.json",
           "export_csv": "latest_export.csv",
           "run_status_file": "run_status.json", "retention_days": 30}
    return JobStore(cfg, base_dir=str(tmp_path))


def _job(uid="uid1"):
    return Job(title="Magazynier", company="ACME", country="NL",
               source="covebo", url="https://example.test/1",
               uid=uid, score=70)


def test_merge_stamps_detail_fetched_at(tmp_path):
    store = _store(tmp_path)
    store.merge([_job()], today=date(2026, 11, 1))
    assert store._store["uid1"]["detail_fetched_at"] == "2026-11-01"
    # A fresh detail re-fetch restamps the anchor; first_seen is kept.
    store.merge([_job()], today=date(2026, 11, 5))
    assert store._store["uid1"]["detail_fetched_at"] == "2026-11-05"
    assert store._store["uid1"]["first_seen"] == "2026-11-01"


def test_seen_marking_never_moves_detail_anchor(tmp_path):
    store = _store(tmp_path)
    store.merge([_job()], today=date(2026, 11, 1))
    for offset in range(1, 9):
        store.update_lifecycle({"uid1"}, {("covebo", "NL")},
                               today=date(2026, 11, 1) + timedelta(days=offset))
    # Eight seen-marking cycles later the detail anchor still says when
    # detail was last FETCHED - the record will rotate back into the
    # delta window on schedule.
    assert store._store["uid1"]["detail_fetched_at"] == "2026-11-01"


def test_pair_covered_rules():
    # Sitemap-index sources: a successfully enumerated index is full
    # coverage evidence even when detail fetches are delta-limited.
    assert pair_covered("ok", {"index_count": 0}, 0, 50) is True
    assert pair_covered("partial", {"index_count": 137}, 5, 50) is True
    # A failed index enumeration is no coverage evidence.
    assert pair_covered("ok", {"index_failed": True, "index_count": 0},
                        0, 50) is False
    assert pair_covered("partial", {"index_failed": True}, 0, 50) is False
    # Non-index sources: capped by max_jobs is not coverage.
    assert pair_covered("ok", {}, 50, 50) is False
    assert pair_covered("ok", {}, 12, 50) is True
    # Failed or skipped pairs prove nothing.
    assert pair_covered("error", {"index_count": 10}, 0, 50) is False
    assert pair_covered("skipped", {}, 0, 50) is False


def test_sitemap_failure_marks_index_failed(cfg):
    class _FailHttp:
        def get(self, url, params=None):
            raise ScraperError("boom")

    sc = CoveboScraper(cfg, _FailHttp())
    sc.enabled = True
    sc.req_delay = (0, 0)
    res = sc.scrape_country("NL", 50)
    assert res.stats["index_failed"] is True
    assert res.stats["index_count"] == 0
    assert not res.jobs
