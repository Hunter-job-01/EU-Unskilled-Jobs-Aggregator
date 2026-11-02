"""Scraper adapters. Each scraper is isolated: one source failing must
never crash the run for other sources or countries."""
from .base import HttpClient, ScrapeResult, ScraperError, polite_sleep
from .eures_scraper import EuresScraper
from .jobspy_scraper import JobSpyScraper

__all__ = [
    "HttpClient",
    "ScrapeResult",
    "ScraperError",
    "polite_sleep",
    "EuresScraper",
    "JobSpyScraper",
]
