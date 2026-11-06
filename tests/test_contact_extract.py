"""v6 stage C (L2) contact/channel extraction. Self-contained."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.contact_extract import extract_channels, has_direct_channel  # noqa: E402


def test_extracts_email_and_phone():
    out = extract_channels(
        "Apply via jobs@example-logistics.pl or call +48 123 456 789.")
    assert out["emails"] == ["jobs@example-logistics.pl"]
    assert out["phones"] == ["+48 123 456 789"]
    assert has_direct_channel(out)


def test_whatsapp_flag_and_dedup():
    out = extract_channels(
        "WhatsApp us on +31 6 1234 5678. Write to a@b.example and a@b.example.")
    assert out["whatsapp_mentioned"] is True
    assert out["emails"] == ["a@b.example"]
    assert out["phones"] == ["+31 6 1234 5678"]


def test_dates_and_ranges_are_not_phones():
    # Regression 2026-11-06: these persisted as recruiter phones on a
    # telegram record before the fix.
    out = extract_channels("Приїзд 05-10.11/16-19.11. Вихід 26.11.2026.")
    assert out["phones"] == []
    assert extract_channels("26.11.2026")["phones"] == []
    assert extract_channels("05.10.2026")["phones"] == []


def test_real_phones_still_extracted():
    out = extract_channels("Call +48 123 456 789 or +380936408993.")
    assert out["phones"] == ["+48 123 456 789", "+380936408993"]
    assert extract_channels("tel. +48791123543")["phones"] == ["+48791123543"]
    # Single-slash EU formats are real phones and must survive.
    assert extract_channels("Tel: 0630/674-3549")["phones"] == ["0630/674-3549"]
    assert extract_channels("Tel. 05741/ 3485 - 0")["phones"] == ["05741/ 3485 - 0"]
    assert extract_channels("0 38 31 / 28 81 - 0")["phones"] == ["0 38 31 / 28 81 - 0"]


def test_no_channel_means_empty():
    out = extract_channels("Warehouse work with housing and training.")
    assert out == {"emails": [], "phones": [], "whatsapp_mentioned": False}
    assert not has_direct_channel(out)
    assert extract_channels("")["emails"] == []
