"""Goodmorning adapter tests against a recorded real vacancy page
(tests/fixtures/goodmorning_vacancy.html, captured 2026-11-03 from the
agency's own public page) and a synthetic vacancy sitemap."""
import os

from src.scrapers.base import ScraperError
from src.scrapers.goodmorning import GoodmorningScraper

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "goodmorning_vacancy.html")
PAGE_URL = "https://goodmorning.eu/vacatures/1424-productiemedewerker-afvuloperator/"
SITEMAP = ('<?xml version="1.0"?><urlset>'
           "<url><loc>https://goodmorning.eu/vacatures/</loc></url>"
           "<url><loc>{}</loc></url>"
           "<url><loc>https://goodmorning.eu/en/vacancies/1423-production-employee-filling-operator/</loc></url>"
           "<url><loc>https://goodmorning.eu/vacatures/1422-medewerker-bedrijfsrestaurant/</loc></url>"
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
    sc = GoodmorningScraper(cfg, FakeHttp(**kw))
    sc.enabled = True
    sc.req_delay = (0, 0)
    return sc


def test_sitemap_filters_listing_and_en_locale(cfg):
    sc = make(cfg)
    res = sc.scrape_country("NL", max_jobs=10)
    # listing page (/vacatures/ without id) and /en/ locale urls excluded
    assert res.stats["sitemap_urls"] == 2
    assert res.fetched == 2


def test_parses_recorded_page(cfg):
    sc = make(cfg)
    job = sc._parse_page(PAGE_URL, open(FIXTURE, encoding="utf-8").read())
    assert job.title == "Productiemedewerker – Afvuloperator"
    assert job.country == "NL"                    # source-level
    assert job.location == "Roosendaal"
    assert job.company == "GOODMORNING"
    assert "15.35" in job.description and "HOUR" in job.description


def test_validthrough_2999_sentinel_not_expired(cfg):
    sc = make(cfg)
    job = sc._parse_page(PAGE_URL, open(FIXTURE, encoding="utf-8").read())
    assert job != "expired"  # 2999-12-31 sentinel means no real expiry


def test_scrape_and_country_gate(cfg):
    sc = make(cfg)
    res = sc.scrape_country("NL", max_jobs=5)
    assert res.status == "ok"
    assert res.stats.get("country_assumed_from_source") == "NL"
    assert make(cfg).scrape_country("PL", max_jobs=5).status == "skipped"


def test_disabled_state_skips(cfg):
    sc = GoodmorningScraper(cfg, FakeHttp())
    sc.enabled = False
    assert sc.scrape_country("NL", max_jobs=5).status == "skipped"


def test_page_failure_reported(cfg):
    sc = make(cfg, fail_pages=True)
    res = sc.scrape_country("NL", max_jobs=5)
    assert res.status == "failed"
    assert res.errors
