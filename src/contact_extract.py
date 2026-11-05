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


def _clean_phone(raw: str) -> str:
    digits = re.sub(r"\D", "", raw)
    if not 8 <= len(digits) <= 15:
        return ""
    return raw.strip(" .()/-")


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
