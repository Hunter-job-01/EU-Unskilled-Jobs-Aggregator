"""Sweden JobTech JobSearch API adapter (Platsbanken, official catalogue
labels the data CC0).

Endpoint: GET https://jobsearch.api.jobtechdev.se/search?q=...&limit=...
The API currently serves unauthenticated search; the getting-started
text still mentions a key, so a live probe must re-confirm the current
authentication before any production use (the adapter itself does not
send credentials). Only ads whose workplace address grounds to Sweden
are accepted; anything else is rejected or quarantined by the shared
EU27 location rules.

Disabled by default (sources.sweden_jobsearch.enabled).
"""
from __future__ import annotations

import logging

from ..eu_location import classify_location
from ..models import Job
from .base import ScrapeResult, ScraperError, polite_sleep, strip_html

log = logging.getLogger(__name__)

API_URL = "https://jobsearch.api.jobtechdev.se/search"


class SwedenJobSearchScraper:
    name = "sweden_jobsearch"
    COUNTRY = "SE"

    def __init__(self, cfg: dict, http, matrix):
        self.cfg = cfg
        self.http = http
        self.matrix = matrix
        sc = (cfg.get("sources") or {}).get("sweden_jobsearch") or {}
        self.enabled = bool(sc.get("enabled", False))
        self.api_url = str(sc.get("api_url") or API_URL)
        self.kw_cap = int(sc.get("keywords_per_country",
                                 cfg["batch"].get("keywords_per_country", 8)))
        self.limit = min(int(sc.get("results_per_query", 25)), 100)
        self.req_delay = tuple(sc.get("delay_between_requests_sec",
                                      cfg["batch"].get("delay_between_requests_sec", [1.5, 4.0])))

    def scrape_country(self, country_code: str, max_jobs: int) -> ScrapeResult:
        country_code = country_code.upper()
        result = ScrapeResult(source=self.name, country=country_code)
        if not self.enabled:
            result.status = "skipped"
            result.notes.append("disabled in config (sources.sweden_jobsearch.enabled: false)")
            return result
        if country_code != self.COUNTRY:
            result.status = "skipped"
            result.notes.append("Platsbanken/JobSearch covers Sweden only")
            return result
        languages = (self.cfg["countries"].get("SE") or {}).get("languages") or ["sv", "en"]
        keywords = self.matrix.search_keywords(languages, self.kw_cap)
        seen_ids = set()
        for kw in keywords:
            if result.fetched >= max_jobs:
                break
            try:
                resp = self.http.get(self.api_url, params={
                    "q": kw, "limit": min(self.limit, max_jobs - result.fetched), "offset": 0})
                data = resp.json()
            except ScraperError as exc:
                result.errors.append("keyword {!r}: {}".format(kw, exc))
                continue
            except (ValueError, AttributeError) as exc:
                result.errors.append("keyword {!r}: unparsable response: {}".format(kw, exc))
                continue
            hits = data.get("hits") if isinstance(data, dict) else None
            if not isinstance(hits, list):
                result.errors.append("keyword {!r}: unexpected schema (no hits list)".format(kw))
                continue
            for hit in hits:
                if result.fetched >= max_jobs:
                    break
                ad_id = str(hit.get("id") or "")
                if ad_id and ad_id in seen_ids:
                    continue
                workplace = hit.get("workplace_address") or {}
                # JobSearch country_code is a numeric taxonomy id
                # ("199" = Sverige), not ISO; the country label is the
                # authoritative readable field.
                cc = workplace.get("country_code")
                structured = cc if isinstance(cc, str) and cc.isalpha() else workplace.get("country")
                verdict = classify_location(
                    structured_country=structured,
                    location_text=", ".join(str(p) for p in (
                        workplace.get("municipality"), workplace.get("region")) if p),
                    search_country="SE",
                )
                if verdict.status == "non_eu":
                    result.stats["non_eu_rejects"] = result.stats.get("non_eu_rejects", 0) + 1
                    continue
                if verdict.quarantined or verdict.country != "SE":
                    result.stats["quarantined"] = result.stats.get("quarantined", 0) + 1
                    continue
                seen_ids.add(ad_id)
                employer = hit.get("employer") or {}
                desc = hit.get("description") or {}
                # Authoritative structured requirement fields from the API
                # stay visible as evidence lines, so a KNOWN mandatory
                # conflict (e.g. driving licence) is rejected from stored
                # text instead of degrading to "unknown" when the JS detail
                # page only yields site chrome. Absence/False is never a
                # waiver, so lines are appended only for positive flags.
                evidence = []
                if hit.get("driving_license_required"):
                    evidence.append("driving licence required (JobSearch structured field)")
                if hit.get("experience_required"):
                    evidence.append("experience required (JobSearch structured field)")
                for lang in ((hit.get("must_have") or {}).get("languages") or []):
                    label = str((lang or {}).get("label") or "").strip()
                    if label:
                        evidence.append("must-have language: {}".format(label))
                description = strip_html(desc.get("text") or "")[:20000]
                if evidence:
                    description = (description + "\n[JobSearch evidence] "
                                   + "; ".join(evidence)).strip()
                job = Job(
                    title=str(hit.get("headline") or "").strip(),
                    company=str(employer.get("name") or "").strip(),
                    country="SE",
                    source=self.name,
                    source_id=ad_id,
                    url=str(hit.get("webpage_url")
                            or (hit.get("application_details") or {}).get("url") or ""),
                    description=description,
                    location=", ".join(str(p) for p in (
                        workplace.get("municipality"), workplace.get("region")) if p),
                    posted_date=str(hit.get("publication_date") or "")[:10] or None,
                )
                if job.title and job.url:
                    result.jobs.append(job)
            polite_sleep(self.req_delay)
        if result.errors:
            result.status = "partial" if result.jobs else "failed"
        return result
