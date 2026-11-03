"""Classify a vacancy into its application route.

Route A: direct employer/agency email found in the posting text.
Route B: WhatsApp contact found in the posting text.
Route C: web form / portal (the posting URL itself, or an apply link).

Classification is evidence-based: every route carries the matched
evidence string so the approval card can show WHY this route was chosen.
No route is guessed; nothing contactable yields route=None.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

# A contact address is accepted only from unambiguous evidence:
#   1. an explicit mailto: link (the page's own structured contact), or
#   2. a whitespace-delimited token that matches an email in full.
# Scraped text that fuses an address to adjacent words
# ("bewerbung@spp-direkt.deWhatsApp:") fails CLOSED: no contact is guessed
# from ambiguous concatenation. The final label must be all-lowercase or
# all-uppercase ("de", "COM"); mixed case marks a fused word, not a TLD.
# Multi-level domains (example.co.uk), subdomains, plus-addresses and
# hyphenated labels all match.
_MAILTO_RE = re.compile(r'mailto:([^?\s"<>\']+)', re.I)
_EMAIL_TOKEN_RE = re.compile(
    r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,10}$")
_TOKEN_STRIP = '<>()"[\]{}\';,'


def _valid_email_token(token: str) -> str:
    token = token.strip(_TOKEN_STRIP).rstrip(".")
    if not _EMAIL_TOKEN_RE.match(token):
        return ""
    final_label = token.rsplit(".", 1)[1]
    if final_label.islower() or final_label.isupper():
        return token
    return ""  # mixed-case final label: fused text, fail closed
# WhatsApp signals: wa.me links or explicit "whatsapp" near a phone number
_WAME_RE = re.compile(r"(?:wa\.me/|api\.whatsapp\.com/send\?phone=)(\d{6,15})", re.I)
_APPLY_LINK_RE = re.compile(r"https?://[^\s\"'<>]+", re.I)


class Route(Enum):
    EMAIL = "A"       # direct email application
    WHATSAPP = "B"    # WhatsApp contact application
    WEBFORM = "C"     # portal / web form


@dataclass
class RouteResult:
    route: Route
    contact: str          # email address, wa.me number, or form URL
    evidence: str         # the matched text justifying the route
    portal_precheck_required: bool = False  # WEBFORM: account/autosave/disclosure check before any card action


def classify_route(text: str, page_url: str = "") -> "RouteResult | None":
    """Pick the application route from posting text (+ its own URL).

    Priority: explicit email > explicit WhatsApp > apply link in text >
    the posting page itself as a web form. Returns None when nothing
    contactable is found (card must not be built).
    """
    text = text or ""
    m = _MAILTO_RE.search(text)
    if m:
        email = _valid_email_token(m.group(1))
        if email:
            return RouteResult(Route.EMAIL, email, "mailto link: " + email)
    for token in text.split():
        email = _valid_email_token(token)
        if email and "@" in email:
            return RouteResult(Route.EMAIL, email, "email in posting: " + email)
    # WhatsApp route ONLY from an explicit wa.me / api.whatsapp.com link
    # with a valid international-format number (6-15 digits). A bare phone
    # number next to the word "whatsapp" is NOT proof the number has
    # WhatsApp and is never routed as one.
    m = _WAME_RE.search(text)
    if m and m.group(1).isdigit() and 6 <= len(m.group(1)) <= 15:
        return RouteResult(Route.WHATSAPP, m.group(1), "wa.me link in posting (verified format)")
    # EURES "Application Details: <url>" is a labeled employer-application
    # field - the external form named by the posting itself.
    m = re.search("Application Details:\\s*(https?://[^\\s\"'<>]+)", text, re.I)
    if m:
        return RouteResult(Route.WEBFORM, m.group(1).rstrip(".,);]"),
                           "EURES Application Details field",
                           portal_precheck_required=True)
    for m in _APPLY_LINK_RE.finditer(text):
        url = m.group(0).rstrip(".,);]")
        if "apply" in url.lower() or "career" in url.lower() or "vacanc" in url.lower():
            return RouteResult(Route.WEBFORM, url, "apply link in posting",
                               portal_precheck_required=True)
    if page_url:
        return RouteResult(Route.WEBFORM, page_url, "posting page (no verified direct contact)",
                           portal_precheck_required=True)
    return None
