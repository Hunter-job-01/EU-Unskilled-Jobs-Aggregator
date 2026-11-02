"""Poland CBOP (Centralna Baza Ofert Pracy) adapter - official PES board.

Endpoint (probe-verified 2026-11-02, session-free):
  POST https://oferty.praca.gov.pl/portal-api/v3/oferta/wyszukiwanie
       ?page=N&size=10&sort=stanowisko,asc
  body {"stanowiska": ["magazynier", ...], "kodJezyka": "PL"}
Response: payload.ofertyPracyPage.content[] with id, stanowisko (title),
pracodawca (employer), miejscePracy (place), dataWaznDo (valid through,
DD.MM.YYYY), sposobAplikowania (application method), rodzajUmowy.
404 total magazynier offers at probe. Search results carry no full
description - the detail step is future work; description here is built
only from returned fields and labelled as such.

Country is PL by construction (national Polish board). Foreign-applicant
eligibility is NOT asserted; OHP-posted rows (pracodawca "kontakt przez
OHP") route through a labour office, noted per row.

Enabled per owner authorization (sources.pl_cbop.enabled).
"""
from __future__ import annotations

import logging

from ..eu_location import EU27
from ..models import Job
from ..keywords import KeywordMatrix
from .base import ScrapeResult, ScraperError, polite_sleep

log = logging.getLogger(__name__)

API_URL = "https://oferty.praca.gov.pl/portal-api/v3/oferta/wyszukiwanie"
DEFAULT_TERMS = ["magazynier", "pakowacz", "pracownik produkcji",
                 "sprzątaczka", "pomoc kuchenna", "pracownik sezonowy"]
_PAGE_URL = "https://oferty.praca.gov.pl/portal/wyszukiwarka-ofert/oferta?id={}"


class PlCbopScraper:
    name = "pl_cbop"

    def __init__(self, cfg: dict, http):
        self.cfg = cfg
        self.http = http
        sc = (cfg.get("sources") or {}).get("pl_cbop") or {}
        self.enabled = bool(sc.get("enabled", False))
        self.api_url = str(sc.get("api_url") or API_URL)
        self.terms = list(sc.get("search_terms") or DEFAULT_TERMS)
        self.max_pages_per_term = int(sc.get("max_pages_per_term", 1))
        self.page_size = int(sc.get("page_size", 10))
        self.req_delay = tuple(sc.get("delay_between_requests_sec",
                                      cfg["batch"].get("delay_between_requests_sec", [1.5, 4.0])))
        self.early_filter = (
            KeywordMatrix(cfg["keywords"])
            if (cfg.get("scoring") or {}).get("early_title_filter", True)
            else None
        )
        self._offers = None  # offers shared across country queries

    def _fetch_offers(self, result: ScrapeResult) -> list:
        offers, seen = [], set()
        for term in self.terms:
            for page in range(self.max_pages_per_term):
                payload = {"stanowiska": [term], "kodJezyka": "PL"}
                params = {"page": page, "size": self.page_size,
                          "sort": "stanowisko,asc"}
                try:
                    data = self.http.post_json(self.api_url, payload, params=params)
                except ScraperError as exc:
                    result.errors.append("term {!r} page {}: {}".format(term, page, exc))
                    break
                except (ValueError, AttributeError) as exc:
                    result.errors.append("term {!r} page {}: unparsable: {}".format(term, page, exc))
                    break
                content = (((data or {}).get("payload") or {})
                           .get("ofertyPracyPage") or {}).get("content")
                if not isinstance(content, list):
                    result.errors.append("term {!r} page {}: unexpected schema".format(term, page))
                    break
                for row in content:
                    oid = str((row or {}).get("id") or "")
                    if oid and oid not in seen:
                        seen.add(oid)
                        offers.append(row)
                if not content:
                    break
                polite_sleep(self.req_delay)
        result.stats["offers_fetched"] = len(offers)
        return offers

    def _map(self, row: dict) -> Job:
        title = str(row.get("stanowisko") or "").strip()
        employer = str(row.get("pracodawca") or "").strip()
        place = str(row.get("miejscePracy") or "").strip()
        oid = str(row.get("id") or "").strip()
        via_ohp = employer.lower() == "kontakt przez ohp"
        parts = []
        if row.get("rodzajUmowy"):
            parts.append("Umowa: {}".format(row["rodzajUmowy"]))
        if row.get("dataWaznDo"):
            parts.append("Oferta ważna do: {}".format(row["dataWaznDo"]))
        if via_ohp:
            parts.append("Aplikacja przez urząd pracy (OHP), nie bezpośrednio do pracodawcy")
        return Job(
            title=title,
            company=employer or "CBOP listing",
            country="PL",
            source=self.name,
            source_id=oid,
            url=_PAGE_URL.format(oid),
            description="[search-result fields only] " + "; ".join(parts),
            location=place,
            posted_date=None,  # dataDodaniaCbop is DD.MM.YYYY; normalized later
        )

    def scrape_country(self, country_code: str, max_jobs: int) -> ScrapeResult:
        country_code = country_code.upper()
        result = ScrapeResult(source=self.name, country=country_code)
        if not self.enabled:
            result.status = "skipped"
            result.notes.append("disabled in config (sources.pl_cbop.enabled: false)")
            return result
        if country_code != "PL":
            result.status = "skipped"
            result.notes.append("CBOP is the Polish national board (PL only); skipped for {}".format(country_code))
            return result
        if self._offers is None:
            self._offers = self._fetch_offers(result)
        else:
            result.notes.append("reused {} offers fetched earlier this run".format(len(self._offers)))
        for row in self._offers:
            if result.fetched >= max_jobs:
                break
            job = self._map(row)
            if not job.title:
                result.stats["untitled_rows"] = result.stats.get("untitled_rows", 0) + 1
                continue
            if self.early_filter is not None:
                reason = self.early_filter.early_title_exclusion(job.title)
                if reason:
                    result.stats["early_title_excluded"] = result.stats.get("early_title_excluded", 0) + 1
                    continue
            result.jobs.append(job)
        if result.errors:
            result.status = "partial" if result.jobs else "failed"
        return result
