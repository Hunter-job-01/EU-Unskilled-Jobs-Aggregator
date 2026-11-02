"""AnyworkAnywhere board adapter (public WP job sitemaps, no key).

Discovery: job-sitemap.xml .. job-sitemap7.xml list /job/<slug>/ pages
(probe 2026-11-02). Localized variants under /fr//fi//nl/ etc. are the
same vacancies - only canonical /job/ URLs are fetched (dedupe by slug).
Pages carry schema.org JobPosting JSON-LD: title, datePosted,
validThrough, jobLocation[].address.addressCountry, hiringOrganization,
description. The blocks need a lenient decode (control characters and
concatenated objects observed on the live site).

Country comes from each posting's jobLocation.addressCountry - per-job
grounding, no source-level assumption; non-EU27 rows are dropped here.
Foreign-applicant (Egypt-resident) eligibility is NOT asserted by this
adapter: unknown unless separate evidence exists.

Disabled by default (sources.anyworkanywhere.enabled).
"""
from __future__ import annotations

import json
import logging
import re

from datetime import date

from ..eu_location import EU27
from ..models import Job
from ..keywords import KeywordMatrix
from .base import ScrapeResult, ScraperError, polite_sleep, strip_html

log = logging.getLogger(__name__)

SITEMAP_TEMPLATE = "https://www.anyworkanywhere.com/job-sitemap{}.xml"
SITEMAP_INDEX = "https://www.anyworkanywhere.com/job-sitemap.xml"
_SITEMAP_RANGE = range(7, 0, -1)  # newest first: job-sitemap7.xml .. job-sitemap1.xml (old pages predate JSON-LD)

_LOC_RE = re.compile(
    r"<loc>\s*(https://www\.anyworkanywhere\.com/job/[^<\s]+)\s*</loc>")
_LD_RE = re.compile(r'application/ld\+json[^>]*>(.*?)</script>', re.S)
_COUNTRY_NAME = {
    "portugal": "PT", "greece": "GR", "spain": "ES", "france": "FR",
    "germany": "DE", "netherlands": "NL", "italy": "IT", "croatia": "HR",
    "czech republic": "CZ", "czechia": "CZ", "poland": "PL", "malta": "MT",
    "austria": "AT", "belgium": "BE", "bulgaria": "BG", "cyprus": "CY",
    "denmark": "DK", "estonia": "EE", "finland": "FI", "hungary": "HU",
    "ireland": "IE", "latvia": "LV", "lithuania": "LT", "luxembourg": "LU",
    "romania": "RO", "slovakia": "SK", "slovenia": "SI", "sweden": "SE",
}
_ISO2 = set(_COUNTRY_NAME.values())


