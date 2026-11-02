"""HOBIJ adapter tests against a recorded real vacancy page
(tests/fixtures/hobij_vacancy.html, captured 2026-11-03 from the
agency's own public page) and a synthetic vacancy sitemap."""
import os

from src.scrapers.base import ScraperError
from src.scrapers.hobij import HobijScraper

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "hobij_vacancy.html")
PAGE_URL = "https://hobij.com/nl/vacatures/2148-logistiek-medewerker/"
SITEMAP = ('<?xml version="1.0"?><urlset>'
           "<url><loc>{}</loc></url>"
           "<url><loc>https://hobij.com/nl/vacatures/5152-heftruck-chauffeur/</loc></url>"
           "</urlset>").format(PAGE_URL)


class FakeResp:
    def __init__(self, text):
        self.text = text


class FakeHttp:
    def __init__(self, fail_pages=False):
        self.page = open(FIXTURE, encoding="utf-8").read()
        self.fail_pages = fail_pages

    def get(self, url, params=None):
        if "sitemap" in url:
            return FakeResp(SITEMAP)
        if self.fail_pages:
            raise ScraperError("boom")
        return FakeResp(self.page)


def make(cfg, **kw):
    sc = HobijScraper(cfg, FakeHttp(**kw))
    sc.enabled = True
    sc.req_delay = (0, 0)
    return sc


def test_parses_recorded_page(cfg):
    sc = make(cfg)
    job = sc._parse_page(PAGE_URL, open(FIXTURE, encoding="utf-8").read())
    assert job.title == "Logistiek medewerker / Heftruckchauffeur"
    assert job.country == "NL"                    # source-level
    assert job.location == "Den Dungen"
    assert job.company == "Hobij"
    assert "16.24" in job.description


def test_scrape_and_country_gate(cfg):
    sc = make(cfg)
    res = sc.scrape_country("NL", max_jobs=5)
    assert res.status == "ok"
    assert res.fetched == 2
    assert res.stats.get("country_assumed_from_source") == "NL"
    res_pl = make(cfg).scrape_country("PL", max_jobs=5)
    assert res_pl.status == "skipped"


def test_disabled_state_skips(cfg):
    sc = HobijScraper(cfg, FakeHttp())
    sc.enabled = False
    assert sc.scrape_country("NL", max_jobs=5).status == "skipped"


def test_page_failure_reported(cfg):
    sc = make(cfg, fail_pages=True)
    res = sc.scrape_country("NL", max_jobs=5)
    assert res.status == "failed"
    assert res.errors
