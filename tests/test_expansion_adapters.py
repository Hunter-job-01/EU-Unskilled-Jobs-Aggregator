"""Offline tests for the disabled-by-default expansion adapters."""
import copy
import json

import pytest

from src.scrapers import (ArbeitnowScraper, AtsPublicScraper, CzechMpsvScraper,
                          LatviaNvaScraper, SwedenJobSearchScraper)
from src.scrapers.base import ScraperError


class FakeResp:
    def __init__(self, payload=None, content=b""):
        self.payload = payload
        self.content = content

    def json(self):
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


class FakeHttp:
    def __init__(self, payloads=None, content=b"", fail=False):
        self.payloads = list(payloads or [])
        self.content = content
        self.fail = fail
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append(url)
        if self.fail:
            raise ScraperError("boom")
        if self.payloads:
            return FakeResp(payload=self.payloads.pop(0), content=self.content)
        return FakeResp(payload={}, content=self.content)


def enable(cfg, name, **extra):
    cfg = copy.deepcopy(cfg)
    cfg.setdefault("sources", {})[name] = {"enabled": True}
    cfg["sources"][name].update(extra)
    return cfg


# ----------------------------- arbeitnow ------------------------------
ARBEITNOW_PAGE = {"data": [
    {"slug": "packer-berlin", "company_name": "Logi GmbH", "title": "Packer",
     "description": "<p>Warehouse</p>", "remote": False,
     "url": "https://www.arbeitnow.com/jobs/packer-berlin",
     "location": "Berlin, Germany", "created_at": 1762000000},
    {"slug": "dev-london", "company_name": "Tech Ltd", "title": "Developer",
     "description": "x", "remote": False, "url": "https://x/2",
     "location": "London, United Kingdom", "created_at": 1762000000},
    {"slug": "remote-eu", "company_name": "Anywhere BV", "title": "Helper",
     "description": "x", "remote": True, "url": "https://x/3",
     "location": "Remote", "created_at": 1762000000},
]}


def test_arbeitnow_disabled_by_default(cfg):
    scraper = ArbeitnowScraper(cfg, FakeHttp([ARBEITNOW_PAGE]))
    result = scraper.scrape_country("DE", 10)
    assert result.status == "skipped"
    assert any("disabled" in n for n in result.notes)


def test_arbeitnow_eu_filtering(cfg):
    cfg = enable(cfg, "arbeitnow", max_pages=2)
    scraper = ArbeitnowScraper(cfg, FakeHttp([ARBEITNOW_PAGE, {"data": []}]))
    scraper.req_delay = (0, 0)
    result = scraper.scrape_country("DE", 10)
    assert result.status == "ok"
    assert [j.title for j in result.jobs] == ["Packer"]
    assert result.jobs[0].country == "DE"
    assert result.stats["non_eu_rejects"] == 1      # London
    assert result.stats["quarantined"] == 1         # unconstrained remote
    # second country reuses the fetched rows (one bounded page sequence)
    result_fr = scraper.scrape_country("FR", 10)
    assert any("reused" in n for n in result_fr.notes)
    assert len(scraper.http.calls) == 2  # page 1 + one empty page, then reused


def test_arbeitnow_schema_drift_is_an_error(cfg):
    cfg = enable(cfg, "arbeitnow", max_pages=1)
    scraper = ArbeitnowScraper(cfg, FakeHttp([{"unexpected": True}]))
    scraper.req_delay = (0, 0)
    result = scraper.scrape_country("DE", 10)
    assert result.status == "failed" and result.errors


# -------------------------- sweden_jobsearch --------------------------
SWEDEN_HITS = {"hits": [
    {"id": "1", "headline": "Lagerarbetare", "employer": {"name": "Lager AB"},
     "workplace_address": {"municipality": "Stockholm", "region": "Stockholm",
                           "country_code": "SWE"},
     "description": {"text": "<p>Lagerarbete</p>"},
     "publication_date": "2026-10-30T10:00:00",
     "webpage_url": "https://arbetsformedlingen.se/platsbanken/annonser/1"},
]}


def test_sweden_disabled_by_default(cfg):
    scraper = SwedenJobSearchScraper(cfg, FakeHttp([SWEDEN_HITS]), None)
    assert scraper.scrape_country("SE", 10).status == "skipped"


def test_sweden_only_covers_se(cfg):
    cfg = enable(cfg, "sweden_jobsearch")
    scraper = SwedenJobSearchScraper(cfg, FakeHttp([SWEDEN_HITS]), None)
    result = scraper.scrape_country("PL", 10)
    assert result.status == "skipped"


def test_sweden_maps_hits(cfg, matrix):
    cfg = enable(cfg, "sweden_jobsearch")
    scraper = SwedenJobSearchScraper(cfg, FakeHttp([SWEDEN_HITS] * 20), matrix)
    scraper.req_delay = (0, 0)
    result = scraper.scrape_country("SE", 5)
    assert result.fetched == 1  # same id deduped across keywords
    job = result.jobs[0]
    assert job.country == "SE" and job.source == "sweden_jobsearch"
    assert job.title == "Lagerarbetare" and "<p>" not in job.description


