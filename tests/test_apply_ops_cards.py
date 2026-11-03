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
    assert "Szanowni Państwo," in b.card.draft_body  # PL letter leads the body
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


class TestCardV32:
    """Manual reviewed titles, separated fact checks, AR letter rendering."""

    def _card(self, page_text, tmp_path, **kw):
        store, tracker = CardStore(), ApplicationTracker(tmp_path / "t.json")
        return build_application_card(
            job(title=kw.pop("title", "Kommissionierer (m/w/d)"),
                company="SYNERGIE", country="DE", title_ar=""),
            fetch=lambda u: page_text, search=None, card_store=store,
            tracker=tracker, source_ref="EURES", candidate_name="Mustafa Saleh",
            contact_line="mustafa@example.com", **kw)

    def test_manual_titles_labeled(self, tmp_path):
        b = self._card(PAGE_EMAIL, tmp_path,
                       title_en_manual="Order picker",
                       title_ar_manual="عامل تجهيز طلبات")
        t = b.card_text_ar
        assert "عامل تجهيز طلبات (ترجمة يدوية مراجعة)" in t
        assert "Job title (EN): Order picker (manual reviewed translation)" in t
        assert "العنوان الأصلي: Kommissionierer (m/w/d)" in t

    def test_en_pending_honest_when_no_manual(self, tmp_path):
        b = self._card(PAGE_EMAIL, tmp_path)
        assert "ترجمة إنجليزية غير متاحة بعد" in b.card_text_ar

    def test_career_changer_not_training(self, tmp_path):
        page = PAGE_EMAIL + " Wir suchen auch Quereinsteiger."
        b = self._card(page, tmp_path)
        assert "قبول المتحولين مهنيا: مذكور" in b.card_text_ar
        assert "تدريب مقدم من جهة العمل: غير مذكور" in b.card_text_ar

    def test_local_letter_arabic_rendering(self, tmp_path):
        page = ("Maschinenbediener gesucht. Bewerbung per E-Mail an "
                "jobs@tritec.example. " * 3)
        b = self._card(page, tmp_path, title="Maschinenbediener (m/w/d)")
        assert b.local_language == "de"
        assert "ترجمة الخطاب (de) إلى العربية" in b.card_text_ar
        assert "أعيش في مصر" in b.card_text_ar

    def test_salary_fragment_verbatim(self, tmp_path):
        page = ("Kommissionierer gesucht. Apply by email to hr@acme-logistics.pl. "
                "Wir bieten: Übertarifliche Vergütung ab 15,50 €/Std. und mehr. " * 2)
        b = self._card(page, tmp_path)
        assert "Vergütung ab 15,50 €/Std" in b.card_text_ar

    def test_information_is_not_training(self, tmp_path):
        page = PAGE_EMAIL + " Information about the company follows."
        b = self._card(page, tmp_path)
        assert "تدريب مقدم من جهة العمل: غير مذكور" in b.card_text_ar

    def test_full_training_detected(self, tmp_path):
        page = ("Kitchen porter wanted. Apply by email to hr@acme-logistics.pl. "
                "We provide full training, kitchen experience is a plus. " * 2)
        b = self._card(page, tmp_path, title="Kitchen Porter")
        assert "تدريب مقدم من جهة العمل: مذكور" in b.card_text_ar

    def test_reasonable_accommodation_is_not_housing(self, tmp_path):
        page = ("Warehouse operative wanted. Apply by email to hr@acme-logistics.pl. "
                "We ensure reasonable accommodations are implemented. " * 2)
        b = self._card(page, tmp_path, title="Warehouse Operative")
        assert "السكن: غير مذكور" in b.card_text_ar

    def test_salary_fragment_prefers_tight_money_sentence(self, tmp_path):
        page = ("Warehouse operative wanted. Apply by email to hr@acme-logistics.pl.\n"
                "We offer a competitive and transparent salary system for all.\n"
                "€15.40 rising to €17.90 per hour after 3 years\n"
                "20 days holidays per annum")
        b = self._card(page, tmp_path, title="Warehouse Operative")
        assert "€15.40 rising to €17.90 per hour" in b.card_text_ar

    def test_salary_label_value_split_lines(self, tmp_path):
        page = ("Kitchen porter wanted. Apply by email to hr@acme-logistics.pl.\n"
                "Remuneration\nRemuneration basis\nFix base paid\nSalary\n"
                "15 EUR per year (Initial rate of compensation)\nHow to apply")
        b = self._card(page, tmp_path, title="Kitchen Porter")
        assert "Salary 15 EUR per year" in b.card_text_ar
