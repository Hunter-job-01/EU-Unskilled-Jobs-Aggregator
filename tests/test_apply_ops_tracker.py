"""Tracker persistence contract: private, evidence-based state machine."""
import json

import pytest

from src.apply_ops.hitl import CardDecision
from src.apply_ops.tracker import (STATUS_APPLIED, STATUS_PENDING,
                                   STATUS_SKIPPED, ApplicationTracker,
                                   TrackerEntry, TrackerError)


def entry(uid="u1"):
    return TrackerEntry(uid=uid, company="Acme", title_ar="عامل مخزن",
                        country="PL", method="direct_email",
                        contact_target="hr@acme.example")


def test_roundtrip_persistence_and_gitignore(tmp_path):
    t = ApplicationTracker(tmp_path / "data" / "applications_tracker.json")
    t.add_pending(entry())
    t.save()
    t2 = ApplicationTracker(tmp_path / "data" / "applications_tracker.json")
    assert t2.get("u1").company == "Acme" and t2.get("u1").status == STATUS_PENDING
    gitignore = open(".gitignore", encoding="utf-8").read()
    assert "data/applications_tracker.json" in gitignore  # never committed


def test_add_pending_never_overwrites_outcome(tmp_path):
    t = ApplicationTracker(tmp_path / "t.json")
    t.add_pending(entry())
    t.record_decision("u1", CardDecision.APPROVE, proof="gmail msg 123")
    again = t.add_pending(entry())  # same uid re-issued card
    assert again.status == STATUS_APPLIED  # not reset to pending


def test_applied_requires_proof_and_stamps_time(tmp_path):
    t = ApplicationTracker(tmp_path / "t.json")
    t.add_pending(entry())
    with pytest.raises(TrackerError):
        t.record_decision("u1", CardDecision.APPROVE, proof="  ")
    e = t.record_decision("u1", CardDecision.APPROVE, proof="gmail msg 123")
    assert e.status == STATUS_APPLIED and e.applied_at.endswith("Z") and e.proof


def test_decisions_on_non_pending_rejected(tmp_path):
    t = ApplicationTracker(tmp_path / "t.json")
    t.add_pending(entry())
    t.record_decision("u1", CardDecision.SKIP)
    with pytest.raises(TrackerError):
        t.record_decision("u1", CardDecision.APPROVE, proof="x")
    with pytest.raises(TrackerError):
        t.record_decision("ghost", CardDecision.SKIP)


def test_edit_leaves_pending_and_skip_marks(tmp_path):
    t = ApplicationTracker(tmp_path / "t.json")
    t.add_pending(entry())
    e = t.record_decision("u1", CardDecision.EDIT)
    assert e.status == STATUS_PENDING
    t.add_pending(entry("u2"))
    t.record_decision("u2", CardDecision.SKIP)
    assert [x.uid for x in t.by_status(STATUS_SKIPPED)] == ["u2"]
    assert [x.uid for x in t.by_status(STATUS_PENDING)] == ["u1"]


def test_tracker_file_has_no_message_bodies(tmp_path):
    t = ApplicationTracker(tmp_path / "t.json")
    t.add_pending(entry())
    t.record_decision("u1", CardDecision.APPROVE, proof="gmail msg 123")
    t.save()
    raw = json.loads((tmp_path / "t.json").read_text(encoding="utf-8"))
    allowed = {"uid", "company", "title_ar", "country", "method",
               "contact_target", "status", "applied_at", "proof",
               "exclusion_reason"}
    assert set(raw["entries"]["u1"]) == allowed


def test_not_eligible_terminal(tmp_path):
    from src.apply_ops.tracker import (STATUS_NOT_ELIGIBLE, ApplicationTracker,
                                       TrackerEntry, TrackerError)
    from src.apply_ops.hitl import CardDecision
    tr = ApplicationTracker(tmp_path / "t.json")
    e = tr.add_not_eligible(TrackerEntry(
        uid="u9", company="SYNERGIE", title_ar="", country="DE",
        method="direct_email", contact_target="buchholz-jobs@synergie.de",
        status=STATUS_NOT_ELIGIBLE),
        reason="requires good German (Gute Deutschkenntnisse); candidate Arabic native / English A2-B1")
    assert e.status == "not_eligible"
    assert "German" in e.exclusion_reason
    tr.save()
    tr2 = ApplicationTracker(tmp_path / "t.json")
    assert tr2.entries["u9"].status == "not_eligible"
    try:
        tr2.record_decision("u9", CardDecision.APPROVE, proof="x")
        assert False, "must reject decisions on terminal entries"
    except TrackerError:
        pass
    try:
        tr2.add_not_eligible(TrackerEntry(
            uid="u10", company="X", title_ar="", country="DE",
            method="direct_email", contact_target="x@y.de",
            status=STATUS_NOT_ELIGIBLE), reason="")
        assert False, "reason required"
    except TrackerError:
        pass


def test_eligibility_unresolved_hold(tmp_path):
    from src.apply_ops.tracker import (STATUS_ELIGIBILITY_UNRESOLVED,
                                       ApplicationTracker, TrackerError)
    from src.apply_ops.hitl import CardDecision
    tr = ApplicationTracker(tmp_path / "t.json")
    tr.add_pending(entry())
    e = tr.mark_eligibility_unresolved("u1", "form screens right-to-work; sponsorship unresolved")
    assert e.status == "eligibility_unresolved"
    assert "sponsorship" in e.exclusion_reason
    try:
        tr.record_decision("u1", CardDecision.APPROVE, proof="x")
        assert False, "held entries reject decisions"
    except TrackerError:
        pass
    tr.save()
    tr2 = ApplicationTracker(tmp_path / "t.json")
    assert tr2.entries["u1"].status == STATUS_ELIGIBILITY_UNRESOLVED
    try:
        tr2.mark_eligibility_unresolved("u1", "")
        assert False, "reason required"
    except TrackerError:
        pass
