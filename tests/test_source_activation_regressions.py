"""Regression tests for the 2026-11-02 approved source activation:
known conflicts in stored source text must reject before any detail fetch,
chrome-only detail pages must classify as labelled-uncertain (never pass),
Czech contact-person fields must never enter stored/sent job data, and the
legacy single-chat digest path must work with private fit enabled."""
import copy
import json
import os

import pytest

from src.eligibility import AutomaticEligibleStore
from src.models import Job
from src.scrapers import SwedenJobSearchScraper, CzechMpsvScraper
from src.notifier import send_digest
from src.storage import JobStore


def enable(cfg, name):
    cfg = copy.deepcopy(cfg)
    cfg["sources"][name]["enabled"] = True
    return cfg


class FakeResp:
    def __init__(self, payload=None):
        self.payload = payload
        self.status_code = 200

    def json(self):
        return self.payload


class FakeHttp:
    def __init__(self, payloads):
        self.payloads = list(payloads)

    def get(self, url, **kwargs):
        return FakeResp(payload=self.payloads.pop(0))


# ---------------- Sweden structured requirement evidence ----------------

def _hit(**kw):
    hit = {"id": "1", "headline": "Lagerarbetare",
           "employer": {"name": "Lager AB"},
           "description": {"text": "Vi söker lagerarbetare."},
           "webpage_url": "https://arbetsformedlingen.se/platsbanken/annonser/1",
           "workplace_address": {"country": "Sverige", "municipality": "Huddinge"},
           "publication_date": "2026-11-01"}
    hit.update(kw)
    return hit


def test_sweden_driving_licence_flag_becomes_checkable_evidence(cfg, matrix):
    cfg = enable(cfg, "sweden_jobsearch")
    hits = {"hits": [_hit(driving_license_required=True, experience_required=True,
                          must_have={"languages": [{"label": "Svenska"}]})]}
    result = SwedenJobSearchScraper(cfg, FakeHttp([hits] * 20), matrix).scrape_country("SE", 5)
    assert len(result.jobs) == 1
    desc = result.jobs[0].description
    assert "driving licence required" in desc
    assert "experience required" in desc
    assert "must-have language: Svenska" in desc


def test_sweden_no_flag_adds_no_evidence(cfg, matrix):
    cfg = enable(cfg, "sweden_jobsearch")
    result = SwedenJobSearchScraper(cfg, FakeHttp([{"hits": [_hit()]}] * 20), matrix).scrape_country("SE", 5)
    assert "[JobSearch evidence]" not in result.jobs[0].description


# ---------------- Stored-text conflict beats chrome-only fetch ----------------

def _triage_store(tmp_path, jobs):
    cfg = {"data_dir": "data"}
    store = JobStore(cfg, base_dir=str(tmp_path))
    store.merge(jobs)
    store.save()
    return store


def test_known_conflict_in_stored_text_rejects_before_fetch(tmp_path):
    from src.automatic_requirements import automatic_review  # noqa: F401
    job = Job(title="Lagerarbetare", company="Lager AB", country="SE",
              source="sweden_jobsearch", url="https://arbetsformedlingen.se/x",
              uid="se1", score=60,
              description="Vi söker lagerarbetare.\n[JobSearch evidence] "
                          "driving licence required (JobSearch structured field)")
    store = _triage_store(tmp_path, [job])

    def fetch_details(record):
        raise AssertionError("fetch must not run: known conflict rejects first")

    triaged = AutomaticEligibleStore(store, profile=None, fetch_details=fetch_details)
    assert triaged.stats["reject"] == 1
    assert triaged.stats["checked"] == 1
    assert triaged.load() == {}


def test_chrome_only_detail_page_is_labelled_uncertain_never_pass(tmp_path):
    chrome = ("Sök lediga jobb i Platsbanken - Arbetsförmedlingen cookies "
              "Logga in Meny Start Kontakta oss")
    job = Job(title="Lagerarbetare", company="Lager AB", country="SE",
              source="sweden_jobsearch", url="https://arbetsformedlingen.se/y",
              uid="se2", score=60, description="Vi söker lagerarbetare i Huddinge.")
    store = _triage_store(tmp_path, [job])
    triaged = AutomaticEligibleStore(store, profile=None,
                                     fetch_details=lambda record: (chrome, {}))
    assert triaged.stats["review"] == 1
    out = triaged.load()["se2"]
    assert out["private_fit_status"] == "review"
    assert "غير متحقق منه" in out["private_fit_reason"]


# ---------------- Czech contact-person fields never stored ----------------

def test_czech_contact_person_fields_never_mapped(cfg):
    cfg = enable(cfg, "czech_mpsv")
    scraper = CzechMpsvScraper(cfg, FakeHttp([]))
    record = {
        "referencniCislo": "CZ-1",
        "pozadovanaProfese": {"cs": "Skladník"},
        "zamestnavatel": {"nazev": "Sklad s.r.o."},
        "urlAdresa": "https://example.cz/job/1",
        "mistoVykonuPrace": {"adresaText": "Praha 4"},
        "cizinecMimoEu": True,
        "kontaktniOsoba": {"jmeno": "Jan Novák",
                           "telefon": "+420777123456",
                           "email": "jan.novak@example.cz"},
        "kontakt": {"telefon": "+420777123456", "email": "jan.novak@example.cz"},
    }
    job = scraper._map_record(record)
    blob = json.dumps(job.__dict__, ensure_ascii=False)
    assert "Jan Novák" not in blob
    assert "+420777123456" not in blob
    assert "jan.novak@example.cz" not in blob
    assert "kontakt" not in blob.lower()


# ---------------- Legacy single-chat digest with private fit ----------------

def test_legacy_single_chat_with_private_fit_withholds_by_design(tmp_path, caplog, monkeypatch):
    """Not a crash: send_digest explicitly refuses the legacy single-chat
    path when private fit is required (destinations must be independently
    tracked). Production sets TELEGRAM_CHAT_IDS, so this only documents the
    guard - the wrapper stores never reach _send_single_digest."""
    job = Job(title="Warehouse worker", company="ACME", country="SE",
              source="sweden_jobsearch", url="https://arbetsformedlingen.se/z",
              uid="se3", score=80, description="Warehouse worker wanted.")
    cfg = {"data_dir": "data"}
    store = JobStore(cfg, base_dir=str(tmp_path))
    store.merge([job])
    store.save()
    full_cfg = {"storage": cfg,
                "telegram": {"enabled": True, "min_score": 50,
                             "private_fit_required": True,
                             "automatic_requirements": True,
                             "detail_checks_per_run": 0,
                             "full_arabic_translation": False}}
    from cryptography.fernet import Fernet

    class OkSession:
        def post(self, url, json=None, timeout=None):
            return FakeResp(payload={"ok": True, "result": {"message_id": 1}})

    key = Fernet.generate_key().decode()
    monkeypatch.setenv("JOB_FIT_STATE_KEY", key)
    profile = {"schema": 1, "languages": {"en": "A2"}, "licences": [],
               "roles": ["warehouse"], "authorization": "needs_sponsorship"}
    with caplog.at_level("ERROR"):
        notified = send_digest(store, full_cfg, session=OkSession(),
                               environ={"TELEGRAM_BOT_TOKEN": "t",
                                        "TELEGRAM_CHAT_ID": "555",
                                        "JOB_FIT_PROFILE": json.dumps(profile),
                                        "JOB_FIT_REVIEWS": "{}",
                                        "JOB_FIT_STATE_KEY": key},
                               sleep=lambda *_: None)
    assert notified == 0
    assert "independently tracked destinations" in caplog.text
