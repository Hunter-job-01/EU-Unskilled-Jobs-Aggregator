"""PL CBOP adapter tests against a recorded real search response
(tests/fixtures/cbop_search.json, captured 2026-11-02 from the public
oferty.praca.gov.pl portal-api; ids are the board's own public hashes)."""
import json
import os

from src.scrapers.base import ScraperError
from src.scrapers.pl_cbop import PlCbopScraper

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "cbop_search.json")


class FakeHttp:
    def __init__(self, fail=False):
        self.payload = json.load(open(FIXTURE, encoding="utf-8"))
        self.fail = fail
        self.calls = []

    def post_json(self, url, payload, params=None):
        self.calls.append((url, payload, params))
        if self.fail:
            raise ScraperError("boom")
        return self.payload


def make(cfg, **kw):
    sc = PlCbopScraper(cfg, FakeHttp(**kw))
    sc.enabled = True
    sc.terms = ["magazynier"]
    sc.req_delay = (0, 0)
    return sc


def test_maps_recorded_offers(cfg):
    sc = make(cfg)
    res = sc.scrape_country("PL", max_jobs=10)
    assert res.status == "ok"
    assert res.fetched == 5
    job = res.jobs[1]
    assert job.title == "Kierowca - Magazynier (kat. B) K/M"
    assert "DIS-PACK" in job.company
    assert job.country == "PL"
    assert job.location == "Morawica, małopolskie"
    assert job.url.endswith(job.source_id)
    # OHP-routed row is labelled, not presented as a direct employer
    first = res.jobs[0]
    assert "OHP" in first.description or "OHP" in first.company


def test_search_request_shape(cfg):
    sc = make(cfg)
    sc.scrape_country("PL", max_jobs=1)
    url, payload, params = sc.http.calls[0]
    assert payload == {"stanowiska": ["magazynier"], "kodJezyka": "PL"}
    assert params["sort"] == "stanowisko,asc"
    assert "oferta/wyszukiwanie" in url


def test_non_pl_skipped(cfg):
    sc = make(cfg)
    res = sc.scrape_country("DE", max_jobs=10)
    assert res.status == "skipped"
    assert res.jobs == []


def test_disabled_state_skips(cfg):
    sc = PlCbopScraper(cfg, FakeHttp())
    sc.enabled = False
    res = sc.scrape_country("PL", max_jobs=10)
    assert res.status == "skipped"
    assert "disabled" in res.notes[0]


def test_api_failure_reported(cfg):
    sc = make(cfg, fail=True)
    res = sc.scrape_country("PL", max_jobs=10)
    assert res.status == "failed"
    assert res.errors
