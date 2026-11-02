"""Human-in-the-loop approval cards.

Every outbound application is one ApprovalCard bound to exactly one job
(uid) and one CV variant. Decisions: "1" approve / "2" edit / "3" skip.
A numeric reply applies only while the card is pending; it never carries
authority to any other card (no blanket approval), matching the owner's
standing gate. Cards expire (default 72h) so a late "1" cannot fire a
stale draft.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum


class CardDecision(Enum):
    APPROVE = "1"
    EDIT = "2"
    SKIP = "3"


_VALID = {d.value: d for d in CardDecision}


@dataclass
class ApprovalCard:
    card_id: str
    job_uid: str
    cv_variant: str
    card_text_ar: str
    draft_subject: str
    draft_body: str
    status: str = "pending"        # pending | approved | edit_requested | skipped | expired
    created_at: float = field(default_factory=time.time)
    decided_at: float = 0.0
    decision: str = ""
    edit_note: str = ""

    def expired(self, ttl_seconds: float) -> bool:
        return self.status == "pending" and (time.time() - self.created_at) > ttl_seconds


class CardStore:
    """In-memory card registry; the owning process persists/serializes it
    where it lives (private state only - never committed)."""

    def __init__(self, ttl_seconds: float = 72 * 3600):
        self.ttl = ttl_seconds
        self._cards = {}
        self._pending_by_job = {}

    def create(self, card: ApprovalCard) -> ApprovalCard:
        self._cards[card.card_id] = card
        self._pending_by_job.setdefault(card.job_uid, card.card_id)
        return card

    def get(self, card_id: str) -> "ApprovalCard | None":
        return self._cards.get(card_id)

    def pending_for_job(self, job_uid: str) -> "ApprovalCard | None":
        cid = self._pending_by_job.get(job_uid)
        card = self._cards.get(cid) if cid else None
        if card and card.status == "pending" and not card.expired(self.ttl):
            return card
        return None

    def decide(self, card_id: str, reply: str, edit_note: str = "") -> "ApprovalCard | None":
        """Apply a numeric owner reply to exactly one pending card."""
        card = self._cards.get(card_id)
        if card is None or card.status != "pending":
            return None
        if card.expired(self.ttl):
            card.status = "expired"
            return None
        decision = _VALID.get(reply.strip())
        if decision is None:
            return None
        card.decision = decision.value
        card.decided_at = time.time()
        card.edit_note = edit_note if decision is CardDecision.EDIT else ""
        card.status = {
            CardDecision.APPROVE: "approved",
            CardDecision.EDIT: "edit_requested",
            CardDecision.SKIP: "skipped",
        }[decision]
        self._pending_by_job.pop(card.job_uid, None)
        return card
