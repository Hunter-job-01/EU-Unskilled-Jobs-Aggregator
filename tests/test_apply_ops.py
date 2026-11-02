"""apply_ops tests: route classification, CV variant selection, bilingual
tailoring, and the HITL approval-card state machine."""
import time

from src.apply_ops import (
    ApprovalCard, CardStore, Route, classify_route, select_cv_variant,
    tailor_message, CVVariant,
)


# --- route classifier -------------------------------------------------------

def test_route_email_wins():
    r = classify_route("Send your CV to hiring@firma.pl today", "https://x/j/1")
    assert r.route is Route.EMAIL
    assert r.contact == "hiring@firma.pl"
    assert "email in posting" in r.evidence


def test_route_whatsapp_wame_link():
    r = classify_route("Contact us https://wa.me/31612345678 for details", "")
    assert r.route is Route.WHATSAPP
    assert r.contact == "31612345678"


def test_route_whatsapp_hint():
    r = classify_route("Reach us on WhatsApp: +48 512 333 444", "")
    assert r.route is Route.WHATSAPP
    assert r.contact == "+48512333444"


def test_route_apply_link_then_page_fallback():
    r = classify_route("Apply at https://portal.example.com/apply/123 now", "")
    assert r.route is Route.WEBFORM
    assert r.contact == "https://portal.example.com/apply/123"
    r2 = classify_route("No contact details here.", "https://x/j/9")
    assert r2.route is Route.WEBFORM
    assert r2.contact == "https://x/j/9"
    assert classify_route("nothing", "") is None


# --- CV variant selection ---------------------------------------------------

def test_variant_by_title():
    assert select_cv_variant("Warehouse employee").variant is CVVariant.WAREHOUSE_PRODUCTION
    assert select_cv_variant("Kitchen helper").variant is CVVariant.HOSPITALITY_KITCHEN
    assert select_cv_variant("Strawberry picking").variant is CVVariant.AGRICULTURE_SEASONAL


def test_variant_by_description_and_none():
    m = select_cv_variant("Worker wanted", "join our greenhouse team")
    assert m.variant is CVVariant.AGRICULTURE_SEASONAL
    assert select_cv_variant("Software engineer", "python backend") is None


# --- tailoring ---------------------------------------------------------------

def _tailor(**kw):
    args = dict(
        card_id="c1", title="Warehouse employee", company="OTTO Work Force",
        country="NL", route=Route.EMAIL, source_ref="jobs@example.com",
        cv_variant=CVVariant.WAREHOUSE_PRODUCTION, candidate_name="M S",
        contact_line="m@example.com",
    )
    args.update(kw)
    return tailor_message(**args)


def test_tailor_bilingual_and_no_unstated_claims():
    m = _tailor()
    assert "Warehouse employee" in m.subject
    assert "warehouse, packing and production" in m.body_draft
    assert "بطاقة تقديم" in m.card_text_ar
    assert "1" in m.card_text_ar and "2" in m.card_text_ar and "3" in m.card_text_ar
    # no permit/sponsorship assertions in the draft
    low = m.body_draft.lower()
    assert "permit" not in low and "sponsor" not in low
    assert m.claims_used == []


def test_tailor_claims_echoed():
    m = _tailor(claims=["English: A2-B1 (self-assessed)"])
    assert "English: A2-B1" in m.body_draft
    assert m.claims_used == ["English: A2-B1 (self-assessed)"]


# --- HITL cards ----------------------------------------------------------------

def _card(cid="c1", uid="u1"):
    return ApprovalCard(card_id=cid, job_uid=uid, cv_variant="warehouse-production",
                        card_text_ar="x", draft_subject="s", draft_body="b")


def test_card_decision_binding():
    s = CardStore()
    s.create(_card())
    assert s.pending_for_job("u1").card_id == "c1"
    c = s.decide("c1", "1")
    assert c.status == "approved"
    # decided card is no longer pending; a second reply cannot re-fire it
    assert s.pending_for_job("u1") is None
    assert s.decide("c1", "2") is None


def test_card_skip_and_edit():
    s = CardStore()
    s.create(_card("a", "ua"))
    assert s.decide("a", "3").status == "skipped"
    s.create(_card("b", "ub"))
    c = s.decide("b", "2", edit_note="غيّر المدينة")
    assert c.status == "edit_requested"
    assert c.edit_note == "غيّر المدينة"


def test_card_expiry_and_unknown_reply():
    s = CardStore(ttl_seconds=0.01)
    card = _card()
    card.created_at = time.time() - 1
    s.create(card)
    assert s.decide("c1", "1") is None
    assert card.status == "expired"
    s2 = CardStore()
    s2.create(_card())
    assert s2.decide("c1", "9") is None
    assert s2.get("c1").status == "pending"


def test_no_blanket_authority_across_cards():
    s = CardStore()
    s.create(_card("c1", "u1"))
    s.create(_card("c2", "u2"))
    s.decide("c1", "1")
    # approving c1 must not touch c2
    assert s.get("c2").status == "pending"
    assert s.pending_for_job("u2").card_id == "c2"
