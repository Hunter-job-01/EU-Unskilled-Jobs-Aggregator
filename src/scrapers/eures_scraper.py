"""EURES portal scraper (primary source, covers all 27 EU states).

Uses the public, unauthenticated JSON API behind the EURES portal:
    POST https://europa.eu/eures/api/jv-searchengine/public/jv-search/search
This is the endpoint the official portal's own frontend calls. It is NOT
an officially supported public API: there are no published rate limits or
stability guarantees, so the scraper stays polite (few keywords per
country, randomized delays, limited pagination) and degrades gracefully.
"""
from __future__ import annotations

import html
import logging
import re
import uuid
from datetime import datetime, timezone

from ..models import Job
from .base import HttpClient, ScrapeResult, ScraperError, polite_sleep

log = logging.getLogger(__name__)

EURES_SEARCH_URL = "https://europa.eu/eures/api/jv-searchengine/public/jv-search/search"
EURES_DETAIL_URL = "https://europa.eu/eures/api/jv-searchengine/public/jv/id/{id}"
# Human-facing portal page (SPA) - the link a user clicks to read/apply.
PORTAL_DETAIL_URL = "https://europa.eu/eures/portal/jv-se/jv-details/{id}?lang=en"

_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(text: str) -> str:
    if not text:
        return ""
    text = html.unescape(_TAG_RE.sub(" ", text))
    return re.sub(r"\s+", " ", text).strip()


def _ms_to_iso(ms) -> str:
    try:
        return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc).date().isoformat()
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def _region_names(location_map: dict, country_code: str) -> str:
    codes = (location_map or {}).get(country_code.upper()) or []
    # Live EURES payloads occasionally carry null region entries.
    return ", ".join(sorted(str(c) for c in codes if c))


class EuresScraper:
    name = "eures"

    def __init__(self, cfg: dict, http: HttpClient, matrix):
        self.cfg = cfg
        self.http = http
        self.matrix = matrix
        batch = cfg["batch"]
        self.page_size = min(int(batch.get("results_per_page", 50)), 50)
        self.max_pages = int(batch.get("max_pages_per_keyword", 2))
        self.kw_cap = int(batch.get("keywords_per_country", 8))
        self.req_delay = tuple(batch.get("delay_between_requests_sec", [1.5, 4.0]))
        # EURES query expansion for countries JobSpy/Indeed does not
        # cover (LT and SK in tier 1 by default): with no second source
        # they would be under-sampled next to PL/CZ/DE, so EURES
        # compensates with a wider keyword set and deeper pagination.
        boost = batch.get("eures_boost") or {}
        self.boost_countries = {
            str(c).strip().upper() for c in boost.get("countries", []) or []
        }
        self.boost_kw_cap = int(boost.get("keywords_per_country", self.kw_cap))
        self.boost_max_pages = int(boost.get("max_pages_per_keyword", self.max_pages))
        unknown = self.boost_countries - set(cfg["countries"])
        if unknown:
            log.warning("batch.eures_boost.countries has unknown code(s): %s",
                        ", ".join(sorted(unknown)))

    def limits_for(self, country_code: str):
        # (keyword cap, max pages per keyword, boosted?) for a country.
        if country_code.upper() in self.boost_countries:
            return self.boost_kw_cap, self.boost_max_pages, True
        return self.kw_cap, self.max_pages, False

    # -- mapping ---------------------------------------------------------
    def map_vacancy(self, jv: dict, country_code: str) -> Job:
        jv_id = str(jv.get("id") or "")
        employer = jv.get("employer") or {}
        return Job(
            title=(jv.get("title") or "").strip(),
            company=(employer.get("name") or "").strip(),
            country=country_code.upper(),
            source=self.name,
            source_id=jv_id,
            url=PORTAL_DETAIL_URL.format(id=jv_id) if jv_id else EURES_SEARCH_URL,
            description=_strip_html(jv.get("description") or ""),
            location=_region_names(jv.get("locationMap"), country_code),
            posted_date=_ms_to_iso(jv.get("creationDate")),
            languages=list(jv.get("availableLanguages") or []),
        )

    # -- search ----------------------------------------------------------
    def _search_keyword(self, keyword: str, location_code: str, want: int,
                        max_pages: int = None) -> list:
        """All vacancies for one keyword, paginated, up to ``want`` items."""
        collected = []
        for page in range(1, (max_pages or self.max_pages) + 1):
            payload = {
                "resultsPerPage": min(self.page_size, want - len(collected)) or self.page_size,
                "page": page,
                "sortSearch": "MOST_RECENT",
                "keywords": [{"keyword": keyword, "specificSearchCode": "EVERYWHERE"}],
                "publicationPeriod": None,
                "occupationUris": [],
                "skillUris": [],
                "requiredExperienceCodes": [],
                "positionScheduleCodes": [],
                "sectorCodes": [],
                "educationAndQualificationLevelCodes": [],
                "positionOfferingCodes": [],
                "locationCodes": [location_code],
                "euresFlagCodes": [],
                "otherBenefitsCodes": [],
                "requiredLanguages": [],
                "minNumberPost": None,
                "sessionId": uuid.uuid4().hex[:20],
                "requestLanguage": "en",
            }
            data = self.http.post_json(EURES_SEARCH_URL, payload)
            jvs = data.get("jvs") or []
            collected.extend(jvs)
            total = data.get("numberRecords") or 0
            if not jvs or len(collected) >= min(total, want):
                break
            polite_sleep(self.req_delay)
        return collected

    def scrape_country(self, country_code: str, max_jobs: int) -> ScrapeResult:
        country_code = country_code.upper()
        country_cfg = self.cfg["countries"][country_code]
        result = ScrapeResult(source=self.name, country=country_code)
        kw_cap, max_pages, boosted = self.limits_for(country_code)
        if boosted:
            result.notes.append(
                "eures boost: {} keywords x up to {} pages "
                "(no JobSpy/Indeed domain for {})".format(kw_cap, max_pages, country_code)
            )
        keywords = self.matrix.search_keywords(country_cfg.get("languages"), kw_cap)
        location_code = country_cfg["eures_location_code"]
        seen_ids = set()
        for kw in keywords:
            if result.fetched >= max_jobs:
                break
            try:
                jvs = self._search_keyword(kw, location_code, max_jobs - result.fetched,
                                           max_pages)
            except ScraperError as exc:
                result.errors.append("keyword {!r}: {}".format(kw, exc))
                continue
            except Exception as exc:  # noqa: BLE001 - per-keyword isolation
                result.errors.append("keyword {!r}: unexpected: {}".format(kw, exc))
                continue
            for jv in jvs:
                jv_id = str(jv.get("id") or "")
                if jv_id and jv_id in seen_ids:
                    continue
                seen_ids.add(jv_id)
                job = self.map_vacancy(jv, country_code)
                if job.title:
                    result.jobs.append(job)
            polite_sleep(self.req_delay)
        if result.errors:
            result.status = "partial" if result.jobs else "failed"
        return result
