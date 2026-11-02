"""Glossary honesty + deterministic Arabic summary + validator."""
from src.glossary import gloss_lines, glossary_hits
from src.job_translation import (compose_summary_ar, translate_title_mt,
                                 valid_translation)
from src.models import Job


def test_glossary_honest_glosses_not_claims():
    lines = gloss_lines("Quereinsteiger willkommen im Lager", "de")
    assert len(lines) == 1
    assert "career changer" in lines[0]["en"]
    # never upgraded to a training/benefit guarantee
    assert "training provided" not in lines[0]["en"]
    assert "guarantee" not in lines[0]["en"]


def test_glossary_negation_suppressed():
    assert gloss_lines("keine Unterkunft vorhanden", "de") == []
    hits = glossary_hits("keine Unterkunft vorhanden", "de")
    assert hits and hits[0][3] is True  # reported as negated, not glossed


def test_glossary_self_negating_terms_survive():
    lines = gloss_lines("Praca bez doświadczenia, zakwaterowanie", "pl")
    terms = {l["term"] for l in lines}
    assert "bez doświadczenia" in terms
    assert "zakwaterowanie" in terms
    # accommodation gloss stays mention-level
    z = [l for l in lines if l["term"] == "zakwaterowanie"][0]
    assert "as mentioned" in z["en"]


def test_validator_rejects_bad_outputs():
    assert not valid_translation("Hallo", "")
    assert not valid_translation("Hallo", "Hallo")
    assert not valid_translation("Hallo", "As an AI language model, Hallo")
    assert valid_translation("Lagerhelfer", "Warehouse helper")


def test_summary_ar_deterministic_and_qualified():
    job = Job(title="Lagerhelfer", company="X", country="DE", source="eures", url="https://x.test")
    job.title_en = "Warehouse helper"
    job.signals = ["housing_keyword_mention", "employment_agency_source"]
    job.salary_text = "14 EUR/h"
    out = compose_summary_ar(job)
    assert "Warehouse helper" not in out  # title is never repackaged as tasks
    assert "المهام الأساسية" not in out
    assert "يذكر الإعلان: سكن" in out  # mention-level, never "housing provided"
    assert "سكن متوفر" not in out
    assert "وكالة توظيف" in out
    assert "14 EUR/h" in out
    assert "يُتحقق" in out  # verification qualifier always present


def test_summary_ar_empty_without_facts_even_with_title():
    # a bare title alone must NOT produce a fake Arabic block
    job = Job(title="Warehouse Employee", company="X", country="NL", source="nl_jobs", url="https://x.test")
    job.title_en = "Warehouse Employee"
    assert compose_summary_ar(job) == ""


def test_summary_ar_empty_without_content():
    job = Job(title="", company="X", country="DE", source="eures", url="https://x.test")
    assert compose_summary_ar(job) == ""


def test_title_mt_disabled_config_returns_none():
    assert translate_title_mt("Lagerhelfer", "ar", {"translation": {"enabled": False}}) is None
