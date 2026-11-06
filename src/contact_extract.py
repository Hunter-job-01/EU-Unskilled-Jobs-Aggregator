"""v6 stage C (L2): application-channel extraction from posting text.

Data-only: pulls direct recruiter emails and phone numbers already
present in a posting's description into structured fields. Never used
for outreach from this pipeline - employer contact flows are owned
elsewhere. A posting with no direct channel keeps the source URL as its
web-form route; nothing here fabricates a channel.
"""
from __future__ import annotations

import re

EMAIL_RX = re.compile(r"[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}", re.IGNORECASE)
# International-ish phone: optional +, country/area digits, separators,
# 8-15 significant digits total.
PHONE_RX = re.compile(r"\+?[0-9][0-9 .()/-]{7,18}[0-9]")
_WHATSAPP_HINT = re.compile(r"\bwhats\s?app\b", re.IGNORECASE)


# Date-like shapes (26.11.2026, 05-10.11/16-19.11), shift ranges
# (08.30 - 17.00) and thousands-salary figures (23.000 - 28.000) matched
# the old phone pattern and were persisted as recruiter phones on
# 2026-11-05/06 (113 stored records affected). A phone is never a dotted
# date/time group, a slash date, or a multi-slash shift list - but real
# EU phones DO use a single slash (0630/674-3549, 05741/ 3485 - 0), so
# one slash with enough digits stays.
_DATE_SLASH = re.compile(r"^\d{1,2}/\d{1,2}/\d{2,4}$")
_DOTTED = re.compile(r"\d\.\d")
_FR_MOBILE = re.compile(r"0\d(\.\d{2}){4}")


def _clean_phone(raw: str) -> str:
    cand = raw.strip(" .()/-")
    digits = re.sub(r"\D", "", cand)
    if not 9 <= len(digits) <= 15:
        return ""
    if cand.count("/") > 1:
        return ""  # shift list or dd/mm/yyyy
    if _DATE_SLASH.fullmatch(cand):
        return ""
    if _DOTTED.search(cand):
        # Dotted groups are dates, times or thousands separators - never
        # phones in the observed sources, except the French 0X.XX.XX.XX.XX
        # mobile shape.
        if not _FR_MOBILE.fullmatch(cand):
            return ""
    return cand


def extract_channels(text: str) -> dict:
    """Extract direct application channels from posting text."""
    out = {"emails": [], "phones": [], "whatsapp_mentioned": False}
    if not text:
        return out
    for m in EMAIL_RX.finditer(text):
        addr = m.group(0).lower()
        if addr not in out["emails"]:
            out["emails"].append(addr)
    for m in PHONE_RX.finditer(text):
        num = _clean_phone(m.group(0))
        if num and num not in out["phones"]:
            out["phones"].append(num)
    out["whatsapp_mentioned"] = bool(_WHATSAPP_HINT.search(text))
    return out


def has_direct_channel(channels: dict) -> bool:
    return bool(channels.get("emails") or channels.get("phones"))