class AnyworkAnywhereScraper:
    name = "anyworkanywhere"

    def __init__(self, cfg: dict, http):
        self.cfg = cfg
        self.http = http
        sc = (cfg.get("sources") or {}).get("anyworkanywhere") or {}
        self.enabled = bool(sc.get("enabled", False))
        self.sitemap_template = str(sc.get("sitemap_template") or SITEMAP_TEMPLATE)
        self.max_pages = int(sc.get("max_pages", 10))
        self.req_delay = tuple(sc.get("delay_between_requests_sec",
                                      cfg["batch"].get("delay_between_requests_sec", [1.5, 4.0])))
        self.early_filter = (
            KeywordMatrix(cfg["keywords"])
            if (cfg.get("scoring") or {}).get("early_title_filter", True)
            else None
        )
        self._urls = None
        self._cursor = 0  # pages are consumed in order across country calls

    def _sitemap_urls(self, result: ScrapeResult) -> list:
        urls = []
        for i in _SITEMAP_RANGE:
            url = self.sitemap_template.format(i)
            try:
                body = self.http.get(url).text
            except ScraperError as exc:
                result.errors.append("sitemap {}: {}".format(i, exc))
                continue
            urls.extend(_LOC_RE.findall(body or ""))
            polite_sleep(self.req_delay)
        seen, deduped = set(), []
        for u in urls:
            slug = u.rstrip("/").rsplit("/", 1)[-1]
            if slug and slug not in seen:
                seen.add(slug)
                deduped.append(u)
        result.stats["sitemap_urls"] = len(deduped)
        return deduped

    @staticmethod
    def _walk_jobposting(node):
        """Depth-first search for a JobPosting node (handles @graph and
        concatenated/nested structures seen on live pages)."""
        if isinstance(node, dict):
            if node.get("@type") == "JobPosting":
                return node
            for value in node.values():
                found = AnyworkAnywhereScraper._walk_jobposting(value)
                if found:
                    return found
        elif isinstance(node, list):
            for item in node:
                found = AnyworkAnywhereScraper._walk_jobposting(item)
                if found:
                    return found
        return {}

    @staticmethod
    def _jobposting(body: str) -> dict:
        dec = json.JSONDecoder(strict=False)
        for block in _LD_RE.findall(body or ""):
            rest = block.lstrip()
            while rest:
                try:
                    data, end = dec.raw_decode(rest)
                except ValueError:
                    break
                found = AnyworkAnywhereScraper._walk_jobposting(data)
                if found:
                    return found
                rest = rest[end:].lstrip()
        return {}

    def _parse_page(self, url: str, body: str):
        d = self._jobposting(body)
        if not d:
            return None
        title = str(d.get("title") or "").strip()
        locs = d.get("jobLocation") or []
        if isinstance(locs, dict):
            locs = [locs]
        country = ""
        locality = ""
        for loc in locs:
            addr = (loc or {}).get("address") or {}
            raw = str(addr.get("addressCountry") or "").strip()
            locality = locality or str(addr.get("addressLocality") or "").strip()
            if len(raw) == 2 and raw.upper() in _ISO2:
                country = raw.upper()
                break
            mapped = _COUNTRY_NAME.get(raw.lower())
            if mapped:
                country = mapped
                break
        org = d.get("hiringOrganization") or {}
        company = str(org.get("name") or "").strip() if isinstance(org, dict) else ""
        valid = str(d.get("validThrough") or "").strip()
        if valid and valid[:10] < date.today().isoformat():
            return "expired"
        desc = strip_html(str(d.get("description") or ""))[:20000]
        posted = str(d.get("datePosted") or "").strip() or None
        return Job(
            title=title,
            company=company or "AnyworkAnywhere listing",
            country=country,
            source=self.name,
            source_id=url.rstrip("/").rsplit("/", 1)[-1],
            url=url,
            description=desc,
            location=locality,
            posted_date=posted,
        )

    def scrape_country(self, country_code: str, max_jobs: int) -> ScrapeResult:
        country_code = country_code.upper()
        result = ScrapeResult(source=self.name, country=country_code)
        if not self.enabled:
            result.status = "skipped"
            result.notes.append("disabled in config (sources.anyworkanywhere.enabled: false)")
            return result
        if country_code not in EU27:
            result.status = "skipped"
            result.notes.append("{} is outside the EU27 allowlist".format(country_code))
            return result
        if self._urls is None:
            self._urls = self._sitemap_urls(result)
        else:
            result.notes.append("reused {} sitemap urls fetched earlier this run".format(len(self._urls)))
        fetched = 0
        while self._cursor < len(self._urls):
            if result.fetched >= max_jobs or fetched >= self.max_pages:
                break
            url = self._urls[self._cursor]
            self._cursor += 1
            try:
                body = self.http.get(url).text
            except ScraperError as exc:
                result.errors.append("page {}: {}".format(url, exc))
                continue
            fetched += 1
            job = self._parse_page(url, body)
            if job == "expired":
                result.stats["expired_skipped"] = result.stats.get("expired_skipped", 0) + 1
                continue
            if job is None or not job.title:
                result.stats["unparsable_pages"] = result.stats.get("unparsable_pages", 0) + 1
                continue
            if job.country != country_code:
                result.stats["other_country"] = result.stats.get("other_country", 0) + 1
                continue
            if self.early_filter is not None:
                reason = self.early_filter.early_title_exclusion(job.title)
                if reason:
                    result.stats["early_title_excluded"] = result.stats.get("early_title_excluded", 0) + 1
                    continue
            result.jobs.append(job)
            polite_sleep(self.req_delay)
        result.stats["pages_fetched"] = fetched
        result.stats["urls_consumed"] = self._cursor
        if result.errors:
            result.status = "partial" if result.jobs else "failed"
        return result
