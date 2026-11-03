import pandas as pd
import pytest

from src.keywords import KeywordMatrix
from src.scrapers import EuresScraper, HttpClient, JobSpyScraper
from src.scrapers.base import ScraperError

EURES_FIXTURE = {
    "numberRecords": 2,
    "jvs": [
        {
            "title": "Magazynier",
            "description": "<p>Zakwaterowanie, zezwolenie na pracę.</p>",
            "id": "MTIzIDE",
            "creationDate": 1762000000000,
            "locationMap": {"PL": ["PL51"]},
            "employer": {"name": "Progres HR"},
            "availableLanguages": ["pl"],
        },
        {
            "title": "Pakowacz",
            "description": "Praca w magazynie.",
            "id": "MTI0IDE",
            "creationDate": 1762000000000,
            "locationMap": {"PL": ["PL91"]},
            "employer": {"name": "Logis"},
            "availableLanguages": ["pl"],
        },
    ],
}


class FakeHttp:
    def __init__(self, payload=None, fail=False):
        self.payload = payload or EURES_FIXTURE
        self.fail = fail
        self.calls = 0

    def post_json(self, url, payload):
        self.calls += 1
        if self.fail:
            raise ScraperError("boom")
        return self.payload


def test_eures_mapping_and_dedup(cfg, matrix):
    http = FakeHttp()
    scraper = EuresScraper(cfg, http, matrix)
    scraper.req_delay = (0, 0)
    result = scraper.scrape_country("PL", max_jobs=10)
    assert result.status == "ok"
    assert result.fetched == 2  # same fixture per keyword, deduped by source id
    job = result.jobs[0]
    assert job.country == "PL" and job.source == "eures"
    assert job.company == "Progres HR"
    assert job.url == "https://europa.eu/eures/portal/jv-se/jv-details/MTIzIDE?lang=en"
    assert job.posted_date and len(job.posted_date) == 10
    assert "<p>" not in job.description


def test_eures_keyword_failure_isolated(cfg, matrix):
    http = FakeHttp(fail=True)
    scraper = EuresScraper(cfg, http, matrix)
    scraper.req_delay = (0, 0)
    result = scraper.scrape_country("PL", max_jobs=5)
    assert result.status == "failed"
    assert result.jobs == []
    assert result.errors  # one error per attempted keyword, no crash


def test_jobspy_unsupported_country_skipped(cfg, matrix):
    scraper = JobSpyScraper(cfg, matrix)
    result = scraper.scrape_country("LT", max_jobs=5)
    assert result.status == "skipped"
    assert result.fetched == 0


def test_jobspy_maps_dataframe(cfg, matrix, monkeypatch):
    frame = pd.DataFrame([
        {"id": "jd-1", "site": "indeed", "job_url": "https://pl.indeed.com/x",
         "job_url_direct": "https://pl.indeed.com/x", "title": "Magazynier",
         "company": "ACME", "location": "Warsaw", "date_posted": "2026-11-01",
         "description": "Zakwaterowanie."},
    ])

    def fake_scrape_jobs(**kwargs):
        return frame

    import jobspy
    monkeypatch.setattr(jobspy, "scrape_jobs", fake_scrape_jobs)
    scraper = JobSpyScraper(cfg, matrix)
    scraper.req_delay = (0, 0)
    result = scraper.scrape_country("PL", max_jobs=5)
    assert result.status == "ok"
    assert result.fetched == 1
    job = result.jobs[0]
    assert job.source == "jobspy_indeed"
    assert job.posted_date == "2026-11-01"
    assert job.url.startswith("https://pl.indeed.com")


def test_jobspy_exception_isolated(cfg, matrix, monkeypatch):
    def boom(**kwargs):
        raise RuntimeError("indeed blocked us")

    import jobspy
    monkeypatch.setattr(jobspy, "scrape_jobs", boom)
    scraper = JobSpyScraper(cfg, matrix)
    scraper.req_delay = (0, 0)
    result = scraper.scrape_country("PL", max_jobs=5)
    assert result.status == "failed"
    assert any("blocked" in e for e in result.errors)


