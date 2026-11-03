"""Main-routed owner-reply intake for approval cards.

This is the wiring between the owner's chat reply ("1"/"2"/"3", ASCII or
Arabic-Indic) and the private card registry + application tracker. The
main agent (or its task agent) runs this module; it never talks to the
owner directly and never sends anything.

    python -m src.apply_ops.intake_cli register --store CARDS.json < card.json
    python -m src.apply_ops.intake_cli pending --store CARDS.json
    python -m src.apply_ops.intake_cli reply --store CARDS.json \
        --tracker TRACKER.json --card-id CARD_ID --text "1"

Exit code is 0 on a state change or a clean listing, 2 when the reply
decides nothing (undecided/unknown card/already decided) - the caller
re-asks the owner rather than guessing.
"""
from __future__ import annotations

import argparse
import json
import sys

from .hitl import ApprovalCard, CardStore
from .intake import apply_owner_reply
from .tracker import ApplicationTracker


def _cmd_register(args) -> int:
    raw = json.load(sys.stdin)
    card = ApprovalCard(**raw)
    store = CardStore.load(args.store)
    existing = store.pending_for_job(card.job_uid)
    if existing and existing.card_id != card.card_id:
        print(json.dumps({"registered": False, "reason": "job already has a pending card",
                          "card_id": existing.card_id}, ensure_ascii=False))
        return 2
    store.create(card)
    store.save(args.store)
    print(json.dumps({"registered": True, "card_id": card.card_id}, ensure_ascii=False))
    return 0


def _cmd_pending(args) -> int:
    store = CardStore.load(args.store)
    cards = [{"card_id": c.card_id, "job_uid": c.job_uid, "recipient": c.recipient,
              "cv_variant": c.cv_variant, "created_at": c.created_at}
             for c in store.pending()]
    print(json.dumps({"pending": cards, "count": len(cards)}, ensure_ascii=False))
    return 0


def _cmd_reply(args) -> int:
    store = CardStore.load(args.store)
    tracker = ApplicationTracker(args.tracker)
    card, decision, note = apply_owner_reply(
        args.card_id, args.text, store=store, tracker=tracker)
    if card is None:
        print(json.dumps({"applied": False, "card_id": args.card_id}, ensure_ascii=False))
        return 2
    store.save(args.store)
    tracker.save()
    print(json.dumps({"applied": True, "card_id": card.card_id,
                      "decision": decision.value, "status": card.status,
                      "edit_note": note if decision.value == "2" else ""},
                     ensure_ascii=False))
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="intake_cli")
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in ("register", "pending", "reply"):
        p = sub.add_parser(name)
        p.add_argument("--store", required=True, help="private card registry path")
        if name == "reply":
            p.add_argument("--tracker", required=True, help="private tracker path")
            p.add_argument("--card-id", required=True)
            p.add_argument("--text", required=True, help="owner reply text")
    args = parser.parse_args(argv)
    return {"register": _cmd_register, "pending": _cmd_pending,
            "reply": _cmd_reply}[args.cmd](args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
