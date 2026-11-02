"""Extractor + discovery contract tests: evidence-based, never guessed."""
from types import SimpleNamespace

from src.apply_ops.extractor import GATE, extract_application_details
from src.apply_ops.discovery import find_official_page


def job(score=70, url="https://employer.example/job/1", signals=(), salary=""):
    return SimpleNamespace(score=score, url=url, signals=list(signals), salary_text=salary)


PAGE_EMAIL = ("Magazynier needed. Apply by email to hr@acme-logistics.pl "
              "with your CV. Salary 4500 PLN gross monthly. " * 3)
PAGE_WA = ("Warehouse job. Contact us via https://wa.me/48123456789 for details. " * 4)
PAGE_FORM = ("Great job. Apply at https://acme-logistics.pl/careers/apply-now today. " * 3)
PAGE_NONE = ("General marketing copy without any contact or apply route. " * 4)


def test_below_gate_never_fetches():
    called = []
    r = extract_application_details(job(score=GATE - 1), lambda u: called.append(u) or PAGE_EMAIL)
    assert not r.eligible and called == [] and "gate" in r.reason


def test_fetch_failure_and_empty_page_fail_closed():
    def boom(u): raise TimeoutError()
    r = extract_application_details(job(), boom)
    assert not r.eligible and "fetch failed" in r.reason
    r2 = extract_application_details(job(), lambda u: "short")
    assert not r2.eligible and "empty" in r2.reason


def test_email_route_literal_only():
    r = extract_application_details(job(), lambda u: PAGE_EMAIL)
    assert r.eligible and r.application_method == "direct_email"
    assert r.apply_email == "hr@acme-logistics.pl"
    assert "email in posting" in r.evidence


def test_whatsapp_route_only_from_verified_wame_link():
    r = extract_application_details(job(), lambda u: PAGE_WA)
    assert r.application_method == "whatsapp_or_phone"
    assert r.apply_phone_whatsapp == "48123456789"
    # a bare phone number next to the word whatsapp is NOT routed
    bare = "Call or whatsapp +48 123 456 789 for this job. " * 4
    r2 = extract_application_details(job(url="https://x.example/j"), lambda u: bare)
    assert r2.application_method != "whatsapp_or_phone"


def test_webform_route_and_no_route():
    r = extract_application_details(job(), lambda u: PAGE_FORM)
    assert r.application_method == "web_form" and r.apply_form_url.startswith("https://")
    r2 = extract_application_details(job(url=""), lambda u: PAGE_NONE)
    assert not r2.eligible and "no posting URL" in r2.reason
    r3 = extract_application_details(job(), lambda u: PAGE_NONE)
    # page URL itself may serve as web form; otherwise no route - either
    # way the method must come from evidence, never a guess
    assert r3.application_method in ("web_form", "")
    if r3.application_method == "":
        assert "no contactable" in r3.reason


def test_offer_points_are_mention_level_and_verbatim():
    j = job(signals=("housing_keyword_mention", "permit_keyword_mention"), salary="14.99 EUR/h")
    r = extract_application_details(j, lambda u: PAGE_EMAIL)
    assert r.key_offer_points["salary"] == "14.99 EUR/h"
    assert "verify" in r.key_offer_points["housing"]
    assert "provided" not in str(r.key_offer_points).lower()


def test_discovery_skips_aggregators_and_ranks_official():
    def search(q):
        return ["https://pl.indeed.com/acme-job", "https://acme-logistics.pl/praca",
                "https://glassdoor.com/acme", "https://unrelated.example/x"]
    r = find_official_page("Acme Logistics", "PL", search)
    assert r.chosen_url == "https://acme-logistics.pl/praca"
    assert r.confidence in ("high", "medium")
    assert all("indeed" not in c and "glassdoor" not in c for c in r.candidates)


def test_discovery_fail_closed_on_weak_match_and_errors():
    r = find_official_page("Acme Logistics", "PL", lambda q: ["https://zzz-other.example/jobs"])
    assert not r.chosen_url and r.confidence == "none"
    r2 = find_official_page("Acme", "PL", lambda q: (_ for _ in ()).throw(RuntimeError()))
    assert not r2.chosen_url and "search failed" in r2.reason
    r3 = find_official_page("Acme", "PL", lambda q: [])
    assert not r3.chosen_url and "no non-aggregator" in r3.reason
