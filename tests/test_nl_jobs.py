"""NL-Jobs adapter tests against a recorded real vacancy page
(tests/fixtures/nljobs_vacancy.html, captured 2026-11-03 from the
agency's own public page) and a synthetic sitemap."""
import os

from src.scrapers.base import ScraperError
from src.scrapers.nl_jobs import NlJobsScraper

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "nljobs_vacancy.html")
PAGE_URL = "https://www.nl-jobs.com/jobs/production-worker-horticulture-full-time-amsterdam"
SITEMAP = ('<?xml version="1.0"?><urlset>'
           "<url><loc>{}</loc></url>"
           "<url><loc>https://www.nl-jobs.com/service/algemene-voorwaarden</loc></url>"
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
    sc = NlJobsScraper(cfg, FakeHttp(**kw))
    sc.enabled = True
    sc.req_delay = (0, 0)
    return sc


def test_parses_recorded_page(cfg):
    sc = make(cfg)
    job = sc._parse_page(PAGE_URL, open(FIXTURE, encoding="utf-8").read())
    assert job.title == "Production worker (horticulture)"
    assert job.country == "NL"
    assert job.company == "NL-Jobs.com"
    assert job.posted_date == "2026-11-02"
    assert "14.99" in job.description
    assert "employmentType: seasonal" in job.description


def test_scrape_nl_and_other_country(cfg):
    sc = make(cfg)
    res = sc.scrape_country("NL", max_jobs=5)
    assert res.status == "ok"
    assert res.fetched == 1
    sc2 = make(cfg)
    res_de = sc2.scrape_country("DE", max_jobs=5)
    assert res_de.fetched == 0
    assert res_de.stats.get("other_country") == 1


def test_disabled_state_skips(cfg):
    sc = NlJobsScraper(cfg, FakeHttp())
    sc.enabled = False
    res = sc.scrape_country("NL", max_jobs=5)
    assert res.status == "skipped"


def test_page_failure_reported(cfg):
    sc = make(cfg, fail_pages=True)
    res = sc.scrape_country("NL", max_jobs=5)
    assert res.status == "failed"
    assert res.errors
