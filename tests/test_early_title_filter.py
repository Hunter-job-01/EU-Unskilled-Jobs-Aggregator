"""Early title-only exclusion in the ats_public and arbeitnow adapters.

The filter drops technical/office titles before any scoring or
translation work; it mirrors the scorer's hard rules on the title alone.
"""
import copy

from src.scrapers import ArbeitnowScraper, AtsPublicScraper
from test_expansion_adapters import FakeHttp, enable, ARBEITNOW_PAGE, GREENHOUSE


def _ats_cfg(cfg, tenants):
    return enable(cfg, "ats_public", tenants=tenants)


# ------------------------- KeywordMatrix verdict ----------------------
def test_early_title_verdict(matrix):
    assert matrix.early_title_exclusion("Senior Software Engineer") is not None
    assert matrix.early_title_exclusion("Financial Accountant") is not None
    assert matrix.early_title_exclusion("Magazynier") is None
    assert matrix.early_title_exclusion("Warehouse worker") is None
    assert matrix.early_title_exclusion("") is None
    assert matrix.early_title_exclusion("   ") is None


def test_early_title_verdict_disqualifier(matrix):
    assert matrix.early_title_exclusion("Warehouse role - only EU citizens may apply") is not None


# ----------------------------- arbeitnow ------------------------------
def _de_page_with_tech():
    page = copy.deepcopy(ARBEITNOW_PAGE)
    page["data"].append(
        {"slug": "se-berlin", "company_name": "Tech GmbH", "title": "Senior Software Engineer",
         "description": "x", "remote": False, "url": "https://x/se",
         "location": "Berlin, Germany", "created_at": 1762000000})
    return page


def test_arbeitnow_drops_technical_titles_early(cfg):
    cfg = enable(cfg, "arbeitnow", max_pages=1)
    scraper = ArbeitnowScraper(cfg, FakeHttp([_de_page_with_tech()]))
    scraper.req_delay = (0, 0)
    result = scraper.scrape_country("DE", 10)
    assert [j.title for j in result.jobs] == ["Packer"]
    assert result.stats["early_title_excluded"] == 1


def test_arbeitnow_early_filter_can_be_disabled(cfg):
    cfg = enable(cfg, "arbeitnow", max_pages=1)
    cfg["scoring"]["early_title_filter"] = False
    scraper = ArbeitnowScraper(cfg, FakeHttp([_de_page_with_tech()]))
    scraper.req_delay = (0, 0)
    result = scraper.scrape_country("DE", 10)
    assert "Senior Software Engineer" in [j.title for j in result.jobs]
    assert "early_title_excluded" not in result.stats


# ----------------------------- ats_public -----------------------------
def test_ats_public_drops_technical_titles_early(cfg):
    page = copy.deepcopy(GREENHOUSE)
    page["jobs"].append({"id": 2, "title": "Engineering Manager",
                         "location": {"name": "Wroclaw, Poland"},
                         "absolute_url": "https://boards.greenhouse.io/acme/jobs/2",
                         "content": "<p>x</p>", "updated_at": "2026-10-30T09:00:00"})
    cfg = _ats_cfg(cfg, [{"provider": "greenhouse", "tenant": "acme", "country": "PL",
                          "employer": "Acme", "enabled": True,
                          "evidence_url": "https://boards.greenhouse.io/acme"}])
    scraper = AtsPublicScraper(cfg, FakeHttp([page]))
    scraper.req_delay = (0, 0)
    result = scraper.scrape_country("PL", 10)
    assert [j.title for j in result.jobs] == ["Warehouse Operative"]
    assert result.stats["early_title_excluded"] == 1
