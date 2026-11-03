"""Scraper package: EURES (primary), JobSpy/Indeed (secondary) and the
opt-in expansion adapters (all disabled by default)."""
from .base import HttpClient, ScrapeResult, ScraperError, polite_sleep, strip_html
from .eures_scraper import EuresScraper
from .jobspy_scraper import JobSpyScraper
from .arbeitnow import ArbeitnowScraper
from .sweden_jobsearch import SwedenJobSearchScraper
from .czech_mpsv import CzechMpsvScraper
from .latvia_nva import LatviaNvaScraper
from .ats_public import AtsPublicScraper
from .ottoworkforce import OttoWorkforceScraper
from .anyworkanywhere import AnyworkAnywhereScraper
from .pl_cbop import PlCbopScraper
from .nl_jobs import NlJobsScraper
from .hobij import HobijScraper
from .covebo import CoveboScraper
from .ab_midden import AbMiddenScraper
from .goodmorning import GoodmorningScraper

__all__ = [
    "HttpClient", "ScrapeResult", "ScraperError", "polite_sleep", "strip_html",
    "EuresScraper", "JobSpyScraper",
    "ArbeitnowScraper", "SwedenJobSearchScraper", "CzechMpsvScraper",
    "LatviaNvaScraper", "AtsPublicScraper", "OttoWorkforceScraper", "AnyworkAnywhereScraper", "PlCbopScraper", "NlJobsScraper", "HobijScraper", "CoveboScraper", "AbMiddenScraper", "GoodmorningScraper",
]
