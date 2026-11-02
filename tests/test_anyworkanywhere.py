"""AnyworkAnywhere adapter tests against a recorded real job page
(tests/fixtures/awa_jobpage.html, captured 2026-11-02; the listing's own
public page) and a synthetic sitemap."""
import os

from src.scrapers.base import ScraperError
from src.scrapers.anyworkanywhere import AnyworkAnywhereScraper

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "awa_jobpage.html")
PAGE_URL = "https://www.anyworkanywhere.com/job/dutch-speaking-entry-level-health-sector-customer-service/"
SITEMAP = ('<?xml version="1.0"?><urlset>'
           "<url><loc>{}</loc></url>"
           "<url><loc>https://www.anyworkanywhere.com/fr/job/dutch-speaking-entry-level-health-sector-customer-service-fr/</loc></url>"
           "</urlset>").format(PAGE_URL)


class FakeResp:
    def __init__(self, text):
        self.text = text


class FakeHttp:
    def __init__(self, fail_pages=False):
        self.page = open(FIXTURE, encoding="utf-8").read()
        self.fail_pages = fail_pages
        self.calls = []

    def get(self, url, params=None):
        self.calls.append(url)
        if "sitemap" in url:
            return FakeResp(SITEMAP)
        if self.fail_pages:
            raise ScraperError("boom")
        return FakeResp(self.page)


def make(cfg, **kw):
    sc = AnyworkAnywhereScraper(cfg, FakeHttp(**kw))
    sc.enabled = True
    sc.req_delay = (0, 0)
    return sc


def test_parses_recorded_page(cfg):
    sc = make(cfg)
    job = sc._parse_page(PAGE_URL, open(FIXTURE, encoding="utf-8").read())
    assert job.title == "Dutch-speaking Entry-Level Health Sector Customer Service"
    assert job.country == "PT"                      # from jobLocation.addressCountry
    assert job.company == "City Job Offers"
    assert job.posted_date == "2026-11-02"
    assert len(job.description) > 1000


def test_country_filtering(cfg):
    sc = make(cfg)
    res = sc.scrape_country("NL", max_jobs=5)
    assert res.fetched == 0
    assert res.stats.get("other_country") == 1   # PT page consumed, not NL
    sc2 = make(cfg)
    res_pt = sc2.scrape_country("PT", max_jobs=5)
    assert res_pt.fetched == 1
    # cursor consumed the sitemap: a later call has nothing left
    res_more = sc2.scrape_country("PT", max_jobs=5)
    assert res_more.fetched == 0
    assert res_more.stats.get("urls_consumed") == 1


def test_disabled_state_skips(cfg):
    sc = AnyworkAnywhereScraper(cfg, FakeHttp())
    sc.enabled = False
    res = sc.scrape_country("PT", max_jobs=5)
    assert res.status == "skipped"
    assert "disabled" in res.notes[0]


def test_page_failure_reported(cfg):
    sc = make(cfg, fail_pages=True)
    res = sc.scrape_country("PT", max_jobs=5)
    assert res.status == "failed"
    assert res.errors
