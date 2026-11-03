"""AB Midden adapter tests against a recorded real vacancy page
(tests/fixtures/ab_midden_vacancy.html, captured 2026-11-03 from the
agency's own public page) and a synthetic vacancy sitemap."""
import os

from src.scrapers.base import ScraperError
from src.scrapers.ab_midden import AbMiddenScraper

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "ab_midden_vacancy.html")
PAGE_URL = "https://www.abmiddennederland.nl/vacatures/hydrauliek-monteur-hardinxveld-giessendam-2-359-B/"
SITEMAP = ('<?xml version="1.0"?><urlset>'
           "<url><loc>{}</loc></url>"
           "<url><loc>https://www.abmiddennederland.nl/vacatures/vakman-gww-noordeloos-2-504-C/</loc></url>"
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
    sc = AbMiddenScraper(cfg, FakeHttp(**kw))
    sc.enabled = True
    sc.req_delay = (0, 0)
    return sc


def test_parses_recorded_page(cfg):
    sc = make(cfg)
    job = sc._parse_page(PAGE_URL, open(FIXTURE, encoding="utf-8").read())
    assert job.title == "Hydrauliek monteur"
    assert job.country == "NL"                    # from addressCountry
    assert job.location == "Hardinxveld-Giessendam"
    assert job.company == "AB Midden Nederland"
    assert "2610.00" in job.description and "MONTH" in job.description


def test_scrape_and_country_gate(cfg):
    sc = make(cfg)
    res = sc.scrape_country("NL", max_jobs=5)
    assert res.status == "ok"
    assert res.fetched == 2
    assert res.stats.get("country_assumed_from_source") == "NL"
    res_pl = make(cfg).scrape_country("PL", max_jobs=5)
    assert res_pl.status == "skipped"


def test_disabled_state_skips(cfg):
    sc = AbMiddenScraper(cfg, FakeHttp())
    sc.enabled = False
    assert sc.scrape_country("NL", max_jobs=5).status == "skipped"


def test_page_failure_reported(cfg):
    sc = make(cfg, fail_pages=True)
    res = sc.scrape_country("NL", max_jobs=5)
    assert res.status == "failed"
    assert res.errors
