"""Executor: guards, idempotency, unknown-outcome reconciliation."""
import pytest

from src.apply_ops.executor import (ExecutionError, SendLedger,
                                    execute_email_application)
from src.apply_ops.hitl import ApprovalCard, CardStore


def _approved_card(store, **kw):
    card = ApprovalCard(card_id=kw.get("card_id", "c1"), job_uid="j1",
                        cv_variant="warehouse", card_text_ar="x",
                        draft_subject="Application", draft_body="Body",
                        recipient=kw.get("recipient", "hr@example.com"),
                        sending_account="me@gmail.com",
                        cv_file_hash="cvhash")
    store.create(card)
    store.decide(card.card_id, "1")
    return card


def _kw(card, **over):
    base = dict(card=card, recipient="hr@example.com",
                sending_account="me@gmail.com", subject="Application",
                body="Body", cv_path="/tmp/cv.pdf",
                message_hash=card.message_hash, cv_file_hash="cvhash")
    base.update(over)
    return base


def test_send_happy_path():
    store, ledger = CardStore(), SendLedger()
    card = _approved_card(store)
    res = execute_email_application(store=store, ledger=ledger,
                                    sender=lambda *a: "msg-1", **_kw(card))
    assert res.ok and res.record.status == "sent"
    assert res.record.evidence == "msg-1"


def test_idempotent_retry_no_double_send():
    store, ledger = CardStore(), SendLedger()
    card = _approved_card(store)
    calls = []
    send = lambda *a: calls.append(a) or "msg-1"
    execute_email_application(store=store, ledger=ledger, sender=send, **_kw(card))
    res = execute_email_application(store=store, ledger=ledger, sender=send, **_kw(card))
    assert res.ok and "idempotent" in res.detail and len(calls) == 1


def test_unapproved_card_never_sends():
    store, ledger = CardStore(), SendLedger()
    card = ApprovalCard(card_id="c9", job_uid="j1", cv_variant="w",
                        card_text_ar="x", draft_subject="S", draft_body="B",
                        recipient="hr@example.com", sending_account="me@gmail.com",
                        cv_file_hash="cvhash")
    store.create(card)
    with pytest.raises(ExecutionError, match="not approved"):
        execute_email_application(store=store, ledger=ledger,
                                  sender=lambda *a: "x", **_kw(card))


def test_binding_mismatch_after_edit_blocks():
    store, ledger = CardStore(), SendLedger()
    card = _approved_card(store)
    with pytest.raises(ExecutionError, match="binding mismatch"):
        execute_email_application(store=store, ledger=ledger,
                                  sender=lambda *a: "x",
                                  **_kw(card, recipient="other@example.com"))


def test_invalidated_card_blocks():
    store, ledger = CardStore(), SendLedger()
    card = _approved_card(store)
    card.invalidate()
    with pytest.raises(ExecutionError, match="invalidated"):
        execute_email_application(store=store, ledger=ledger,
                                  sender=lambda *a: "x", **_kw(card))


def test_unknown_outcome_blocks_retry_without_reconciler():
    store, ledger = CardStore(), SendLedger()
    card = _approved_card(store)
    def boom(*a):
        raise TimeoutError("smtp timeout")
    res = execute_email_application(store=store, ledger=ledger,
                                    sender=boom, **_kw(card))
    assert not res.ok and res.record.status == "unknown"
    with pytest.raises(ExecutionError, match="reconcile"):
        execute_email_application(store=store, ledger=ledger,
                                  sender=lambda *a: "x", **_kw(card))


def test_unknown_outcome_reconciled_sent_no_duplicate():
    store, ledger = CardStore(), SendLedger()
    card = _approved_card(store)
    def boom(*a):
        raise TimeoutError("timeout after provider accepted")
    execute_email_application(store=store, ledger=ledger, sender=boom, **_kw(card))
    calls = []
    res = execute_email_application(
        store=store, ledger=ledger,
        sender=lambda *a: calls.append(a) or "msg-dup",
        reconciler=lambda rec: "sent", **_kw(card))
    assert res.ok and res.record.status == "sent" and not calls  # no resend


def test_unknown_outcome_reconciled_not_sent_retries_once():
    store, ledger = CardStore(), SendLedger()
    card = _approved_card(store)
    def boom(*a):
        raise TimeoutError("connection refused before send")
    execute_email_application(store=store, ledger=ledger, sender=boom, **_kw(card))
    res = execute_email_application(
        store=store, ledger=ledger, sender=lambda *a: "msg-ok",
        reconciler=lambda rec: "not_sent", **_kw(card))
    assert res.ok and res.record.status == "sent" and res.record.evidence == "msg-ok"
