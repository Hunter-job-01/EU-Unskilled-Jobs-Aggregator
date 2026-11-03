"""Human-in-the-loop approval cards.

Every outbound application is one ApprovalCard. An approval binds the
EXACT send tuple: job uid, recipient, sending account, message content
hash, and CV file hash. Changing the recipient, the sending account, the
message text or the attached CV after approval invalidates it - the send
executor must call verify_binding() immediately before sending and must
treat a False as "needs a fresh approval".

Numeric replies ("1" approve / "2" edit / "3" skip) act on exactly one
pending card - never blanket authority. The 72h TTL is only a staleness
guard, NOT freshness proof: the executor must re-fetch the live posting
or form before acting on any approval (pre_send_recheck stays True).
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path


class CardDecision(Enum):
    APPROVE = "1"
    EDIT = "2"
    SKIP = "3"


_VALID = {d.value: d for d in CardDecision}


def content_hash(*parts: str) -> str:
    h = hashlib.sha256()
    for part in parts:
        h.update(part.encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()


@dataclass
class ApprovalCard:
    card_id: str
    job_uid: str
    cv_variant: str
    card_text_ar: str
    draft_subject: str
    draft_body: str
    # exact send tuple the approval binds (filled at card creation)
    recipient: str = ""
    sending_account: str = ""
    message_hash: str = ""          # content_hash(subject, body)
    cv_file_hash: str = ""          # sha256 of the exact CV file bytes
    status: str = "pending"         # pending | approved | edit_requested | skipped | expired | invalidated
    created_at: float = field(default_factory=time.time)
    decided_at: float = 0.0
    decision: str = ""
    edit_note: str = ""
    pre_send_recheck: bool = True   # executor must re-verify live posting/form before send

    def __post_init__(self):
        if not self.message_hash:
            self.message_hash = content_hash(self.draft_subject, self.draft_body)

    def expired(self, ttl_seconds: float) -> bool:
        return self.status == "pending" and (time.time() - self.created_at) > ttl_seconds

    def verify_binding(self, *, recipient: str, sending_account: str,
                       message_hash: str, cv_file_hash: str) -> bool:
        """True only if the proposed send still matches the approved tuple
        exactly. Any edit, recipient, account or attachment change fails."""
        return (
            self.status == "approved"
            and recipient == self.recipient
            and sending_account == self.sending_account
            and message_hash == self.message_hash
            and cv_file_hash == self.cv_file_hash
        )

    def invalidate(self) -> None:
        if self.status in ("pending", "approved"):
            self.status = "invalidated"


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


    def pending(self) -> list:
        """All live pending cards (not expired), oldest first."""
        out = []
        for card in self._cards.values():
            if card.status == "pending" and not card.expired(self.ttl):
                out.append(card)
        return sorted(out, key=lambda c: c.created_at)

    def save(self, path) -> None:
        """Persist the registry atomically. The file is PRIVATE state:
        it lives outside the repo / gitignored and is never committed."""
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": 1,
                   "cards": {cid: asdict(c) for cid, c in sorted(self._cards.items())}}
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(p)

    @classmethod
    def load(cls, path, ttl_seconds: float = 72 * 3600) -> "CardStore":
        store = cls(ttl_seconds=ttl_seconds)
        p = Path(path)
        if p.exists():
            data = json.loads(p.read_text(encoding="utf-8"))
            for cid, raw in (data.get("cards") or {}).items():
                card = ApprovalCard(**raw)
                store._cards[cid] = card
                if card.status == "pending":
                    store._pending_by_job.setdefault(card.job_uid, cid)
        return store
