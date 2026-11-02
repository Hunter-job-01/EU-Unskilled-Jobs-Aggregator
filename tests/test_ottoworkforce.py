"""OTTO Work Force adapter tests against a recorded real vacancy page
(tests/fixtures/otto_vacancy.html, captured 2026-11-02 from
ottoworkforce.eu/en/jobs/2152-warehouse-employee-undef) and a synthetic
sitemap (no real destination ids beyond the public source itself)."""
import os

import pytest

from src.scrapers.base import ScraperError
from src.scrapers.ottoworkforce import OttoWorkforceScraper

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "otto_vacancy.html")
PAGE_URL = "https://ottoworkforce.eu/en/jobs/2152-warehouse-employee-undef"
SITEMAP = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
    "<url><loc>{}</loc></url>"
    "<url><loc>https://ottoworkforce.eu/en/jobs/9999-night-shift-picker</loc></url>"
    "</urlset>"
).format(PAGE_URL)


class FakeResp:
    def __init__(self, text):
        self.text = text


class FakeHttp:
    def __init__(self, fail_sitemap=False, fail_pages=False):
        self.page = open(FIXTURE, encoding="utf-8").read()
        self.fail_sitemap = fail_sitemap
        self.fail_pages = fail_pages
        self.calls = []

    def get(self, url, params=None):
        self.calls.append(url)
        if "sitemap" in url:
            if self.fail_sitemap:
                raise ScraperError("sitemap boom")
            return FakeResp(SITEMAP)
        if self.fail_pages:
            raise ScraperError("page boom")
        return FakeResp(self.page)


def make(cfg, **kw):
    sc = OttoWorkforceScraper(cfg, FakeHttp(**kw))
    sc.enabled = True
    sc.req_delay = (0, 0)
    return sc


def test_parses_recorded_page(cfg):
    sc = make(cfg)
    res = sc.scrape_country("NL", max_jobs=10)
    assert res.status == "ok"
    assert len(res.jobs) == 2
    job = res.jobs[0]
    assert job.title == "Warehouse employee"
    assert job.company == "OTTO Work Force"
    assert job.country == "NL"
    assert job.location == "Echt"
    assert "15.04" in job.description          # salary from attrs block
    assert "Employment type: Fixed-term" in job.description
    assert job.source_id == "2152-warehouse-employee-undef"


def test_other_country_skipped(cfg):
    sc = make(cfg)
    res = sc.scrape_country("PL", max_jobs=10)
    assert res.status == "skipped"
    assert res.jobs == []
    assert "NL" in res.notes[0]


def test_disabled_state_skips(cfg):
    sc = OttoWorkforceScraper(cfg, FakeHttp())
    sc.enabled = False
    res = sc.scrape_country("NL", max_jobs=10)
    assert res.status == "skipped"
    assert "disabled" in res.notes[0]


def test_sitemap_failure_reported(cfg):
    sc = make(cfg, fail_sitemap=True)
    res = sc.scrape_country("NL", max_jobs=10)
    assert res.status == "failed"
    assert res.errors


def test_page_failure_partial(cfg):
    sc = make(cfg, fail_pages=True)
    res = sc.scrape_country("NL", max_jobs=10)
    assert res.status == "failed"
    assert len(res.errors) == 2


def test_country_assumption_recorded(cfg):
    sc = make(cfg)
    res = sc.scrape_country("NL", max_jobs=10)
    assert res.stats.get("country_assumed_from_source") == "NL"
    assert res.stats.get("sitemap_urls") == 2
