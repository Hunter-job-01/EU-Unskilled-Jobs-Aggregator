"""Approved-application executor.

The only component allowed to turn an approved card into an outbound
send. Approval arrives as EXPLICIT input (API/CLI call carrying the
owner's decision on a specific card id); this module never polls
message stores, never talks to a chat backend, and never holds
credentials - every channel is an injected client supplied by the
authorized caller.

Flow:
1. Caller passes the card id and the exact intended send tuple.
2. The card's binding is re-verified against that tuple: recipient,
   sending account, message hash, CV file hash. Any edit since approval
   fails closed.
3. The send goes through the injected channel client.
4. Exactly-once discipline: a record is written before the attempt.
   If the channel call's outcome is UNKNOWN (exception, timeout, crash),
   the record stays "unknown" and a retry MUST first reconcile provider
   state via the injected reconciler; it never blindly resends.

Route scope: this executor covers email sends only. WhatsApp-route
applications are never sent here - they are owner-handled (the owner
opens the verified wa.me link and sends from their own account), and a
tracker entry is written only after owner confirmation. Webform routes
stop at prepared-browser state by design.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Optional

from .hitl import ApprovalCard, CardStore
from .send_record import SendRecord, idempotency_key


class ExecutionError(Exception):
    """Raised when an execution cannot proceed; never sends."""


@dataclass
class ExecutionResult:
    record: SendRecord
    ok: bool
    detail: str


# Channel contracts (injected):
#   sender(recipient, subject, body, cv_path) -> provider evidence id
#   reconciler(record) -> "sent" | "not_sent" | "unknown"
#     Checks provider state (e.g. sent-mail search) after an uncertain
#     attempt so a retry never creates a duplicate.
EmailSender = Callable[[str, str, str, str], str]
SendReconciler = Callable[[SendRecord], str]


class SendLedger:
    """In-memory ledger; the owning process persists it privately."""

    def __init__(self) -> None:
        self._by_key: dict[str, SendRecord] = {}

    def get(self, key: str) -> Optional[SendRecord]:
        return self._by_key.get(key)

    def put(self, record: SendRecord) -> None:
        self._by_key[record.key] = record

    def all(self) -> list[SendRecord]:
        return list(self._by_key.values())


def _guards(card: ApprovalCard, store: CardStore, *, recipient: str,
            sending_account: str, message_hash: str,
            cv_file_hash: str) -> None:
    stored = store.get(card.card_id)
    if stored is None or stored is not card:
        raise ExecutionError("card not in store (unknown or substituted)")
    if card.status == "invalidated":
        raise ExecutionError("card invalidated by an edit after approval")
    if card.status != "approved":
        raise ExecutionError(f"card not approved (status={card.status})")
    if card.expired(store.ttl):
        raise ExecutionError("card expired; re-approval required")
    if not card.verify_binding(recipient=recipient,
                               sending_account=sending_account,
                               message_hash=message_hash,
                               cv_file_hash=cv_file_hash):
        raise ExecutionError("binding mismatch: send tuple differs from approval")


def execute_email_application(
    *,
    card: ApprovalCard,
    store: CardStore,
    ledger: SendLedger,
    recipient: str,
    sending_account: str,
    subject: str,
    body: str,
    cv_path: str,
    message_hash: str,
    cv_file_hash: str,
    sender: EmailSender,
    reconciler: Optional[SendReconciler] = None,
    now: Optional[float] = None,
) -> ExecutionResult:
    """Send one approved email application or fail closed.

    Every guard failure raises ExecutionError before any channel call.
    An uncertain prior attempt is reconciled against provider state
    before any retry; without a reconciler an unknown outcome blocks
    retries instead of risking a duplicate.
    """
    now = time.time() if now is None else now
    _guards(card, store, recipient=recipient, sending_account=sending_account,
            message_hash=message_hash, cv_file_hash=cv_file_hash)

    key = idempotency_key(card.job_uid, recipient, sending_account,
                          message_hash, cv_file_hash)
    existing = ledger.get(key)
    if existing is not None:
        if existing.status == "sent":
            return ExecutionResult(existing, True, "already sent (idempotent)")
        if existing.status == "unknown":
            verdict = reconciler(existing) if reconciler else "unknown"
            if verdict == "sent":
                existing.status = "sent"
                return ExecutionResult(existing, True,
                                       "reconciled: provider already has it")
            if verdict == "unknown":
                raise ExecutionError(
                    "prior attempt outcome unknown; reconcile provider "
                    "state before retrying (no automatic duplicate)")
            # verdict == "not_sent": verified absent, safe to retry
            existing.status = "planned"

    record = existing or SendRecord(key=key, card_id=card.card_id,
                                    job_uid=card.job_uid,
                                    recipient=recipient,
                                    sending_account=sending_account)
    ledger.put(record)
    try:
        evidence = sender(recipient, subject, body, cv_path)
    except Exception as exc:
        # Outcome uncertain: the provider may have accepted the message.
        record.status = "unknown"
        record.evidence = f"uncertain: {exc}"[:200]
        return ExecutionResult(record, False,
                               f"outcome unknown, reconcile before retry: {exc}")
    record.mark_sent(evidence)
    record.sent_at = now
    return ExecutionResult(record, True, f"sent: {evidence}")
