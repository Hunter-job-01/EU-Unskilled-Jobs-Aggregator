"""Intake parsing + form-prep contract tests."""
from types import SimpleNamespace

from src.apply_ops.form_prep import evaluate_prep, plan_form_prep
from src.apply_ops.hitl import ApprovalCard, CardDecision, CardStore
from src.apply_ops.intake import apply_owner_reply, parse_owner_reply
from src.apply_ops.tracker import ApplicationTracker, TrackerEntry


def rig(tmp_path):
    store = CardStore()
    tracker = ApplicationTracker(tmp_path / "t.json")
    store.create(ApprovalCard(card_id="c1", job_uid="u1", cv_variant="warehouse-production",
                              card_text_ar="x", draft_subject="s", draft_body="b",
                              recipient="hr@x.example"))
    tracker.add_pending(TrackerEntry(uid="u1", company="Acme", title_ar="t",
                                     country="PL", method="direct_email",
                                     contact_target="hr@x.example"))
    return store, tracker


def test_parse_ascii_and_arabic_indic_and_notes():
    assert parse_owner_reply("1") == (CardDecision.APPROVE, "")
    assert parse_owner_reply("٢") == (CardDecision.EDIT, "")
    assert parse_owner_reply("3")[0] is CardDecision.SKIP
    assert parse_owner_reply("٢ غيّر السطر الأول") == (CardDecision.EDIT, "غيّر السطر الأول")
    for bad in ("", "yes", "موافق", "11", "١٣", "4", "one"):
        assert parse_owner_reply(bad) == (None, "")


def test_skip_syncs_tracker_approve_does_not(tmp_path):
    store, tracker = rig(tmp_path)
    card, dec, _ = apply_owner_reply("c1", "٣", store=store, tracker=tracker)
    assert card.status == "skipped" and tracker.get("u1").status == "skipped"
    store, tracker = rig(tmp_path)
    card, dec, _ = apply_owner_reply("c1", "1", store=store, tracker=tracker)
    assert card.status == "approved"
    assert tracker.get("u1").status == "pending_approval"  # executor-only move


def test_edit_leaves_tracker_pending_and_note_kept(tmp_path):
    store, tracker = rig(tmp_path)
    card, dec, note = apply_owner_reply("c1", "2 shorter opening", store=store, tracker=tracker)
    assert card.status == "edit_requested" and card.edit_note == "shorter opening"
    assert tracker.get("u1").status == "pending_approval"


def test_undecided_and_nonpending_fail_closed(tmp_path):
    store, tracker = rig(tmp_path)
    assert apply_owner_reply("c1", "موافق", store=store, tracker=tracker) == (None, None, "")
    apply_owner_reply("c1", "3", store=store, tracker=tracker)
    assert apply_owner_reply("c1", "1", store=store, tracker=tracker) == (None, None, "")


def test_form_prep_hard_stop_and_evidence():
    plan = plan_form_prep(form_url="https://x.example/apply", card_id="c1",
                          owner_approved_fields=("name", "email", "cv_upload"))
    assert "submit" in plan.stop_before and "wyślij" in plan.stop_before
    bad = evaluate_prep(plan, screenshot_path="s.png", final_url="https://x.example/apply",
                        filled_fields=["name"], submit_clicked=True)
    assert not bad.prepared and "hard stop" in bad.blocked_reason
    bad2 = evaluate_prep(plan, screenshot_path="s.png", final_url="u",
                         filled_fields=["salary_expectation"], submit_clicked=False)
    assert not bad2.prepared and "outside card approval" in bad2.blocked_reason
    bad3 = evaluate_prep(plan, screenshot_path="", final_url="u",
                         filled_fields=["name"], submit_clicked=False)
    assert not bad3.prepared
    ok = evaluate_prep(plan, screenshot_path="s.png", final_url="https://x.example/apply",
                       filled_fields=["name", "email", "cv_upload"], submit_clicked=False)
    assert ok.prepared and ok.screenshot_path == "s.png"


def test_form_prep_requires_url():
    try:
        plan_form_prep(form_url="", card_id="c1")
        assert False
    except ValueError:
        pass