def test_http_client_retries_then_succeeds(cfg):
    class FlakySession:
        def __init__(self):
            self.calls = 0

        def request(self, method, url, **kwargs):
            self.calls += 1
            if self.calls < 2:
                raise ConnectionError("reset")
            return _Resp()

    class _Resp:
        status_code = 200

        def raise_for_status(self):
            return None

    batch = dict(cfg["batch"], backoff_base_sec=0, backoff_max_sec=0)
    client = HttpClient(batch, session=FlakySession())
    resp = client.request("GET", "https://example.test")
    assert resp.status_code == 200


def test_jobspy_linkedin_uses_location_not_country_indeed(cfg, matrix, monkeypatch):
    captured = []

    def fake_scrape_jobs(**kwargs):
        captured.append(kwargs)
        return pd.DataFrame([
            {"id": "li-1", "site": "linkedin", "job_url": "https://www.linkedin.com/jobs/view/1",
             "job_url_direct": None, "title": "Warehouse worker", "company": "X",
             "location": "Poland", "date_posted": "2026-11-01", "description": ""},
        ])

    import jobspy
    monkeypatch.setattr(jobspy, "scrape_jobs", fake_scrape_jobs)
    scraper = JobSpyScraper(cfg, matrix)
    scraper.sites = ["linkedin"]
    scraper.req_delay = (0, 0)
    result = scraper.scrape_country("PL", max_jobs=3)
    assert result.fetched >= 1
    assert result.jobs[0].source == "jobspy_linkedin"
    assert all("location" in c and c.get("country_indeed") is None for c in captured)
    assert all(c["site_name"] == ["linkedin"] for c in captured)


def test_jobspy_indeed_unsupported_but_linkedin_still_runs(cfg, matrix, monkeypatch):
    frame = pd.DataFrame([
        {"id": "li-2", "site": "linkedin", "job_url": "https://www.linkedin.com/jobs/view/2",
         "job_url_direct": None, "title": "Sandėlio darbuotojas", "company": "UAB",
         "location": "Vilnius", "date_posted": "2026-11-01", "description": ""},
    ])
    monkeypatch.setattr("jobspy.scrape_jobs", lambda **kw: frame)
    scraper = JobSpyScraper(cfg, matrix)
    scraper.sites = ["indeed", "linkedin"]
    scraper.req_delay = (0, 0)
    result = scraper.scrape_country("LT", max_jobs=3)  # Indeed-unsupported country
    assert result.status == "ok"
    assert result.fetched == 1
    assert result.jobs[0].source == "jobspy_linkedin"
    assert any("indeed" in n and "not supported" in n for n in result.notes)


def test_jobspy_unknown_sites_rejected(cfg, matrix):
    bad = dict(cfg)
    bad["batch"] = dict(cfg["batch"], jobspy_sites=["monster"])
    with pytest.raises(ValueError):
        JobSpyScraper(bad, matrix)


def test_jobspy_empty_sites_rejected(cfg, matrix):
    bad = dict(cfg)
    bad["batch"] = dict(cfg["batch"], jobspy_sites=[])
    with pytest.raises(ValueError):
        JobSpyScraper(bad, matrix)


def test_main_rejects_unknown_jobspy_sites(cfg, monkeypatch):
    import main as cli
    bad = dict(cfg)
    bad["batch"] = dict(cfg["batch"], jobspy_sites=["monster"])
    monkeypatch.setattr(cli, "load_config", lambda path=None: bad)
    assert cli.main(["--sources", "jobspy", "--countries", "PL"]) == 2


# ---------------------------------------------------------------------
# Fix 4: EURES query expansion for JobSpy-unsupported countries (LT, SK)
# ---------------------------------------------------------------------

class RecordingHttp:
    def __init__(self, jv_id="MTIzIDE"):
        self.calls = []
        self.jv_id = jv_id

    def post_json(self, url, payload):
        self.calls.append(dict(payload))
        # Large total keeps pagination going up to the page cap.
        return {"numberRecords": 100,
                "jvs": [dict(EURES_FIXTURE["jvs"][0], id=self.jv_id)]}


def test_eures_boost_limits(cfg, matrix):
    scraper = EuresScraper(cfg, FakeHttp(), matrix)
    assert scraper.limits_for("LT") == (16, 3, True)
    assert scraper.limits_for("sk") == (16, 3, True)  # case-insensitive
    assert scraper.limits_for("PL") == (8, 2, False)


