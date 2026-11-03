"""JobSpy scraper (secondary source: Indeed country domains).

Coverage note (verified against python-jobspy 1.4.1): JobSpy's Country
enum supports only 18 of the 27 EU states - AT, BE, CZ, DK, FI, FR, DE,
GR, HU, IE, IT, LU, NL, PL, PT, RO, ES, SE. The other nine (BG, HR, CY,
EE, LV, LT, MT, SI, SK) return status "skipped" here and are covered by
the EURES scraper instead.

Indeed actively blocks datacenter traffic; failures are isolated per
country and reported, never fatal. LinkedIn via JobSpy is supported by
the library but blocks even more aggressively, so it stays opt-in via
config batch.jobspy_sites.
"""
from __future__ import annotations

import logging

from ..models import Job
from .base import ScrapeResult, polite_sleep

log = logging.getLogger(__name__)


class JobSpyScraper:
    name = "jobspy"

    #: Sites this adapter knows how to build country-scoped queries for.
    SUPPORTED_SITES = frozenset({"indeed", "linkedin"})

    def __init__(self, cfg: dict, matrix):
        self.cfg = cfg
        self.matrix = matrix
        batch = cfg["batch"]
        self.kw_cap = int(batch.get("keywords_per_country", 8))
        self.results_wanted = int(batch.get("jobspy_results_wanted", 25))
        self.hours_old = int(batch.get("jobspy_hours_old", 720))
        self.sites = [str(s).strip().lower() for s in batch.get("jobspy_sites", ["indeed"])]
        unknown = sorted(set(self.sites) - self.SUPPORTED_SITES)
        if unknown:
            raise ValueError(
                "unknown jobspy_sites: {} (supported: {})".format(
                    ", ".join(unknown), ", ".join(sorted(self.SUPPORTED_SITES)))
            )
        if not self.sites:
            raise ValueError("batch.jobspy_sites is empty; nothing for the JobSpy source to do")
        self.req_delay = tuple(batch.get("delay_between_requests_sec", [1.5, 4.0]))

    @staticmethod
    def _location_text(loc) -> str:
        if loc is None:
            return ""
        if isinstance(loc, str):
            return loc
        # pydantic Location object or dict
        parts = []
        for attr in ("city", "state", "country"):
            value = getattr(loc, attr, None)
            if value is None and isinstance(loc, dict):
                value = loc.get(attr)
            if value:
                parts.append(str(getattr(value, "value", value)))
        return ", ".join(parts)

    @staticmethod
    def _clean_cell(value) -> str:
        # pandas missing values (NaN/None) are truthy floats, so a bare
        # `a or b` keeps NaN and str() turns it into the literal "nan".
        if value is None:
            return ""
        text = str(value).strip()
        return "" if text.lower() in ("nan", "none", "nat") else text

    def map_row(self, row: dict, country_code: str, site: str) -> Job:
        date_posted = row.get("date_posted")
        source_id = self._clean_cell(row.get("id")) or self._clean_cell(row.get("job_url"))
        url = self._clean_cell(row.get("job_url_direct")) or self._clean_cell(row.get("job_url"))
        rebuilt = False
        if not url and site == "indeed" and source_id.startswith("in-"):
            # jobspy builds job_url as {country}.indeed.com/viewjob?jk={key}
            # with id f"in-{key}"; rebuild the canonical posting URL when the
            # frame lost both URL columns, and mark it as unverified.
            url = "https://{}.indeed.com/viewjob?jk={}".format(country_code.lower(), source_id[3:])
            rebuilt = True
        return Job(
            title=str(row.get("title") or "").strip(),
            company=str(row.get("company") or "").strip(),
            country=country_code.upper(),
            source="jobspy_{}".format(site),
            source_id=source_id,
            url=url,
            description=str(row.get("description") or "")[:20000],
            location=self._location_text(row.get("location")),
            posted_date=str(date_posted)[:10] if date_posted else None,
            url_provenance="reconstructed_from_source_key" if rebuilt else "",
            url_verified=False if rebuilt else None,
        )

    def scrape_country(self, country_code: str, max_jobs: int) -> ScrapeResult:
        country_code = country_code.upper()
        country_cfg = self.cfg["countries"][country_code]
        result = ScrapeResult(source=self.name, country=country_code)
        try:
            from jobspy import scrape_jobs
        except ImportError:
            result.status = "failed"
            result.errors.append("python-jobspy not installed (pip install python-jobspy)")
            return result

        # Per-site capability and call plans: Indeed is country-scoped via
        # country_indeed and only exists for the 18 supported countries;
        # LinkedIn has no per-country domain in JobSpy, so it is scoped by
        # a location string (country name) and remains available where
        # Indeed is unsupported.
        call_plans = []
        if "indeed" in self.sites:
            if country_cfg.get("indeed_supported"):
                call_plans.append(("indeed", {"site_name": ["indeed"],
                                              "country_indeed": country_cfg["indeed_country"]}))
            else:
                result.notes.append(
                    "indeed: {} not supported by JobSpy/Indeed (EURES covers it)".format(country_code)
                )
        if "linkedin" in self.sites:
            call_plans.append(("linkedin", {"site_name": ["linkedin"],
                                            "location": country_cfg["name"]}))
        if not call_plans:
            result.status = "skipped"
            return result
        keywords = self.matrix.search_keywords(country_cfg.get("languages"), self.kw_cap)
        seen_urls = set()
        for site, extra in call_plans:
            for kw in keywords:
                if result.fetched >= max_jobs:
                    break
                try:
                    frame = scrape_jobs(
                        search_term=kw,
                        results_wanted=min(self.results_wanted, max_jobs - result.fetched),
                        hours_old=self.hours_old,
                        verbose=0,
                        **extra,
                    )
                except Exception as exc:  # noqa: BLE001 - per-keyword isolation
                    result.errors.append("{} keyword {!r}: {}".format(site, kw, exc))
                    continue
                if frame is None:
                    result.errors.append("{} keyword {!r}: no response frame".format(site, kw))
                    continue
                # JobSpy can return an empty DataFrame with errors in attrs
                # instead of raising. Never call a blocked source healthy.
                attrs = getattr(frame, "attrs", {}) or {}
                frame_errors = attrs.get("errors") or {}
                for err_site, detail in frame_errors.items():
                    result.errors.append("{} keyword {!r}: {}".format(err_site, kw, detail))
                for warn_site, details in (attrs.get("warnings") or {}).items():
                    result.notes.append("{}: {}".format(warn_site, details))
                blocked = any(any(marker in str(detail).lower() for marker in
                              ("403", "429", "captcha", "blocked", "forbidden", "rate limit"))
                              for detail in frame_errors.values())
                if blocked:
                    result.notes.append("Source denied or throttled this country; remaining keywords skipped")
                if frame.empty:
                    if blocked:
                        break
                    polite_sleep(self.req_delay)
                    continue
                for row in frame.to_dict("records"):
                    url = str(row.get("job_url") or "")
                    if url and url in seen_urls:
                        continue
                    seen_urls.add(url)
                    job = self.map_row(row, country_code, str(row.get("site") or site))
                    if job.title and job.url:
                        result.jobs.append(job)
                polite_sleep(self.req_delay)
                if blocked:
                    break
        if result.jobs:
            result.status = "partial" if result.errors else "ok"
        elif result.errors:
            result.status = "failed"
        else:
            result.status = "ok"  # plans ran cleanly, zero results is an answer
        return result
