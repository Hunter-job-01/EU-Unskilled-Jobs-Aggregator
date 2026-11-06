"""OTTO Work Force vacancies adapter (public careers sitemap, no key).

Discovery: GET https://ottoworkforce.eu/sitemap.vacancies.xml lists
server-rendered vacancy pages under /en/jobs/<id>-<slug>. Each page is
plain HTML (probe 2026-11-02): h1 = title, span.location = city, and an
attrs block with Salary / Categories / Sector / Employment type /
Work schedule / Accepted languages label-value pairs. No JSON-LD.

OTTO Work Force is a Dutch temp agency; vacancies on this board are
Netherlands placements. Country is therefore fixed to NL at the source
level (honest, stated here and in stats) rather than guessed per page.
Foreign-applicant eligibility is NOT asserted by this adapter: any
"serves applicants residing outside the NL/EU" signal belongs in the
registry/tracker as evidence, never assumed from a live page.

Disabled by default (sources.ottoworkforce.enabled).
"""
from __future__ import annotations

import logging
import re

from ..eu_location import EU27
from ..models import Job
from ..keywords import KeywordMatrix
from .base import ScrapeResult, ScraperError, polite_sleep, strip_html

log = logging.getLogger(__name__)

SITEMAP_URL = "https://ottoworkforce.eu/sitemap.vacancies.xml"
_COUNTRY = "NL"  # source-level placement country (Dutch agency board)

_LOC_RE = re.compile(r"<loc>\s*(https://ottoworkforce\.eu/en/jobs/[^<\s]+)\s*</loc>")
_H1_RE = re.compile(r"<h1[^>]*>(.*?)</h1>", re.S)
_LOCATION_RE = re.compile(r'class="location[^"]*"[^>]*>(.*?)</span>', re.S)
_ATTR_RE = re.compile(
    r'text-white/65[^>]*>\s*(.*?)\s*</span>\s*<span[^>]*>\s*(.*?)\s*</span>',
    re.S,
)
_META_DESC_RE = re.compile(
    r'<meta\s+name="description"\s+content="([^"]*)"', re.S
)


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", strip_html(text or "")).strip()


class OttoWorkforceScraper:
    name = "ottoworkforce"

    def __init__(self, cfg: dict, http):
        self.cfg = cfg
        self.http = http
        sc = (cfg.get("sources") or {}).get("ottoworkforce") or {}
        self.enabled = bool(sc.get("enabled", False))
        self.sitemap_url = str(sc.get("sitemap_url") or SITEMAP_URL)
        self.max_vacancies = int(sc.get("max_vacancies", 10))
        self.req_delay = tuple(sc.get("delay_between_requests_sec",
                                      cfg["batch"].get("delay_between_requests_sec", [1.5, 4.0])))
        self.early_filter = (
            KeywordMatrix(cfg["keywords"])
            if (cfg.get("scoring") or {}).get("early_title_filter", True)
            else None
        )
        self._urls = None  # sitemap URLs shared across country queries

    def _sitemap_urls(self, result: ScrapeResult) -> list:
        try:
            resp = self.http.get(self.sitemap_url)
            body = resp.text
        except ScraperError as exc:
            result.errors.append("sitemap: {}".format(exc))
            return []
        urls = _LOC_RE.findall(body or "")
        result.stats["sitemap_urls"] = len(urls)
        return urls

    def _parse_page(self, url: str, body: str) -> Job:
        m = _H1_RE.search(body or "")
        title = _clean(m.group(1)) if m else ""
        loc = ""
        m = _LOCATION_RE.search(body or "")
        if m:
            loc = _clean(m.group(1))
        attrs = {}
        for k, v in _ATTR_RE.findall(body or ""):
            attrs[_clean(k)] = _clean(v)
        desc = ""
        m = _META_DESC_RE.search(body or "")
        if m:
            desc = _clean(m.group(1))
        salary = attrs.get("Salary", "")
        extras = "; ".join(
            "{}: {}".format(k, v)
            for k, v in attrs.items()
            if k != "Salary" and v
        )
        description = desc
        if salary:
            description = "Salary: {}. {}".format(salary, description).strip()
        if extras:
            description = "{} [{}]".format(description, extras).strip()
        return Job(
            title=title,
            company="OTTO Work Force",
            country=_COUNTRY,
            source=self.name,
            source_id=url.rsplit("/", 1)[-1],
            url=url,
            description=description[:20000],
            location=loc,
        )

    def scrape_country(self, country_code: str, max_jobs: int) -> ScrapeResult:
        country_code = country_code.upper()
        result = ScrapeResult(source=self.name, country=country_code)
        if not self.enabled:
            result.status = "skipped"
            result.notes.append("disabled in config (sources.ottoworkforce.enabled: false)")
            return result
        if country_code not in EU27:
            result.status = "skipped"
            result.notes.append("{} is outside the EU27 allowlist".format(country_code))
            return result
        if country_code != _COUNTRY:
            result.status = "skipped"
            result.notes.append(
                "OTTO board placements are {} (source-level, Dutch agency); "
                "skipped for {}".format(_COUNTRY, country_code))
            return result
        if self._urls is None:
            self._urls = self._sitemap_urls(result)
        else:
            result.notes.append("reused {} sitemap urls fetched earlier this run".format(len(self._urls)))
        result.stats["country_assumed_from_source"] = _COUNTRY
        result.stats["index_count"] = len(self._urls)
        # v6 stage A (slug-first delta): postings the store already holds
        # fresh (last_seen within the recheck window) are not re-fetched;
        # the sitemap index above is still fully enumerated, so lifecycle
        # coverage and removal detection stay honest. Skipped slugs are
        # reported so main.py can mark them seen. Records older than the
        # window rotate back in for a bounded-staleness recheck.
        _known_fresh = getattr(self, "known_fresh_ids", None) or set()
        fetched = 0
        for url in self._urls:
            if result.fetched >= max_jobs or fetched >= self.max_vacancies:
                break
            _slug = url.rstrip("/").rsplit("/", 1)[-1]
            if _slug in _known_fresh:
                result.stats["delta_skipped"] = result.stats.get("delta_skipped", 0) + 1
                result.stats.setdefault("delta_skipped_ids", []).append(_slug)
                continue
            try:
                page = self.http.get(url).text
            except ScraperError as exc:
                result.errors.append("page {}: {}".format(url, exc))
                continue
            fetched += 1
            job = self._parse_page(url, page)
            if not job.title:
                result.stats["unparsable_pages"] = result.stats.get("unparsable_pages", 0) + 1
                continue
            if self.early_filter is not None:
                reason = self.early_filter.early_title_exclusion(job.title)
                if reason:
                    result.stats["early_title_excluded"] = result.stats.get("early_title_excluded", 0) + 1
                    continue
            result.jobs.append(job)
            polite_sleep(self.req_delay)
        result.stats["pages_fetched"] = fetched
        if result.errors:
            result.status = "partial" if result.jobs else "failed"
        return result
