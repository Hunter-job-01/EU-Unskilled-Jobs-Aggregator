"""Apply operations layer (Part 2 of the owner plan, 2026-11-02 23:04).

Generic machinery only. No personal facts, CV content, contact lists or
tracker data live in this package: private artifacts stay in the owner's
private storage (Drive/Sheet) and are referenced by opaque handles.

Flow per vacancy card:
  classify_route -> select_cv_variant -> tailor message (bilingual)
  -> HITL card (1 approve / 2 edit / 3 skip, bound to one job + one CV
  version) -> send only after explicit per-card approval.
"""
from .route_classifier import Route, classify_route
from .cv_variants import CVVariant, select_cv_variant
from .message_tailor import TailoredMessage, tailor_message
from .hitl import ApprovalCard, CardDecision, CardStore

__all__ = [
    "Route", "classify_route",
    "CVVariant", "select_cv_variant",
    "TailoredMessage", "tailor_message",
    "ApprovalCard", "CardDecision", "CardStore",
]