def test_eures_boosted_country_queries_more_keywords_and_pages(cfg, matrix):
    http_lt = RecordingHttp()
    lt_scraper = EuresScraper(cfg, http_lt, matrix)
    lt_scraper.req_delay = (0, 0)
    lt = lt_scraper.scrape_country("LT", max_jobs=100)

    http_pl = RecordingHttp()
    pl_scraper = EuresScraper(cfg, http_pl, matrix)
    pl_scraper.req_delay = (0, 0)
    pl = pl_scraper.scrape_country("PL", max_jobs=100)

    assert len(http_lt.calls) > len(http_pl.calls)   # wider keyword set
    assert max(c["page"] for c in http_lt.calls) == 3    # deeper pagination
    assert max(c["page"] for c in http_pl.calls) == 2    # default depth
    assert all(c["locationCodes"] == ["lt"] for c in http_lt.calls)
    assert any("boost" in n for n in lt.notes)
    assert not any("boost" in n for n in pl.notes)


def test_eures_boost_unknown_country_warns_not_crash(cfg, matrix, caplog):
    bad = dict(cfg)
    bad["batch"] = dict(cfg["batch"],
                        eures_boost={"countries": ["XX"],
                                     "keywords_per_country": 16,
                                     "max_pages_per_keyword": 3})
    import logging
    with caplog.at_level(logging.WARNING):
        scraper = EuresScraper(bad, FakeHttp(), matrix)
    assert scraper.boost_countries == {"XX"}
    assert any("unknown" in r.message for r in caplog.records)


def test_eures_mapping_tolerates_null_region_codes(cfg, matrix):
    fixture = {"numberRecords": 1, "jvs": [dict(EURES_FIXTURE["jvs"][0],
                                                locationMap={"LT": [None, "LT01"]})]}
    scraper = EuresScraper(cfg, FakeHttp(fixture), matrix)
    scraper.req_delay = (0, 0)
    result = scraper.scrape_country("LT", max_jobs=5)
    assert result.status == "ok"
    assert result.jobs[0].location == "LT01"


def test_jobspy_dataframe_error_is_not_healthy(cfg, matrix, monkeypatch):
    frame = pd.DataFrame()
    frame.attrs = {'errors': {'indeed': 'HTTP 403 forbidden'}, 'warnings': {}}
    calls = []
    def scrape(**kwargs):
        calls.append(kwargs)
        return frame
    monkeypatch.setattr('jobspy.scrape_jobs', scrape)
    scraper = JobSpyScraper(cfg, matrix)
    scraper.req_delay = (0, 0)
    result = scraper.scrape_country('PL', max_jobs=5)
    assert result.status == 'failed'
    assert any('403' in err for err in result.errors)
    assert len(calls) == 1


def test_jobspy_nan_direct_url_falls_back_to_job_url(cfg, matrix):
    scraper = JobSpyScraper(cfg, matrix)
    row = {"id": "in-aa2012504b307e91", "job_url": "https://pl.indeed.com/viewjob?jk=aa2012504b307e91",
           "job_url_direct": float("nan"), "title": "Magazynier", "company": "ACME",
           "location": "Warsaw", "date_posted": "2026-11-01", "description": "x"}
    job = scraper.map_row(row, "PL", "indeed")
    assert job.url == "https://pl.indeed.com/viewjob?jk=aa2012504b307e91"


def test_jobspy_both_urls_nan_rebuilds_canonical_viewjob(cfg, matrix):
    scraper = JobSpyScraper(cfg, matrix)
    row = {"id": "in-aa2012504b307e91", "job_url": float("nan"), "job_url_direct": float("nan"),
           "title": "Magazynier", "company": "ACME", "location": "Warsaw",
           "date_posted": "2026-11-01", "description": "x"}
    job = scraper.map_row(row, "PL", "indeed")
    assert job.url == "https://pl.indeed.com/viewjob?jk=aa2012504b307e91"
    assert job.url != "nan"


def test_jobspy_no_id_no_url_stays_empty_not_nan(cfg, matrix):
    scraper = JobSpyScraper(cfg, matrix)
    row = {"id": float("nan"), "job_url": float("nan"), "job_url_direct": None,
           "title": "Magazynier", "company": "ACME", "location": "Warsaw",
           "date_posted": "2026-11-01", "description": "x"}
    job = scraper.map_row(row, "PL", "indeed")
    assert job.url == ""
    assert job.source_id == ""
