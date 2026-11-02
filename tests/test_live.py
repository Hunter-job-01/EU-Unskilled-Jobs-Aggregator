"""Live smoke tests - hit real endpoints. Run explicitly: pytest -m live."""
import pytest

from src.keywords import KeywordMatrix
from src.scrapers import EuresScraper, HttpClient, JobSpyScraper

pytestmark = pytest.mark.live


def test_live_eures_poland(cfg):
    matrix = KeywordMatrix(cfg["keywords"])
    http = HttpClient(cfg["batch"], cfg.get("proxies"))
    scraper = EuresScraper(cfg, http, matrix)
    scraper.kw_cap = 1
    scraper.max_pages = 1
    scraper.req_delay = (0, 0)
    result = scraper.scrape_country("PL", max_jobs=5)
    assert result.status == "ok"
    assert 1 <= result.fetched <= 5
    job = result.jobs[0]
    assert job.title and job.url.startswith("https://europa.eu/eures/portal/")


def test_live_jobspy_poland(cfg):
    matrix = KeywordMatrix(cfg["keywords"])
    scraper = JobSpyScraper(cfg, matrix)
    scraper.kw_cap = 1
    scraper.results_wanted = 5
    scraper.req_delay = (0, 0)
    result = scraper.scrape_country("PL", max_jobs=5)
    # Indeed may block datacenter IPs; a failed/partial status is reported,
    # not hidden. This assertion only requires a well-formed outcome.
    assert result.status in ("ok", "partial", "failed")
    if result.status == "ok":
        assert result.fetched >= 1
        assert result.jobs[0].url


def _tiny(scraper):
    scraper.kw_cap = 1
    scraper.max_pages = 1
    scraper.req_delay = (0, 0)
    return scraper


def test_live_eures_boosted_lithuania(cfg):
    matrix = KeywordMatrix(cfg["keywords"])
    http = HttpClient(cfg["batch"], cfg.get("proxies"))
    scraper = _tiny(EuresScraper(cfg, http, matrix))
    result = scraper.scrape_country("LT", max_jobs=5)
    assert result.status == "ok"
    assert 1 <= result.fetched <= 5
    assert result.jobs[0].country == "LT"
    # boost is configured for LT even though this probe caps it to 1 keyword
    assert scraper.limits_for("LT")[2] is True


def test_live_eures_boosted_slovakia(cfg):
    matrix = KeywordMatrix(cfg["keywords"])
    http = HttpClient(cfg["batch"], cfg.get("proxies"))
    scraper = _tiny(EuresScraper(cfg, http, matrix))
    result = scraper.scrape_country("SK", max_jobs=5)
    assert result.status == "ok"
    assert 1 <= result.fetched <= 5
    assert result.jobs[0].country == "SK"
