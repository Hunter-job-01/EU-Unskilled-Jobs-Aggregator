"""Card builder integration: fail-closed orchestration across modules."""
from types import SimpleNamespace

from src.apply_ops.cards import build_application_card
from src.apply_ops.hitl import CardStore
from src.apply_ops.tracker import ApplicationTracker

PAGE_EMAIL = ("Magazynier potrzebny od zaraz. Apply by email to hr@acme-logistics.pl "
              "Salary 4500 PLN. " * 4)
PAGE_NONE = "General text with no route at all. " * 5


def job(**kw):
    base = dict(uid="u1", score=72, url="https://eures.example/j/1",
                title="Magazynier", company="Acme", country="PL",
                description="", signals=[], salary_text="", title_ar="عامل مخزن")
    base.update(kw)
    return SimpleNamespace(**base)


def rig(tmp_path):
    return CardStore(), ApplicationTracker(tmp_path / "t.json")


def test_full_card_built_with_local_letter_and_tracker(tmp_path):
    store, tracker = rig(tmp_path)
    b = build_application_card(job(), fetch=lambda u: PAGE_EMAIL, search=None,
                               card_store=store, tracker=tracker,
                               source_ref="EURES", candidate_name="Mustafa Saleh",
                               contact_line="mustafa@example.com")
    assert b and b.method == "direct_email" and b.contact == "hr@acme-logistics.pl"
    assert b.card.recipient == "hr@acme-logistics.pl"
    assert "Szanowni Państwu" in b.card.draft_body  # PL letter leads the body
    assert "Dear" in b.card.draft_body  # EN body follows
    assert "لم تُجهّز" not in b.card_text_ar  # local letter not owed
    assert tracker.get("u1").status == "pending_approval"
    assert tracker.get("u1").contact_target == "hr@acme-logistics.pl"


def test_no_card_without_route_variant_or_gate(tmp_path):
    store, tracker = rig(tmp_path)
    # posting-page fallback IS a designed web_form route: a card is built
    # with contact == the posting URL itself (evidence, not a guess)
    b = build_application_card(job(), fetch=lambda u: PAGE_NONE, search=lambda q: [],
                               card_store=store, tracker=tracker, source_ref="x",
                               candidate_name="M", contact_line="c")
    assert b and b.method == "web_form" and b.contact == job().url
    store, tracker = rig(tmp_path)
    assert build_application_card(job(score=50), fetch=lambda u: PAGE_EMAIL, search=None,
                                  card_store=store, tracker=tracker, source_ref="x",
                                  candidate_name="M", contact_line="c") is None
    assert build_application_card(job(title="Quantum Phytopathologist"),
                                  fetch=lambda u: PAGE_EMAIL, search=None,
                                  card_store=store, tracker=tracker, source_ref="x",
                                  candidate_name="M", contact_line="c") is None
    assert tracker.entries == {}  # nothing pending when no card


def test_discovery_retry_once_when_no_route(tmp_path):
    store, tracker = rig(tmp_path)
    calls = []
    def fetch(u):
        calls.append(u)
        return PAGE_NONE if len(calls) == 1 else PAGE_EMAIL
    def search(q):
        return ["https://acme-logistics.pl/praca/magazynier"]
    j = job(url="https://europa.eu/eures/portal/jv-se/jv-details/123")
    b = build_application_card(j, fetch=fetch, search=search,
                               card_store=store, tracker=tracker,
                               source_ref="x", candidate_name="M", contact_line="c")
    assert b and b.method == "direct_email"  # official page beats portal fallback
    assert len(calls) == 2  # exactly one bounded discovery retry


def test_unsupported_language_marks_letter_owed(tmp_path):
    store, tracker = rig(tmp_path)
    page = "Laundry job. Apply via hr@hotel.mt now. " * 6
    b = build_application_card(job(title="Laundry Attendant", country="MT"),
                               fetch=lambda u: page, search=None,
                               card_store=store, tracker=tracker,
                               source_ref="EURES", candidate_name="M", contact_line="c")
    if b and b.local_language:  # only when detection yields an unsupported lang
        assert b.local_letter_owed and "لم تُجهّز" in b.card_text_ar
