"""apply_ops tests: route classification, CV variant selection, bilingual
tailoring, and the HITL approval-card state machine."""
import time

from src.apply_ops.extractor import _detect_language
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


def test_whatsapp_hint_is_not_a_route():
    # a bare number next to the word "whatsapp" is NOT proof of WhatsApp;
    # it must fall back to the page as webform, never a WhatsApp claim
    r = classify_route("Reach us on WhatsApp: +48 512 333 444", "https://x/j/3")
    assert r.route is Route.WEBFORM
    assert r.portal_precheck_required
    r2 = classify_route("Call +48 512 333 444", "")
    assert r2 is None


def test_route_apply_link_then_page_fallback():
    r = classify_route("Apply at https://portal.example.com/apply/123 now", "")
    assert r.route is Route.WEBFORM
    assert r.contact == "https://portal.example.com/apply/123"
    assert r.portal_precheck_required
    r2 = classify_route("No contact details here.", "https://x/j/9")
    assert r2.route is Route.WEBFORM
    assert r2.contact == "https://x/j/9"
    assert classify_route("nothing", "") is None


def test_wa_me_format_validation():
    assert classify_route("https://wa.me/31612345678", "").route is Route.WHATSAPP
    # too short / malformed wa.me numbers are not verified
    assert classify_route("https://wa.me/123", "https://x/j/1").route is Route.WEBFORM


# --- CV variant selection ---------------------------------------------------

def test_variant_by_title():
    assert select_cv_variant("Warehouse employee").variant is CVVariant.WAREHOUSE_LOGISTICS
    assert select_cv_variant("Kitchen helper").variant is CVVariant.HOSPITALITY_GENERAL
    assert select_cv_variant("Strawberry picking").variant is CVVariant.HOSPITALITY_GENERAL


def test_variant_by_description_and_none():
    m = select_cv_variant("Worker wanted", "join our greenhouse team")
    assert m.variant is CVVariant.HOSPITALITY_GENERAL
    assert select_cv_variant("Software engineer", "python backend") is None


# --- tailoring ---------------------------------------------------------------

def _tailor(**kw):
    args = dict(
        card_id="c1", title="Warehouse employee", company="OTTO Work Force",
        country="NL", route=Route.EMAIL, source_ref="jobs@example.com",
        cv_variant=CVVariant.WAREHOUSE_LOGISTICS, candidate_name="M S",
        contact_line="m@example.com",
        background_line="My background includes four years in mobile phone sales (2020-2024), warehouse work, and my current work in a computer shop (since September 2025).",
        auth_question_line="I am based in Egypt and would require legal work authorization. Could you let me know whether you can consider or support a candidate from outside the EU?",
    )
    args.update(kw)
    return tailor_message(**args)


def test_tailor_bilingual_and_no_unstated_claims():
    m = _tailor()
    assert "Warehouse employee" in m.subject
    # verified background only; target-variant duties never become experience
    assert "four years in mobile phone sales" in m.body_draft
    assert "logistics work" not in m.body_draft
    assert "assembly" not in m.body_draft
    # honest Egypt-resident work-authorization question, no immediate start
    assert "would require legal work authorization" in m.body_draft
    assert "outside the EU" in m.body_draft
    low = m.body_draft.lower()
    assert "immediately" not in low and "sponsor" not in low
    assert "available to relocate" not in low
    assert "بطاقة تقديم" in m.card_text_ar
    assert "1" in m.card_text_ar and "2" in m.card_text_ar and "3" in m.card_text_ar
    assert m.claims_used == []


def test_tailor_claims_echoed():
    m = _tailor(claims=["English: A2-B1 (self-assessed)"])
    assert "English: A2-B1" in m.body_draft
    assert m.claims_used == ["English: A2-B1 (self-assessed)"]


# --- HITL cards ----------------------------------------------------------------

