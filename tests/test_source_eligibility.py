"""Source-eligibility labelling: operator restriction excludes, unknown
labels without score change, technical liveness never implies eligibility."""
from src.models import Job
from src.scorer import Scorer
from src.source_eligibility import apply_to_job, source_eligibility

REG = {"sources": [
    {"source_id": "pl_ewl", "category": "agency", "applicant_from_egypt": "restricted_non_eu_resident_current"},
    {"source_id": "pl_cbop", "category": "official", "applicant_from_egypt": "unknown_not_assessed"},
    {"source_id": "nl_otto_workforce", "category": "agency", "applicant_from_egypt": "unknown_not_assessed"},
]}


def _job(source):
    return Job(title="Warehouse employee", company="X", country="PL",
               source=source, url="https://x/1",
               description="warehouse packing work, no experience needed")


def test_restricted_source_excludes():
    job = _job("pl_ewl")
    apply_to_job(job, REG)
    assert job.excluded
    assert "does not serve applicants residing outside" in job.exclusion_reason
    assert "source_eligibility_restricted" in job.signals


def test_unknown_source_labelled_not_penalized(cfg):
    job = _job("pl_cbop")
    apply_to_job(job, REG)
    assert not job.excluded
    assert "source_eligibility_unknown" in job.signals


def test_unlisted_source_untouched():
    job = _job("some_new_board")
    apply_to_job(job, REG)
    assert not job.excluded
    assert job.signals == []


def test_scorer_applies_registry_without_score_change(cfg):
    scorer = Scorer(cfg, registry=REG)
    job = _job("pl_cbop")
    scorer.score(job)
    assert "source_eligibility_unknown" in job.signals
    # unknown eligibility must not change the numeric score
    scorer_plain = Scorer(cfg)
    job2 = _job("pl_cbop")
    scorer_plain.score(job2)
    assert job.score == job2.score
    # and a restricted source ends excluded through the scorer too
    job3 = _job("pl_ewl")
    scorer.score(job3)
    assert job3.excluded


def test_registry_lookup():
    assert source_eligibility(REG, "pl_ewl").startswith("restricted")
    assert source_eligibility(REG, "missing") == ""


def test_agency_origin_boost(cfg):
    # agency-origin jobs get the agency signal + points WITHOUT any permit claim
    scorer = Scorer(cfg, registry=REG)
    job = _job("nl_otto_workforce")
    job.country = "NL"
    scorer.score(job)
    assert "employment_agency_source" in job.signals
    assert "source_eligibility_unknown" in job.signals
    # official (PES) origin does NOT get the agency boost
    job2 = _job("pl_cbop")
    scorer.score(job2)
    assert "employment_agency_source" not in job2.signals
    assert "agency_relocation_text_mention" not in job2.signals
    # and the boost is real points
    scorer_plain = Scorer(cfg)
    job3 = _job("nl_otto_workforce")
    job3.country = "NL"
    scorer_plain.score(job3)
    assert job.score > job3.score