def test_sweden_non_swedish_rows_rejected(cfg, matrix):
    bad = {"hits": [{"id": "9", "headline": "Helfer",
                     "employer": {"name": "X"},
                     "workplace_address": {"municipality": "Oslo",
                                           "country_code": "NOR"},
                     "description": {"text": "x"},
                     "webpage_url": "https://x/9"}]}
    cfg = enable(cfg, "sweden_jobsearch")
    scraper = SwedenJobSearchScraper(cfg, FakeHttp([bad] * 20), matrix)
    scraper.req_delay = (0, 0)
    result = scraper.scrape_country("SE", 5)
    assert result.jobs == []
    assert result.stats["non_eu_rejects"] >= 1


# ----------------------------- czech_mpsv -----------------------------
MPSV_PAYLOAD = {"polozky": [
    {"portalId": 1, "id": "VolneMisto/1", "referencniCislo": "R1",
     "cizinecMimoEu": True, "zamestnaneckaKarta": False, "azylant": False,
     "datumVlozeni": "2026-10-31T00:00:00.000Z",
     "pozadovanaProfese": {"cs": "Skladnik"},
     "zamestnavatel": {"ico": "1", "nazev": "Sklad sro"},
     "urlAdresa": "https://www.uradprace.cz/v/R1",
     "mistoVykonuPrace": {"adresaText": "Praha 4, Libusska 12"},
     "mesicniMzdaOd": 23000, "mesicniMzdaDo": None},
    {"portalId": 2, "id": "VolneMisto/2", "referencniCislo": "R2",
     "datumVlozeni": "2026-10-31T00:00:00.000Z",
     "pozadovanaProfese": {"cs": "Skryta firma"},
     "zamestnavatel": None, "urlAdresa": None,
     "mistoVykonuPrace": {"adresaText": None, "pracoviste": []}},
]}


class StreamResp:
    def __init__(self, payload):
        self.payload = payload

    def iter_content(self, chunk_size=65536, decode_unicode=True):
        text = json.dumps(self.payload, ensure_ascii=False)
        for i in range(0, len(text), 997):
            yield text[i:i + 997]


class FakeHttpStream:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append(url)
        return StreamResp(self.payload)


def test_czech_disabled_by_default(cfg):
    assert CzechMpsvScraper(cfg, FakeHttpStream(MPSV_PAYLOAD)).scrape_country("CZ", 10).status == "skipped"


def test_czech_only_covers_cz(cfg):
    cfg = enable(cfg, "czech_mpsv")
    r = CzechMpsvScraper(cfg, FakeHttpStream(MPSV_PAYLOAD)).scrape_country("PL", 10)
    assert r.status == "skipped"


def test_czech_maps_and_quarantines_hidden_employer(cfg):
    cfg = enable(cfg, "czech_mpsv")
    scraper = CzechMpsvScraper(cfg, FakeHttpStream(MPSV_PAYLOAD))
    result = scraper.scrape_country("CZ", 10)
    assert [j.title for j in result.jobs] == ["Skladnik"]
    job = result.jobs[0]
    assert job.country == "CZ" and job.company == "Sklad sro"
    assert "cizinecMimoEu=True" in job.description  # non-EU consent evidence kept
    assert job.posted_date == "2026-10-31"
    assert result.stats["quarantined"] == 1  # hidden employer never invented


def test_czech_schema_drift_fails_visibly(cfg):
    cfg = enable(cfg, "czech_mpsv")
    scraper = CzechMpsvScraper(cfg, FakeHttpStream({"polozky": [{"x": 1}, {"y": 2}]}))
    result = scraper.scrape_country("CZ", 10)
    assert result.status == "failed"
    assert any("schema" in e for e in result.errors)


# ----------------------------- latvia_nva -----------------------------
NVA_META = {"result": {"resources": [
    {"name": "Vakances", "format": "CSV", "url": "https://data.gov.lv/x/vakances-old.csv",
     "last_modified": "2026-10-01T00:00:00"},
    {"name": "Vakances", "format": "CSV", "url": "https://data.gov.lv/x/vakances-new.csv",
     "last_modified": "2026-11-01T23:02:02"},
]}}
NVA_CSV = ("Vakances_Nr,Aktualizacijas_datums,Vakances_nosaukums,Vakances_kategorija,"
           "Alga_no,Alga_lidz,Vieta,Vakances_paplasinats_apraksts\n"
           "261020-72,2026-10-20,Noliktavas darbinieks,Pakalpojumi,900,1100,"
           "Gaujas iela 33A Adazi,https://cvvp.nva.gov.lv/#/pub/vakances/1\n")


