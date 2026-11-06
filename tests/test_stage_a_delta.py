"""v6 stage A: slug-first delta for sitemap-index board scrapers.

Known-fresh slugs (store last_seen inside the recheck window) skip the
detail fetch while the sitemap index is still fully enumerated; skipped
slugs are reported for lifecycle seen-marking. Older records rotate
back in (bounded staleness). anyworkanywhere is deliberately NOT
delta-skipped: its rotating cursor does not enumerate the full index
per run.
"""
import os

from src.scrapers.covebo import CoveboScraper

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "covebo_vacancy.html")
KNOWN_URL = "https://www.covebo.nl/vacature/v-000422-hulpmonteur"
NEW_URL = "https://www.covebo.nl/vacature/v-155601-warehouse-employee-maarssen"
SITEMAP = ('<?xml version="1.0"?><urlset>'
           "<url><loc>{}</loc></url>"
           "<url><loc>{}</loc></url>"
           "</urlset>").format(KNOWN_URL, NEW_URL)


class FakeResp:
    def __init__(self, text):
        self.text = text


class FakeHttp:
    def __init__(self):
        self.page = open(FIXTURE, encoding="utf-8").read()
        self.fetched = []

    def get(self, url, params=None):
        if "sitemap" in url:
            return FakeResp(SITEMAP)
        self.fetched.append(url)
        return FakeResp(self.page)


def make(cfg):
    sc = CoveboScraper(cfg, FakeHttp())
    sc.enabled = True
    sc.req_delay = (0, 0)
    return sc


def test_known_fresh_slug_skips_detail_fetch(cfg):
    sc = make(cfg)
    sc.known_fresh_ids = {"v-000422-hulpmonteur"}
    res = sc.scrape_country("NL", 50)
    # Index fully enumerated and reported; only the new slug fetched.
    assert res.stats["index_count"] == 2
    assert sc.http.fetched == [NEW_URL]
    assert res.stats["delta_skipped"] == 1
    assert res.stats["delta_skipped_ids"] == ["v-000422-hulpmonteur"]
    assert len(res.jobs) == 1


def test_no_known_ids_fetches_everything(cfg):
    sc = make(cfg)
    sc.known_fresh_ids = set()
    res = sc.scrape_country("NL", 50)
    assert len(sc.http.fetched) == 2
    assert res.stats.get("delta_skipped") is None
    assert res.stats["index_count"] == 2


def test_missing_attribute_means_no_delta(cfg):
    sc = make(cfg)
    assert not hasattr(sc, "known_fresh_ids")
    res = sc.scrape_country("NL", 50)
    assert len(sc.http.fetched) == 2
    assert res.stats["index_count"] == 2
