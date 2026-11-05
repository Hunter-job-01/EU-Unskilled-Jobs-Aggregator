"""v6 lifecycle/dead-link tracking + EURES delta early-stop. Self-contained."""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.storage import JobStore  # noqa: E402
from src.lifecycle import checkable_url, select_candidates  # noqa: E402


def _store(tmp_path, records):
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    store = JobStore({"data_dir": str(tmp_path / "data"),
                      "retention_days": 30}, base_dir=".")
    store._store = dict(records)
    return store


def _rec(uid, source="covebo", country="NL", score=70, url="https://x.io/1"):
    return {"uid": uid, "source": source, "country": country,
            "title": "Warehouse worker", "url": url, "score": score,
            "posted_date": "2026-11-01", "first_seen": "2026-11-01",
            "last_seen": "2026-11-01", "excluded": False}


def test_miss_only_counts_when_pair_covered(tmp_path):
    rec = _rec("a", source="covebo", country="NL")
    store = _store(tmp_path, {"a": rec})
    # Source failed this run: pair not covered -> no miss accrues.
    counts = store.update_lifecycle(seen_uids=set(), covered_pairs=set())
    assert counts["missed"] == 0
    assert store._store["a"]["lifecycle"].get("missed_runs", 0) == 0
    # Covered run without the record -> one miss, still active.
    counts = store.update_lifecycle(set(), {("covebo", "NL")})
    assert counts["missed"] == 1
    assert store._store["a"]["lifecycle"]["status"] == "active"


def test_deactivation_after_consecutive_covered_misses(tmp_path):
    store = _store(tmp_path, {"a": _rec("a")})
    store.update_lifecycle(set(), {("covebo", "NL")})
    counts = store.update_lifecycle(set(), {("covebo", "NL")}, miss_threshold=2)
    assert counts["deactivated"] == 1
    lc = store._store["a"]["lifecycle"]
    assert lc["status"] == "deactivated"
    assert lc["deactivation_reason"] == "not_seen_in_2_covered_runs"
    assert lc["deactivated_at"]


def test_reactivation_on_rescrape(tmp_path):
    store = _store(tmp_path, {"a": _rec("a")})
    for _ in range(2):
        store.update_lifecycle(set(), {("covebo", "NL")}, miss_threshold=2)
    assert store._store["a"]["lifecycle"]["status"] == "deactivated"
    counts = store.update_lifecycle({"a"}, set(), miss_threshold=2)
    assert counts["reactivated"] == 1
    lc = store._store["a"]["lifecycle"]
    assert lc["status"] == "active"
    assert lc["missed_runs"] == 0
    assert lc["reactivations"] == 1
    assert lc["deactivated_at"] is None


def test_source_prefix_matching(tmp_path):
    # jobspy records carry jobspy_indeed; the scraper's name is jobspy.
    store = _store(tmp_path, {"a": _rec("a", source="jobspy_indeed", country="PL")})
    counts = store.update_lifecycle(set(), {("jobspy", "PL")})
    assert counts["missed"] == 1


def test_dead_link_deactivation_and_live_reset(tmp_path):
    store = _store(tmp_path, {"a": _rec("a")})
    store.update_lifecycle({"a"}, set(), dead_uids={"a"})
    assert store._store["a"]["lifecycle"]["status"] == "active"
    counts = store.update_lifecycle(set(), set(), dead_uids={"a"}, dead_threshold=2)
    lc = store._store["a"]["lifecycle"]
    assert lc["status"] == "deactivated"
    assert lc["deactivation_reason"] == "dead_link_confirmed"
    # A live check resets the dead counter.
    store2 = _store(tmp_path / "b", {"a": _rec("a")})
    store2.update_lifecycle({"a"}, set(), dead_uids={"a"})
    store2.update_lifecycle({"a"}, set(), live_uids={"a"})
    assert store2._store["a"]["lifecycle"]["dead_checks"] == 0


def test_merge_preserves_lifecycle(tmp_path):
    from src.models import Job
    store = _store(tmp_path, {})
    job = Job(title="Warehouse worker", company="X", country="NL",
              source="covebo", url="https://x.io/1", uid="u1",
              posted_date="2026-11-04")
    store.merge([job])
    store._store["u1"]["lifecycle"] = {"status": "deactivated",
                                       "missed_runs": 3}
    store.merge([job])  # re-scrape carries lifecycle={}
    assert store._store["u1"]["lifecycle"]["status"] == "deactivated"


def test_checkable_url():
    assert checkable_url("https://europa.eu/eures/portal/jv-se/jv-details/ABC123?lang=en")
    assert checkable_url("https://covebo.nl/vacatures/warehouse/")
    assert not checkable_url("https://pl.indeed.com/viewjob?jk=abc")
    assert not checkable_url("https://www.indeed.com/viewjob?jk=abc")
    assert not checkable_url("")
    assert not checkable_url("not-a-url")


def test_select_candidates_skips_seen_and_deactivated():
    records = {
        "a": _rec("a", score=90),
        "b": _rec("b", score=50),
        "c": dict(_rec("c"), lifecycle={"status": "deactivated"}),
        "d": _rec("d", url="https://pl.indeed.com/viewjob?jk=x"),
    }
    picked = select_candidates(records, seen_uids={"a"}, limit=10)
    uids = [r["uid"] for r in picked]
    assert uids == ["b"]  # a seen, c deactivated, d uncheckable


def test_eures_no_early_stop_on_known_pages(monkeypatch):
    """2026-11-05 live probe: EURES MOST_RECENT is NOT creationDate-ordered,
    so a fully-known page proves nothing about deeper pages. Pagination
    must continue to max_pages even when every id on a page is stored."""
    from src.scrapers.eures_scraper import EuresScraper

    class FakeHttp:
        def __init__(self):
            self.calls = 0

        def post_json(self, url, payload):
            self.calls += 1
            return {"jvs": [{"id": "K1"}, {"id": "K2"}],
                    "numberRecords": 100}

    cfg = {"batch": {"results_per_page": 2, "max_pages_per_keyword": 5,
                     "keywords_per_country": 1, "delay_between_requests_sec": [0, 0]},
           "countries": {"NL": {"languages": ["en"], "eures_location_code": "NL"}}}
    matrix = type("M", (), {"search_keywords": lambda self, langs, cap: ["warehouse"]})()
    scraper = EuresScraper(cfg, FakeHttp(), matrix)
    scraper.known_ids = {"K1", "K2"}
    result = scraper.scrape_country("NL", max_jobs=50)
    assert scraper.http.calls == 5  # all pages fetched despite fully-known pages
    assert not result.stats.get("early_stopped")
