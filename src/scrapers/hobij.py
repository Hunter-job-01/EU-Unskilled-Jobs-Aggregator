"""HOBIJ adapter (NL agency, public vacancy sitemap + JobPosting JSON-LD).

Discovery: https://hobij.com/vacancy-sitemap.xml lists
/nl/vacatures/<id>-<slug> pages (82 at probe 2026-11-03). Pages carry
schema.org JobPosting JSON-LD: title, datePosted, jobLocation
(addressLocality; NO addressCountry - NL fixed at source level, recorded
in stats), hiringOrganization, baseSalary (EUR/HOUR), description.

HOBIJ is a Dutch temp agency. Foreign-applicant (Egypt-resident)
eligibility is NOT asserted by this adapter: unknown unless separate
evidence exists.

Enabled per owner authorization (sources.hobij.enabled).
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

SITEMAP_URL = "https://hobij.com/vacancy-sitemap.xml"
_COUNTRY = "NL"  # source-level placement country (Dutch agency board)
_LOC_RE = re.compile(r"<loc>\s*(https://hobij\.com/nl/vacatures/[^<\s]+)\s*</loc>")
_LD_RE = re.compile(r'application/ld\+json[^>]*>(.*?)</script>', re.S)


def _jobposting(body: str) -> dict:
    dec = json.JSONDecoder(strict=False)
    for block in _LD_RE.findall(body or ""):
        try:
            data, _ = dec.raw_decode(block.lstrip())
        except ValueError:
            continue
        if isinstance(data, dict) and data.get("@type") == "JobPosting":
            return data
    return {}


class HobijScraper:
    name = "hobij"

    def __init__(self, cfg: dict, http):
        self.cfg = cfg
        self.http = http
        sc = (cfg.get("sources") or {}).get("hobij") or {}
        self.enabled = bool(sc.get("enabled", False))
        self.sitemap_url = str(sc.get("sitemap_url") or SITEMAP_URL)
        self.max_pages = int(sc.get("max_pages", 10))
        self.req_delay = tuple(sc.get("delay_between_requests_sec",
                                      cfg["batch"].get("delay_between_requests_sec", [1.5, 4.0])))
        self.early_filter = (
            KeywordMatrix(cfg["keywords"])
            if (cfg.get("scoring") or {}).get("early_title_filter", True)
            else None
        )
        self._urls = None

    def _sitemap_urls(self, result: ScrapeResult) -> list:
        try:
            body = self.http.get(self.sitemap_url).text
        except ScraperError as exc:
            result.errors.append("sitemap: {}".format(exc))
            return []
        urls = list(dict.fromkeys(_LOC_RE.findall(body or "")))
        result.stats["sitemap_urls"] = len(urls)
        return urls

    def _parse_page(self, url: str, body: str):
        d = _jobposting(body)
        if not d:
            return None
        valid = str(d.get("validThrough") or "").strip()
        if valid and valid[:10] < date.today().isoformat():
            return "expired"
        title = str(d.get("title") or "").strip()
        addr = ((d.get("jobLocation") or {}).get("address")
                if isinstance(d.get("jobLocation"), dict) else {}) or {}
        locality = str(addr.get("addressLocality") or "").strip()
        org = d.get("hiringOrganization") or {}
        company = str(org.get("name") or "").strip() if isinstance(org, dict) else ""
        salary = d.get("baseSalary") or {}
        salary_txt = ""
        if isinstance(salary, dict):
            val = salary.get("value") or {}
            if isinstance(val, dict) and val.get("value"):
                salary_txt = "Salary: {} {}/{}".format(
                    val.get("value"), salary.get("currency", ""), val.get("unitText", ""))
        desc = strip_html(str(d.get("description") or ""))[:20000]
        if salary_txt:
            desc = "{}. {}".format(salary_txt, desc).strip()
        return Job(
            title=title,
            company=company or "HOBIJ",
            country=_COUNTRY,
            source=self.name,
            source_id=url.rstrip("/").rsplit("/", 1)[-1],
            url=url,
            description=desc,
            location=locality,
            posted_date=str(d.get("datePosted") or "").strip() or None,
        )

    def scrape_country(self, country_code: str, max_jobs: int) -> ScrapeResult:
        country_code = country_code.upper()
        result = ScrapeResult(source=self.name, country=country_code)
        if not self.enabled:
            result.status = "skipped"
            result.notes.append("disabled in config (sources.hobij.enabled: false)")
            return result
        if country_code != _COUNTRY:
            result.status = "skipped"
            result.notes.append(
                "HOBIJ board placements are {} (source-level, Dutch agency); "
                "skipped for {}".format(_COUNTRY, country_code))
            return result
        if self._urls is None:
            self._urls = self._sitemap_urls(result)
        else:
            result.notes.append("reused {} sitemap urls fetched earlier this run".format(len(self._urls)))
        result.stats["country_assumed_from_source"] = _COUNTRY
        fetched = 0
        for url in self._urls:
            if result.fetched >= max_jobs or fetched >= self.max_pages:
                break
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
