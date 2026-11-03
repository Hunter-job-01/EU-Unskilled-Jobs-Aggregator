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
_FUSED_TLD_RE = re.compile(r"(\.[a-z]{2,10})[A-Z]")
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
    m = _EMAIL_RE.search(text)
    if m:
        email = m.group(0)
        # Scraped text can fuse the email with the next word
        # ("bewerbung@spp-direkt.deWhatsApp:"); TLDs are lowercase, so cut
        # a capitalised tail fused onto the final label.
        fused = _FUSED_TLD_RE.search(email, email.rfind("@"))
        if fused:
            email = email[: fused.start(1)] + fused.group(1)
        return RouteResult(Route.EMAIL, email, "email in posting: " + email)
    # WhatsApp route ONLY from an explicit wa.me / api.whatsapp.com link
    # with a valid international-format number (6-15 digits). A bare phone
    # number next to the word "whatsapp" is NOT proof the number has
    # WhatsApp and is never routed as one.
    m = _WAME_RE.search(text)
    if m and m.group(1).isdigit() and 6 <= len(m.group(1)) <= 15:
        return RouteResult(Route.WHATSAPP, m.group(1), "wa.me link in posting (verified format)")
    for m in _APPLY_LINK_RE.finditer(text):
        url = m.group(0).rstrip(".,);]")
        if "apply" in url.lower() or "career" in url.lower() or "vacanc" in url.lower():
            return RouteResult(Route.WEBFORM, url, "apply link in posting",
                               portal_precheck_required=True)
    if page_url:
        return RouteResult(Route.WEBFORM, page_url, "posting page (no verified direct contact)",
                           portal_precheck_required=True)
    return None
