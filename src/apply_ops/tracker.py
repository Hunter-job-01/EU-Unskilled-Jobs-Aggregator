"""Private application tracker (CRM) for the apply-ops flow.

Persists one entry per job uid: company, Arabic title, country,
application method, contact target, status and proof. The tracker file
is PRIVATE (gitignored): it never carries message bodies, CV content or
owner secrets, and it is never committed with live private data.

Status machine (evidence-based, no silent jumps):
    pending_approval --approve(proof required)--> applied
    pending_approval --skip--> skipped
    pending_approval --edit--> pending_approval (card is invalidated and
                               must be rebuilt; the tracker does not move)
Decisions on a uid that is not pending are rejected - an applied or
skipped job is never silently re-opened or overwritten.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from .hitl import CardDecision

STATUS_PENDING = "pending_approval"
STATUS_APPLIED = "applied"
STATUS_SKIPPED = "skipped"
_STATUSES = {STATUS_PENDING, STATUS_APPLIED, STATUS_SKIPPED}


@dataclass
class TrackerEntry:
    uid: str
    company: str
    title_ar: str
    country: str
    method: str           # direct_email | whatsapp_or_phone | web_form
    contact_target: str   # evidence-backed route contact, never guessed
    status: str = STATUS_PENDING
    applied_at: str = ""
    proof: str = ""       # what evidences the send (message id, timestamp)


class TrackerError(ValueError):
    pass


class ApplicationTracker:
    def __init__(self, path):
        self.path = Path(path)
        self.entries = {}
        if self.path.exists():
            data = json.loads(self.path.read_text(encoding="utf-8"))
            for uid, raw in (data.get("entries") or {}).items():
                entry = TrackerEntry(**raw)
                if entry.status not in _STATUSES:
                    raise TrackerError("unknown status in tracker file: " + entry.status)
                self.entries[uid] = entry

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": 1,
                   "entries": {u: asdict(e) for u, e in sorted(self.entries.items())}}
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1),
                       encoding="utf-8")
        tmp.replace(self.path)

    def add_pending(self, entry: TrackerEntry) -> TrackerEntry:
        """Register a card awaiting the owner's 1/2/3 decision. Never
        overwrites an existing entry - a re-issued card for the same uid
        must not reset an applied/skipped outcome."""
        if entry.status != STATUS_PENDING:
            raise TrackerError("new entries must start pending_approval")
        existing = self.entries.get(entry.uid)
        if existing:
            return existing
        self.entries[entry.uid] = entry
        return entry

    def record_decision(self, uid: str, decision: CardDecision, proof: str = "") -> TrackerEntry:
        entry = self.entries.get(uid)
        if not entry:
            raise TrackerError("unknown uid: " + uid)
        if entry.status != STATUS_PENDING:
            raise TrackerError("uid {} already {}".format(uid, entry.status))
        if decision is CardDecision.APPROVE:
            if not proof.strip():
                raise TrackerError("applied requires send proof (message id / timestamp)")
            entry.status = STATUS_APPLIED
            entry.applied_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            entry.proof = proof.strip()
        elif decision is CardDecision.SKIP:
            entry.status = STATUS_SKIPPED
        elif decision is CardDecision.EDIT:
            pass  # stays pending; the invalidated card is rebuilt elsewhere
        else:  # pragma: no cover - enum is closed
            raise TrackerError("unknown decision")
        return entry

    def by_status(self, status: str) -> list:
        if status not in _STATUSES:
            raise TrackerError("unknown status: " + status)
        return [e for e in self.entries.values() if e.status == status]

    def get(self, uid: str):
        return self.entries.get(uid)
