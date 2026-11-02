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

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
# WhatsApp signals: wa.me links or explicit "whatsapp" near a phone number
_WAME_RE = re.compile(r"(?:wa\.me/|api\.whatsapp\.com/send\?phone=)(\d{6,15})", re.I)
_WHATSAPP_HINT_RE = re.compile(r"whatsapp[^0-9+]{0,40}(\+?\d[\d\s().-]{7,14}\d)", re.I)
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


def classify_route(text: str, page_url: str = "") -> "RouteResult | None":
    """Pick the application route from posting text (+ its own URL).

    Priority: explicit email > explicit WhatsApp > apply link in text >
    the posting page itself as a web form. Returns None when nothing
    contactable is found (card must not be built).
    """
    text = text or ""
    m = _EMAIL_RE.search(text)
    if m:
        return RouteResult(Route.EMAIL, m.group(0), "email in posting: " + m.group(0))
    m = _WAME_RE.search(text)
    if m:
        return RouteResult(Route.WHATSAPP, m.group(1), "wa.me link in posting")
    m = _WHATSAPP_HINT_RE.search(text)
    if m:
        return RouteResult(Route.WHATSAPP, re.sub(r"[\s().-]", "", m.group(1)),
                           "whatsapp mention in posting")
    for m in _APPLY_LINK_RE.finditer(text):
        url = m.group(0).rstrip(".,);]")
        if "apply" in url.lower() or "career" in url.lower() or "vacanc" in url.lower():
            return RouteResult(Route.WEBFORM, url, "apply link in posting")
    if page_url:
        return RouteResult(Route.WEBFORM, page_url, "posting page (no direct contact)")
    return None