def _card(cid="c1", uid="u1"):
    return ApprovalCard(card_id=cid, job_uid=uid, cv_variant="warehouse-logistics",
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


# --- strengthened approval binding + idempotent sends ------------------------

from src.apply_ops import SendLedger, SendRecord, content_hash, idempotency_key


def _bound_card():
    return ApprovalCard(
        card_id="c1", job_uid="u1", cv_variant="warehouse-logistics",
        card_text_ar="x", draft_subject="Application - W", draft_body="body v1",
        recipient="hiring@firma.pl", sending_account="owner@gmail.com",
        cv_file_hash="cvhash123",
    )


def test_approval_binds_exact_send_tuple():
    s = CardStore()
    card = _bound_card()
    s.create(card)
    s.decide("c1", "1")
    ok = card.verify_binding(recipient="hiring@firma.pl", sending_account="owner@gmail.com",
                             message_hash=card.message_hash, cv_file_hash="cvhash123")
    assert ok
    # any change invalidates
    assert not card.verify_binding(recipient="other@firma.pl", sending_account="owner@gmail.com",
                                   message_hash=card.message_hash, cv_file_hash="cvhash123")
    assert not card.verify_binding(recipient="hiring@firma.pl", sending_account="owner@gmail.com",
                                   message_hash=content_hash("Application - W", "body v2"),
                                   cv_file_hash="cvhash123")
    assert not card.verify_binding(recipient="hiring@firma.pl", sending_account="owner@gmail.com",
                                   message_hash=card.message_hash, cv_file_hash="cvhash999")


def test_edited_draft_gets_new_message_hash():
    # message_hash is computed once at card creation; an edit means a NEW
    # card (the old approval stays bound to the old hash)
    c1 = _bound_card()
    c2 = _bound_card()
    c2 = ApprovalCard(**{**c2.__dict__, "draft_body": "body v2", "message_hash": ""})
    assert c1.message_hash != c2.message_hash


def test_pre_send_recheck_flag_stays_true():
    assert _bound_card().pre_send_recheck is True


def test_send_ledger_idempotent():
    ledger = SendLedger()
    key = idempotency_key("u1", "hiring@firma.pl", "owner@gmail.com", "mh", "cvh")
    rec = SendRecord(key=key, card_id="c1", job_uid="u1",
                     recipient="hiring@firma.pl", sending_account="owner@gmail.com")
    assert ledger.plan(rec) is rec
    again = SendRecord(key=key, card_id="c1", job_uid="u1",
                       recipient="hiring@firma.pl", sending_account="owner@gmail.com")
    assert ledger.plan(again) is rec          # no duplicate record
    assert not ledger.already_sent(key)
    rec.mark_sent("gmail-msg-1a2b3c")
    assert ledger.already_sent(key)
    assert ledger.get(key).evidence == "gmail-msg-1a2b3c"
    # a changed recipient is a different send entirely
    key2 = idempotency_key("u1", "other@firma.pl", "owner@gmail.com", "mh", "cvh")
    assert key2 != key and not ledger.already_sent(key2)


def test_local_letters_no_immediate_start_and_pl_salutation():
    from src.apply_ops.message_tailor import draft_local_letter
    for lang in ("pl", "de", "nl"):
        letter = draft_local_letter(lang, role="R", source_ref="S",
                                    candidate_name="M", contact_line="c")
        low = letter.lower()
        assert "sofort" not in low and "od zaraz" not in low and "direct beschikbaar" not in low
    pl = draft_local_letter("pl", role="R", source_ref="S", candidate_name="M", contact_line="c")
    assert pl.startswith("Szanowni Państwo,")
    assert "Szanowni Państwu" not in pl
    de = draft_local_letter("de", role="R", source_ref="S", candidate_name="M", contact_line="c")
    assert "Arbeitserlaubnis" in de


def test_tailor_local_language_owed_note():
    m = _tailor(local_language="pl")
    assert m.local_language == "pl"
    assert m.body_draft_local == ""
    assert "لم تُجهّز" in m.card_text_ar          # local version marked as owed
    m2 = _tailor(local_language="pl", body_draft_local="Szanowni Państwo...")
    assert m2.body_draft_local.startswith("Szanowni")
    assert "لم تُجهّز" not in m2.card_text_ar


def test_cv_variant_german_production_stems():
    assert select_cv_variant("Maschinenbediener m/w/d").variant is CVVariant.FACTORY_PRODUCTION
    assert select_cv_variant("Produktionsmitarbeiter (m/w/d)").variant is CVVariant.FACTORY_PRODUCTION
    assert select_cv_variant("Kommissionierer (m/w/d)").variant is CVVariant.WAREHOUSE_LOGISTICS


def test_detect_language_skips_portal_chrome():
    chrome = ("Go to main content An official website of the European Union "
              "Search Home Jobseekers Employers Contact Accept all cookies ") * 6
    body = ("Wir suchen ab sofort motivierte Maschinenbediener in der Kunststoffindustrie. "
            "Sie bedienen die Maschinen und achten auf die Qualität der Produkte. ") * 5
    assert _detect_language(chrome + "\n" + body) == "de"


def test_dashboard_never_shows_source_copy_as_english():
    """UI regression: a title_en identical to the source title is a legacy
    failure-copy, not a translation. The dashboard must not render it
    under the English-title class."""
    from pathlib import Path
    js = (Path(__file__).resolve().parents[1] / "docs" / "app.js").read_text(encoding="utf-8")
    assert "title_en !== job.title" in js or "job.title_en !== job.title" in js
    assert "job-title-pending" in js
    # main title falls back to the ORIGINAL when no real translation exists
    assert "hasRealEn" in js