class FakeHttpLv:
    def __init__(self, content, meta=None, fail_meta=False):
        self.content = content
        self.meta = meta if meta is not None else NVA_META
        self.fail_meta = fail_meta
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append(url)
        if "package_show" in url:
            if self.fail_meta:
                raise ScraperError("meta boom")
            return FakeResp(payload=self.meta)
        return FakeResp(content=self.content)


def test_latvia_disabled_by_default(cfg):
    assert LatviaNvaScraper(cfg, FakeHttpLv(b"")).scrape_country("LV", 10).status == "skipped"


def test_latvia_resolves_newest_resource_and_parses(cfg):
    cfg = enable(cfg, "latvia_nva")
    http = FakeHttpLv(NVA_CSV.encode("utf-8"))
    result = LatviaNvaScraper(cfg, http).scrape_country("LV", 10)
    assert http.calls[0].startswith("https://data.gov.lv")
    assert http.calls[1] == "https://data.gov.lv/x/vakances-new.csv"  # newest resource
    assert [j.title for j in result.jobs] == ["Noliktavas darbinieks"]
    job = result.jobs[0]
    assert job.country == "LV" and job.posted_date == "2026-10-20"
    assert "900-1100" in job.description  # salary evidence kept


def test_latvia_stale_feed_is_reported(cfg):
    stale = NVA_CSV.replace("2026-10-20", "2025-01-01")
    cfg = enable(cfg, "latvia_nva")
    result = LatviaNvaScraper(cfg, FakeHttpLv(stale.encode("utf-8"))).scrape_country("LV", 10)
    assert any("stale" in n for n in result.notes)


def test_latvia_metadata_failure_visible(cfg):
    cfg = enable(cfg, "latvia_nva")
    result = LatviaNvaScraper(cfg, FakeHttpLv(b"", fail_meta=True)).scrape_country("LV", 10)
    assert result.status == "failed" and result.errors


# ----------------------------- ats_public -----------------------------
GREENHOUSE = {"jobs": [{"id": 1, "title": "Warehouse Operative",
                        "location": {"name": "Wroclaw, Poland"},
                        "absolute_url": "https://boards.greenhouse.io/acme/jobs/1",
                        "content": "<p>Packing</p>", "updated_at": "2026-10-30T09:00:00"}]}


def _ats_cfg(cfg, tenants):
    return enable(cfg, "ats_public", tenants=tenants)


def test_ats_disabled_by_default(cfg):
    assert AtsPublicScraper(cfg, FakeHttp([GREENHOUSE])).scrape_country("PL", 10).status == "skipped"


def test_ats_no_tenants_is_noop(cfg):
    cfg = _ats_cfg(cfg, [])
    r = AtsPublicScraper(cfg, FakeHttp([GREENHOUSE])).scrape_country("PL", 10)
    assert r.status == "skipped" and r.jobs == []


def test_ats_unenabled_or_unevidenced_tenant_not_queried(cfg):
    cfg = _ats_cfg(cfg, [{"provider": "greenhouse", "tenant": "acme", "country": "PL",
                          "employer": "Acme", "enabled": False,
                          "evidence_url": "https://acme.example/careers"}])
    http = FakeHttp([GREENHOUSE])
    r = AtsPublicScraper(cfg, http).scrape_country("PL", 10)
    assert r.status == "skipped" and http.calls == []
    cfg = _ats_cfg(cfg, [{"provider": "greenhouse", "tenant": "acme", "country": "PL",
                          "employer": "Acme", "enabled": True, "evidence_url": ""}])
    http = FakeHttp([GREENHOUSE])
    r = AtsPublicScraper(cfg, http).scrape_country("PL", 10)
    assert r.jobs == [] and http.calls == []
    assert any("evidence_url" in n for n in r.notes)


def test_ats_greenhouse_maps_public_posting(cfg):
    cfg = _ats_cfg(cfg, [{"provider": "greenhouse", "tenant": "acme", "country": "PL",
                          "employer": "Acme", "enabled": True,
                          "evidence_url": "https://acme.example/careers"}])
    scraper = AtsPublicScraper(cfg, FakeHttp([GREENHOUSE]))
    scraper.req_delay = (0, 0)
    result = scraper.scrape_country("PL", 10)
    assert [j.title for j in result.jobs] == ["Warehouse Operative"]
    assert result.jobs[0].source == "ats_public_greenhouse"
    assert result.jobs[0].country == "PL"


def test_ats_unknown_tenant_country_never_queried(cfg):
    cfg = _ats_cfg(cfg, [{"provider": "greenhouse", "tenant": "evil", "country": "GB",
                          "employer": "X", "enabled": True,
                          "evidence_url": "https://x.example"}])
    http = FakeHttp([GREENHOUSE])
    r = AtsPublicScraper(cfg, http).scrape_country("PL", 10)
    assert r.status == "skipped" and http.calls == []
