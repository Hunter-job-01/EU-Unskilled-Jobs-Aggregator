"""Idempotent send-execution records.

One SendRecord per attempted outbound application. The idempotency key
binds job + recipient + sending account + exact message + exact CV file,
so a retried execution can never double-send the same application and a
changed tuple is visibly a DIFFERENT send. Records carry their evidence
handles (e.g. provider message id) once sent. The owning process persists
these in private state; this module holds the model and key logic only.
"""
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field


def idempotency_key(job_uid: str, recipient: str, sending_account: str,
                    message_hash: str, cv_file_hash: str) -> str:
    h = hashlib.sha256()
    for part in (job_uid, recipient, sending_account, message_hash, cv_file_hash):
        h.update(part.encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()


@dataclass
class SendRecord:
    key: str
    card_id: str
    job_uid: str
    recipient: str
    sending_account: str
    status: str = "planned"       # planned | sent | failed | cancelled
    evidence: str = ""            # provider message id / portal confirmation ref
    created_at: float = field(default_factory=time.time)
    sent_at: float = 0.0

    def mark_sent(self, evidence: str) -> None:
        self.status = "sent"
        self.evidence = evidence
        self.sent_at = time.time()


class SendLedger:
    """In-memory ledger enforcing idempotency; persistence is the owner's
    private state layer."""

    def __init__(self):
        self._records = {}

    def plan(self, record: SendRecord) -> "SendRecord | None":
        """Register a planned send. Returns the existing record (already
        planned/sent) instead of a duplicate when the key matches."""
        existing = self._records.get(record.key)
        if existing is not None:
            return existing
        self._records[record.key] = record
        return record

    def already_sent(self, key: str) -> bool:
        rec = self._records.get(key)
        return bool(rec and rec.status == "sent")

    def get(self, key: str) -> "SendRecord | None":
        return self._records.get(key)
