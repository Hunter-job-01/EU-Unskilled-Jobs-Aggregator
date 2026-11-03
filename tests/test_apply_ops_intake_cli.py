import json
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path

import pytest

from src.apply_ops.hitl import ApprovalCard, CardStore
from src.apply_ops.intake_cli import main as cli_main
from src.apply_ops.tracker import ApplicationTracker, TrackerEntry, STATUS_PENDING


def _card(card_id="c1", uid="u1"):
    return ApprovalCard(card_id=card_id, job_uid=uid, cv_variant="warehouse-logistics",
                        card_text_ar="بطاقة", draft_subject="Bewerbung",
                        draft_body="Sehr geehrte Damen und Herren",
                        recipient="bewerbung@example.de", sending_account="owner@gmail.com")


def _tracker_with(path, uid="u1"):
    tr = ApplicationTracker(path)
    tr.add_pending(TrackerEntry(uid=uid, company="X GmbH", title_ar="", country="DE",
                                method="direct_email", contact_target="bewerbung@example.de",
                                status=STATUS_PENDING))
    tr.save()


def test_store_persistence_roundtrip(tmp_path):
    p = tmp_path / "cards.json"
    store = CardStore()
    store.create(_card())
    store.save(p)
    loaded = CardStore.load(p)
    card = loaded.pending_for_job("u1")
    assert card is not None and card.card_id == "c1"
    assert card.recipient == "bewerbung@example.de"
    assert card.message_hash == _card().message_hash  # binding survives reload


def test_cli_register_pending_reply_flow(tmp_path, capsys):
    store_p = str(tmp_path / "cards.json")
    tracker_p = str(tmp_path / "tracker.json")
    _tracker_with(tracker_p)
    # register
    import io
    stdin = sys.stdin
    sys.stdin = io.StringIO(json.dumps(asdict(_card())))
    try:
        assert cli_main(["register", "--store", store_p]) == 0
    finally:
        sys.stdin = stdin
    # duplicate pending for same job rejected
    sys.stdin = io.StringIO(json.dumps(asdict(_card(card_id="c2"))))
    try:
        assert cli_main(["register", "--store", store_p]) == 2
    finally:
        sys.stdin = stdin
    # pending lists exactly one
    assert cli_main(["pending", "--store", store_p]) == 0
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert out["count"] == 1 and out["pending"][0]["card_id"] == "c1"
    # undecided reply -> exit 2, nothing moved
    assert cli_main(["reply", "--store", store_p, "--tracker", tracker_p,
                     "--card-id", "c1", "--text", "ماشي"]) == 2
    # Arabic-Indic skip -> tracker moves to skipped
    assert cli_main(["reply", "--store", store_p, "--tracker", tracker_p,
                     "--card-id", "c1", "--text", "٣"]) == 0
    tr = ApplicationTracker(tracker_p)
    assert tr.get("u1").status == "skipped"
    # replayed reply on decided card -> exit 2
    assert cli_main(["reply", "--store", store_p, "--tracker", tracker_p,
                     "--card-id", "c1", "--text", "3"]) == 2


def test_cli_approve_keeps_tracker_pending_and_binds(tmp_path):
    store_p, tracker_p = str(tmp_path / "cards.json"), str(tmp_path / "tracker.json")
    _tracker_with(tracker_p)
    store = CardStore()
    store.create(_card())
    store.save(store_p)
    assert cli_main(["reply", "--store", store_p, "--tracker", tracker_p,
                     "--card-id", "c1", "--text", "1"]) == 0
    tr = ApplicationTracker(tracker_p)  # applied only via executor after proven send
    assert tr.get("u1").status == "pending_approval"
    card = CardStore.load(store_p).get("c1")
    assert card.status == "approved"
    assert card.verify_binding(recipient="bewerbung@example.de",
                               sending_account="owner@gmail.com",
                               message_hash=card.message_hash,
                               cv_file_hash=card.cv_file_hash)
    assert not card.verify_binding(recipient="other@example.de",
                                   sending_account="owner@gmail.com",
                                   message_hash=card.message_hash,
                                   cv_file_hash=card.cv_file_hash)


def test_cli_edit_note_keeps_pending(tmp_path):
    store_p, tracker_p = str(tmp_path / "cards.json"), str(tmp_path / "tracker.json")
    _tracker_with(tracker_p)
    store = CardStore()
    store.create(_card())
    store.save(store_p)
    assert cli_main(["reply", "--store", store_p, "--tracker", tracker_p,
                     "--card-id", "c1", "--text", "٢ غيّر المرفق"]) == 0
    card = CardStore.load(store_p).get("c1")
    assert card.status == "edit_requested" and card.edit_note == "غيّر المرفق"
    assert ApplicationTracker(tracker_p).get("u1").status == "pending_approval"
