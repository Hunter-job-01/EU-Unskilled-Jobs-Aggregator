"""Strict-schema application-details extractor for high-priority jobs.

For jobs at or above the apply-ops gate (score >= 65), fetch the full
posting page through an injected fetcher and extract only facts that are
literally present on the page. Contacts are never guessed: an email is
reported only when it appears in the page text; a WhatsApp route only
from a verified wa.me / api.whatsapp.com link (route_classifier rules);
the application method always carries its matched evidence so the
approval card can show WHY. Offer points are mention-level facts from
structured job fields - never benefit claims.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable

from .route_classifier import Route, classify_route

FetchFn = Callable[[str], str]  # url -> page text (tests inject canned pages)

GATE = 65  # apply-ops extraction gate: only high-priority jobs are fetched

_METHOD = {
    Route.EMAIL: "direct_email",
    Route.WHATSAPP: "whatsapp_or_phone",
    Route.WEBFORM: "web_form",
}


@dataclass
class Extraction:
    eligible: bool = False
    reason: str = ""
    application_method: str = ""      # direct_email | whatsapp_or_phone | web_form | ""
    apply_email: str = ""
    apply_phone_whatsapp: str = ""
    apply_form_url: str = ""
    employer_language: str = ""
    key_offer_points: dict = field(default_factory=dict)
    evidence: str = ""
    page_text: str = ""  # the fetched posting text (for fact extraction)


def _detect_language(text: str) -> str:
    try:
        from langdetect import DetectorFactory, detect
        DetectorFactory.seed = 0  # deterministic; langdetect is unseeded by default
        # Portal chrome (menus, cookie bars) is often English and can fill
        # the head of the page; detect on the longest substantial block -
        # the job body - before falling back to the raw head.
        blocks = sorted(
            (b.strip() for b in text.split("\n") if len(b.strip()) >= 200),
            key=len, reverse=True)
        if blocks:
            return detect(blocks[0][:4000]) or ""
        return detect(text[:2000]) or ""
    except Exception:
        return ""


def _offer_points(job) -> dict:
    """Mention-level facts only, straight from structured job fields."""
    points = {}
    signals = set(getattr(job, "signals", []) or [])
    if "housing_keyword_mention" in signals:
        points["housing"] = "mentioned in posting text - verify directly"
    if "permit_keyword_mention" in signals:
        points["work_permit"] = "mentioned in posting text - verify directly"
    if "no_exp_or_training_mention" in signals:
        points["training_or_no_experience"] = "mentioned in posting text - verify directly"
    salary = getattr(job, "salary_text", "") or ""
    if salary:
        points["salary"] = salary  # verbatim as stated
    return points


def extract_application_details(job, fetch: FetchFn) -> Extraction:
    score = getattr(job, "score", 0) or 0
    if score < GATE:
        return Extraction(reason="below apply-ops gate (score {} < {})".format(score, GATE))
    url = getattr(job, "url", "") or ""
    if not url:
        return Extraction(reason="no posting URL")
    try:
        text = fetch(url) or ""
    except Exception as exc:
        return Extraction(reason="fetch failed: {}".format(type(exc).__name__))
    if len(text.strip()) < 40:
        return Extraction(reason="page text empty or too short to trust")
    result = Extraction(eligible=True,
                        employer_language=_detect_language(text),
                        page_text=text,
                        key_offer_points=_offer_points(job))
    route = classify_route(text, url)
    if not route:
        result.reason = "no contactable application route found on page"
        return result
    result.application_method = _METHOD[route.route]
    result.evidence = route.evidence
    if route.route is Route.EMAIL:
        result.apply_email = route.contact
    elif route.route is Route.WHATSAPP:
        result.apply_phone_whatsapp = route.contact  # verified wa.me format only
    else:
        result.apply_form_url = route.contact
    result.reason = "route from page evidence"
    return result
