"""Owner decision intake for approval cards.

The main agent routes the owner's reply (WhatsApp, Arabic context) here
as plain text. Parsing is strict and fail-closed: the decision digit is
"1" (approve) / "2" (edit) / "3" (skip), ASCII or Arabic-Indic (١٢٣),
optionally followed by an edit note after a space, dash or newline.
Anything else returns undecided - the owner is re-asked, never guessed.

Side effects are explicit: SKIP also records in the private tracker;
EDIT leaves the tracker pending and marks the card for rebuild;
APPROVE only flips the card - the tracker moves to applied solely via
the executor after a proven send.
"""
from __future__ import annotations

import re

from .hitl import CardDecision, CardStore
from .tracker import ApplicationTracker, TrackerError

_DIGITS = {"1": "1", "2": "2", "3": "3",
           "١": "1", "٢": "2", "٣": "3"}


def parse_owner_reply(text: str):
    """-> (CardDecision, edit_note) or (None, ''). Never guesses."""
    t = (text or "").strip()
    if not t:
        return None, ""
    digit = _DIGITS.get(t[0])
    if digit is None:
        return None, ""
    if len(t) > 1 and t[1] not in " \t\n-–—:,.":
        return None, ""  # "11", "1a" etc. are not decisions
    note = re.sub(r"^[\s\-–—:,.]+", "", t[1:]).strip()
    return CardDecision(digit), note


def apply_owner_reply(card_id: str, reply: str, *, store: CardStore,
                      tracker: ApplicationTracker):
    """Apply one owner reply to one pending card.

    Returns (card, decision, note) on a state change, else (None, None, '').
    """
    decision, note = parse_owner_reply(reply)
    if decision is None:
        return None, None, ""
    card = store.decide(card_id, decision.value, edit_note=note)
    if card is None:
        return None, None, ""
    if decision is CardDecision.SKIP:
        try:
            tracker.record_decision(card.job_uid, CardDecision.SKIP)
        except TrackerError:
            pass  # tracker already moved (e.g. replayed reply) - card state stands
    # APPROVE: tracker moves only in the executor after proven send.
    # EDIT: tracker stays pending; the card is rebuilt with the edit note.
    return card, decision, note
