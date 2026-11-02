"""Employer-scoped public ATS read routes: Greenhouse, Lever,
SmartRecruiters.

These are NOT all-Europe discovery APIs. They read the public postings
of one verified employer board each, so the adapter only queries tenants
the owner reviewed and listed under sources.ats_public.tenants with
enabled: true and an evidence_url pointing at the employer's real
careers page. Guessed/enumerated tenants are never queried.

* Greenhouse: GET https://boards-api.greenhouse.io/v1/boards/{tenant}/jobs?content=true
* Lever:      GET https://api.lever.co/v0/postings/{tenant}?mode=json
* SmartRecruiters: GET https://api.smartrecruiters.com/v1/companies/{tenant}/postings
  (public postings endpoint only - never INTERNAL postings, applicant
  records or privileged credentials)

Disabled by default (sources.ats_public.enabled); with no enabled
tenants it is a clean no-op.
"""
from __future__ import annotations

import logging

from ..eu_location import EU27, classify_location
from ..models import Job
from ..keywords import KeywordMatrix
from .base import ScrapeResult, ScraperError, polite_sleep, strip_html

log = logging.getLogger(__name__)

PROVIDERS = ("greenhouse", "lever", "smartrecruiters")


class AtsPublicScraper:
    name = "ats_public"

    def __init__(self, cfg: dict, http):
        self.cfg = cfg
        self.http = http
        sc = (cfg.get("sources") or {}).get("ats_public") or {}
        self.enabled = bool(sc.get("enabled", False))
        self.max_postings_per_tenant = int(sc.get("max_postings_per_tenant", 100))
        self.req_delay = tuple(sc.get("delay_between_requests_sec",
                                      cfg["batch"].get("delay_between_requests_sec", [1.5, 4.0])))
        # Early title-only exclusion (scoring.early_title_filter): drops
        # technical/office titles before any scoring/translation work.
        self.early_filter = (
            KeywordMatrix(cfg["keywords"])
            if (cfg.get("scoring") or {}).get("early_title_filter", True)
            else None
        )
        self.tenants = []
        for t in sc.get("tenants") or []:
            if not isinstance(t, dict):
                continue
            provider = str(t.get("provider") or "").lower()
            tenant = str(t.get("tenant") or "").strip()
            country = str(t.get("country") or "").upper()
            if provider in PROVIDERS and tenant and country in EU27:
                self.tenants.append({
                    "provider": provider, "tenant": tenant, "country": country,
                    "employer": str(t.get("employer") or tenant),
                    "enabled": bool(t.get("enabled", False)),
                    "evidence_url": str(t.get("evidence_url") or ""),
                })

    def _url(self, provider: str, tenant: str) -> str:
        if provider == "greenhouse":
            return "https://boards-api.greenhouse.io/v1/boards/{}/jobs?content=true".format(tenant)
        if provider == "lever":
            return "https://api.lever.co/v0/postings/{}?mode=json".format(tenant)
        return "https://api.smartrecruiters.com/v1/companies/{}/postings?limit=100".format(tenant)

    def _rows(self, provider: str, payload) -> list:
        if provider == "greenhouse":
            jobs = payload.get("jobs") if isinstance(payload, dict) else None
            return jobs if isinstance(jobs, list) else []
        if provider == "lever":
            return payload if isinstance(payload, list) else []
        content = payload.get("content") if isinstance(payload, dict) else None
        return content if isinstance(content, list) else []

    def _map_row(self, provider: str, row: dict, tenant: dict) -> Job:
        if provider == "greenhouse":
            loc = (row.get("location") or {}).get("name") or ""
            return Job(title=str(row.get("title") or "").strip(),
                       company=tenant["employer"], country=tenant["country"],
                       source="{}_{}".format(self.name, provider),
                       source_id=str(row.get("id") or ""),
                       url=str(row.get("absolute_url") or ""),
                       description=strip_html(row.get("content") or "")[:20000],
                       location=loc,
                       posted_date=str(row.get("updated_at") or "")[:10] or None)
        if provider == "lever":
            cats = row.get("categories") or {}
            return Job(title=str(row.get("text") or "").strip(),
                       company=tenant["employer"], country=tenant["country"],
                       source="{}_{}".format(self.name, provider),
                       source_id=str(row.get("id") or ""),
                       url=str(row.get("hostedUrl") or row.get("applyUrl") or ""),
                       description=strip_html(row.get("description") or "")[:20000],
                       location=str(cats.get("location") or ""),
                       posted_date=str(row.get("createdAt") or "")[:10] or None)
        loc = row.get("location") or {}
        loc_text = ", ".join(str(p) for p in (loc.get("city"), loc.get("region")) if p)
        return Job(title=str(row.get("name") or "").strip(),
                   company=tenant["employer"], country=tenant["country"],
                   source="{}_{}".format(self.name, provider),
                   source_id=str(row.get("id") or ""),
                   url=(row.get("ref") or ""),
                   location=loc_text or str(loc.get("country") or ""),
                   posted_date=str(row.get("releasedDate") or "")[:10] or None)

    def scrape_country(self, country_code: str, max_jobs: int) -> ScrapeResult:
        country_code = country_code.upper()
        result = ScrapeResult(source=self.name, country=country_code)
        if not self.enabled:
            result.status = "skipped"
            result.notes.append("disabled in config (sources.ats_public.enabled: false)")
            return result
        tenants = [t for t in self.tenants if t["enabled"] and t["country"] == country_code]
        if not tenants:
            result.status = "skipped"
            result.notes.append("no reviewed+enabled tenants for {}".format(country_code))
            return result
        for tenant in tenants:
            if result.fetched >= max_jobs:
                break
            if not tenant["evidence_url"]:
                result.notes.append(
                    "tenant {} skipped: no careers-page evidence_url recorded".format(tenant["tenant"]))
                continue
            url = self._url(tenant["provider"], tenant["tenant"])
            try:
                resp = self.http.get(url)
                payload = resp.json()
            except ScraperError as exc:
                result.errors.append("{} {}: {}".format(tenant["provider"], tenant["tenant"], exc))
                continue
            except (ValueError, AttributeError) as exc:
                result.errors.append("{} {}: unparsable response: {}".format(
                    tenant["provider"], tenant["tenant"], exc))
                continue
            rows = self._rows(tenant["provider"], payload)[: self.max_postings_per_tenant]
            result.stats["tenants_queried"] = result.stats.get("tenants_queried", 0) + 1
            for row in rows:
                if result.fetched >= max_jobs:
                    break
                job = self._map_row(tenant["provider"], row, tenant)
                verdict = classify_location(
                    structured_country=None, location_text=job.location,
                    search_country=country_code)
                if verdict.status == "non_eu":
                    result.stats["non_eu_rejects"] = result.stats.get("non_eu_rejects", 0) + 1
                    continue
                if verdict.quarantined:
                    result.stats["quarantined"] = result.stats.get("quarantined", 0) + 1
                    continue
                if verdict.country != country_code:
                    continue
                if job.title and job.url:
                    if self.early_filter is not None:
                        reason = self.early_filter.early_title_exclusion(job.title)
                        if reason:
                            result.stats["early_title_excluded"] = result.stats.get("early_title_excluded", 0) + 1
                            continue
                    result.jobs.append(job)
            polite_sleep(self.req_delay)
        if result.errors:
            result.status = "partial" if result.jobs else "failed"
        return result
