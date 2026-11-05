"""czech_mpsv urlAdresa normalization. Self-contained."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.scrapers.czech_mpsv import CzechMpsvScraper  # noqa: E402


def test_schemeless_domain_gets_https():
    assert CzechMpsvScraper._normalized_url("www.pco.cz") == "https://www.pco.cz"
    assert CzechMpsvScraper._normalized_url("www.tibex.cz/kariera") == "https://www.tibex.cz/kariera"


def test_full_urls_pass_through():
    assert CzechMpsvScraper._normalized_url("https://x.cz/a") == "https://x.cz/a"
    assert CzechMpsvScraper._normalized_url("http://x.cz") == "http://x.cz"


def test_unusable_link_text_quarantined():
    assert CzechMpsvScraper._normalized_url("nejde o odkaz") == ""
    assert CzechMpsvScraper._normalized_url("") == ""
    assert CzechMpsvScraper._normalized_url(None) == ""
