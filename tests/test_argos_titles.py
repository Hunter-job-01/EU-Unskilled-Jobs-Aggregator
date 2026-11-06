"""Argos offline title translation: prepass, controlled Arabic vocab,
honest-empty behaviour. Model calls are mocked; no network or model
files are needed."""
import sys
import types
import pytest

from src.argos_titles import (TITLE_AR_VOCAB, arabic_for_english_title,
                              prepass_title, translate_title_argos)


def _fake_argos(mapping):
    """Install a fake argostranslate.translate module returning
    mapping[(text, from_code, to_code)] or raising KeyError."""
    mod = types.ModuleType("argostranslate")
    tr = types.ModuleType("argostranslate.translate")
    def translate(text, from_code, to_code):
        key = (text, from_code, to_code)
        if key not in mapping:
            raise RuntimeError("no model for %r" % (key,))
        return mapping[key]
    tr.translate = translate
    mod.translate = tr
    sys.modules["argostranslate"] = mod
    sys.modules["argostranslate.translate"] = tr
    return mod


@pytest.fixture(autouse=True)
def _clean_modules():
    saved = {k: sys.modules.get(k) for k in ("argostranslate", "argostranslate.translate")}
    yield
    for k, v in saved.items():
        if v is None:
            sys.modules.pop(k, None)
        else:
            sys.modules[k] = v


def test_prepass_replaces_domain_terms():
    assert prepass_title("Magazynier", "pl") == "warehouse worker"
    assert prepass_title("Komisjonowanie", "pl") == "order picking"
    assert prepass_title("Skladnik", "cs") == "warehouse worker"
    assert "agricultural worker" in prepass_title("ΕΡΓΑΤΗΣ ΓΕΩΡΓΟΚΤΗΝΟΤΡΟΦΙΑΣ", "el")
    assert "meat" in prepass_title("Mėsos pusgaminių ruošėjas", "lt")


def test_prepass_leaves_unknown_terms():
    assert prepass_title("Zupenie cos innego", "pl") == "Zupenie cos innego"


def test_arabic_vocab_longest_match():
    assert arabic_for_english_title("Warehouse worker") == "عامل مستودع"
    assert arabic_for_english_title("Order picker, night shift") == \
        "عامل تجميع الطلبات / وردية ليلية"
    assert arabic_for_english_title("Senior architect") == ""


def test_translate_uses_vocab_arabic_over_model():
    _fake_argos({("warehouse worker", "pl", "en"): "Warehouse Worker",
                 ("Warehouse Worker", "en", "ar"): "المستودعات"})  # measured-weak raw output
    en, ar = translate_title_argos("Magazynier", "pl")
    assert en == "Warehouse Worker"
    assert ar == "عامل مستودع"  # reviewed vocab beats the raw en->ar model


def test_translate_english_title_direct():
    en, ar = translate_title_argos("Forklift operator", "en")
    assert en == "Forklift operator"
    assert ar == "مشغل رافعة شوكية"


def test_failure_leaves_both_empty():
    _fake_argos({})
    en, ar = translate_title_argos("Magazynier", "pl")
    assert en == "" and ar == ""


def test_identical_translation_rejected():
    _fake_argos({("Magazynier", "pl", "en"): "Magazynier"})
    en, ar = translate_title_argos("Magazynier", "pl")
    # "Magazynier" has no prepass term match... it does; use a termless title
    en, ar = translate_title_argos("Xyzq", "pl")
    assert en == "" and ar == ""


def test_empty_title():
    assert translate_title_argos("", "pl") == ("", "")
    assert translate_title_argos("   ", "de") == ("", "")


def test_en_gate_rejects_measured_wrong_outputs():
    from src.argos_titles import faithful_english
    assert not faithful_english("Skladník / Kompletář objednávek – HPP",
                                "Warehouse worker / Order Complex - HPP")
    assert not faithful_english("Cariste (H/F)", "Carist (H/F)")
    assert not faithful_english("Orderpicker early shift | Budbee",
                                "oncccccccccccccccccccc")
    assert not faithful_english("Manutentionnaire F/H", "Manual F/H")


def test_en_gate_qualifier_preservation():
    from src.argos_titles import faithful_english
    # licence requirement dropped -> reject
    assert not faithful_english("Lagerhelfer mit Führerschein (m/w/d)",
                                "Warehouse helper (m/f/d)")
    assert faithful_english("Lagerhelfer mit Führerschein (m/w/d)",
                            "Warehouse helper with driving license (m/f/d)")
    # seniority dropped -> reject
    assert not faithful_english("Analytický chemik QC – junior",
                                "Analytical chemist QC")
    assert faithful_english("Analytický chemik QC – junior",
                            "Analytical chemist QC - junior")
    # Teilzeit dropped -> reject
    assert not faithful_english("Teilzeit Zusteller*in (w/m/d)",
                                "Delivery (f/m/d)")
    assert faithful_english("Teilzeit Zusteller*in (w/m/d)",
                            "Part-time delivery (f/m/d)")


def test_en_gate_runaway_length():
    from src.argos_titles import faithful_english
    assert not faithful_english("Packer", "Packer " + "with benefits " * 8)


def test_compound_title_composes_full_gloss():
    from src.argos_titles import arabic_gloss
    gloss, full = arabic_gloss('Warehouse Worker / Order Picker')
    assert full
    assert 'عامل مستودع' in gloss and 'عامل تجميع الطلبات' in gloss


def test_uncovered_words_make_gloss_explicitly_partial():
    from src.argos_titles import arabic_gloss, _PARTIAL_MARK
    gloss, full = arabic_gloss('Warehouse Worker, Night Shift, Linz')
    assert not full
    rendered = arabic_for_english_title('Warehouse Worker, Night Shift, Linz')
    assert _PARTIAL_MARK in rendered


def test_vocab_arabic_marks_partial_in_pipeline():
    from src.argos_titles import translate_title_argos, _PARTIAL_MARK
    _fake_argos({("warehouse worker / order picker - full-time, Brno",
                  "cs", "en"):
                 "Warehouse Worker / Order Picker - Full-time, Brno"})
    en, ar = translate_title_argos(
        'Skladnik / Kompletar objednavek - HPP, Brno', 'cs')
    assert 'عامل مستودع' in ar and 'عامل تجميع الطلبات' in ar
    assert 'دوام كامل' in ar
    assert _PARTIAL_MARK in ar  # Brno uncovered -> partial


def test_hpp_qualifier_gets_full_time_arabic():
    from src.argos_titles import arabic_gloss
    gloss, full = arabic_gloss('Warehouse Worker - Full-time')
    assert 'دوام كامل' in gloss


def test_ambiguous_terms_never_render_full():
    from src.argos_titles import arabic_gloss
    gloss, full = arabic_gloss('Terminal Worker')
    assert not full and 'عامل محطة فرز' in gloss
    gloss, full = arabic_gloss('Operator')
    assert not full


def test_kompletar_objednavek_is_order_picker():
    from src.argos_titles import prepass_title
    assert 'order picker' in prepass_title(
        'Skladnik / Kompletar objednavek - HPP', 'cs')


def test_gate_rejects_doubled_words_and_symbols():
    from src.argos_titles import faithful_english
    assert not faithful_english(
        'Accompagnateur social en sejours',
        'Social and Social Accompagnateur in Distanting Reforms')
    assert not faithful_english(
        'Orderpicker early shift | Budbee Vianen',
        'order picker early shift ♪ Budbee Vianen')
    assert faithful_english('Magazijnmedewerker', 'warehouse worker')


def test_title_language_overrides_country_on_strong_signal():
    from src.argos_titles import guess_title_lang, translate_title_argos
    assert guess_title_lang(
        'Accompagnateur social en sejours de distanciation (H/F)') == 'fr'
    assert guess_title_lang('Lagerhelfer (m/w/d) in Hamburg') == 'de'
    assert guess_title_lang('Orderpicker') is None  # ambiguous -> country
    _fake_argos({("Accompagnateur social en sejours de distanciation (H/F)",
                  "fr", "en"): "Social escort for distancing stays (M/F)"})
    en, ar = translate_title_argos(
        'Accompagnateur social en sejours de distanciation (H/F)', 'es')
    assert en.startswith('Social escort')  # fr model, not es


def test_cyrillic_uk_ru_bg_detection_not_blanket_bulgarian():
    # Regression 2026-11-06: hosted tier-1 routed Ukrainian telegram
    # titles through bg->en ('Біля Ополе...' -> '♪ ♪' garbage). Cyrillic
    # script alone must not pick a language; unique letters decide.
    from src.argos_titles import guess_title_lang
    assert guess_title_lang('Біля Ополе та недалеко Глівіце') == 'uk'
    assert guess_title_lang('Пакувальник на склад і збір') == 'uk'
    assert guess_title_lang('Разнорабочий на выставке') == 'ru'
    assert guess_title_lang('Търся работници за склад') == 'bg'
    # Cyrillic without a decisive letter keeps the country mapping.
    assert guess_title_lang('Склад одяг Swarzędz') is None


def test_reviewed_rejected_title_fails_honest_empty():
    from src.argos_titles import translate_title_argos, _fold, _rejected_titles
    rejected = _rejected_titles()
    assert any(t.startswith('elektroniker') for t in rejected)
    _fake_argos({})
    for title in list(rejected):
        assert translate_title_argos(title, 'de') == ('', '')


def test_faithful_english_rejects_repeated_token_runaway():
    from src.argos_titles import faithful_english
    assert not faithful_english(
        "Operátor/ka výroby | až 33 500 Kč | ubytko zajištěno | nástup ihned",
        "Operator of production CZK 124; up to CZK 33,500) 124; accommodation provided 124; boarding immediately")
    # Legitimate titles with at most two repeated tokens still pass.
    assert faithful_english("Skladník/ce", "Warehouse worker warehouse")


def test_country_en_passthrough_requires_english_evidence():
    from src.argos_titles import translate_title_argos, has_english_evidence
    # Danish ad in an MT feed (country maps to en): no passthrough.
    en, ar = translate_title_argos(
        "Job på Malta - Har du selvtillid og lækkert hår? Vi betaler for fly og de første 3 uger på hotel.", "en")
    assert en == "" and ar == ""
    assert has_english_evidence("Production Worker")
    assert has_english_evidence("Siivooja / Cleaner")
    assert not has_english_evidence("Mozo/a de almacén")


def test_scheduled_layer_holds_free_model_output(monkeypatch):
    """Containment (2026-11-03): the unattended scheduled layer publishes
    only source-original English passthroughs + reviewed-vocab Arabic.
    Raw free-model EN stays EMPTY for manual semantic review."""
    import sys
    from types import ModuleType, SimpleNamespace
    sys.modules.setdefault('argostranslate', ModuleType('argostranslate'))
    import src.argos_titles as at
    from src.offline_translation import translate_titles_offline

    monkeypatch.setattr(at, 'ensure_packages', lambda langs=None, log=None: [])
    calls = {
        'Magazynier': ('Warehouse worker', 'عامل مستودع'),      # free-model output
        # scorer.looks_english misses this genuine-English title (its
        # hint set lacks 'cashier'); the evidence check lets
        # the layer copy the source through.
        'Cashier': ('Cashier', 'أمين صندوق'),
    }
    monkeypatch.setattr(at, 'translate_title_argos',
                        lambda title, lang: calls[title])
    jobs = [
        SimpleNamespace(title='Magazynier', title_en='', title_ar='',
                        score=70, excluded=False, country='PL',
                        source_language='pl', translated=False),
        SimpleNamespace(title='Cashier', title_en='', title_ar='',
                        score=80, excluded=False, country='IE',
                        source_language='en', translated=False),
    ]
    held = []
    en_done, ar_done = translate_titles_offline(
        jobs, {'translation': {'enabled': True}}, held=held)
    # Held raw model output is captured data-only for review, never
    # published: uid/title/lang/computed_en/score, nothing else.
    assert held == [{'uid': '', 'title': 'Magazynier', 'source_lang': 'pl',
                     'computed_en': 'Warehouse worker', 'score': 70}]
    # Free-model output held: honest-empty, never published unattended.
    assert jobs[0].title_en == '' and jobs[0].title_ar == ''
    assert not jobs[0].translated
    # Source-original English passthrough + reviewed-vocab gloss publish.
    assert jobs[1].title_en == 'Cashier'
    assert jobs[1].title_ar == 'أمين صندوق'
    assert (en_done, ar_done) == (1, 1)


def test_main_gates_unattended_model_writes():
    """main.py must keep both unattended free-model title layers behind
    explicit opt-in flags (containment, 2026-11-03). Static guard so a
    refactor cannot silently re-enable unattended publication."""
    import pathlib
    src = pathlib.Path('main.py').read_text(encoding='utf-8')
    assert 'publish_model_output_en' in src and 'publish_model_output_ar' in src
    todo_line = next(l for l in src.splitlines() if 'ar_candidates if not j.title_ar' in l)
    assert 'publish_model_ar' in todo_line
    en_gate = next(l for l in src.splitlines() if 'if mt_enabled and' in l)
    assert 'publish_model_en' in en_gate


def test_delivery_person_is_not_rendered_as_driver():
    """German Zusteller titles do not state a driving role: 'delivery
    person' must map to a non-driver reviewed Arabic term."""
    from src.argos_titles import arabic_for_english_title
    out = arabic_for_english_title("Delivery person (m/f/d) 6522 Prutz")
    assert "عامل توصيل" in out and "سائق" not in out
    assert "سائق توصيل" in arabic_for_english_title("Delivery driver (m/f/d)")


def test_batch15_reviewed_terms_render_expected_arabic():
    from src.argos_titles import arabic_for_english_title
    assert arabic_for_english_title("Kitchen Porter") == "مساعد مطبخ (غسل الأواني والتنظيف)"
    assert arabic_for_english_title("Warehouse team leader") == "قائد فريق مستودع"


def test_uncovered_role_qualifier_yields_no_gloss():
    from src.argos_titles import arabic_for_english_title
    for t in ("Analytical chemist QC - junior",
              "Experienced medical secretary",
              "Service employee under 18",
              "Fitter / Metalworker (m/f/d)",
              "Fashion clothing packaging operator (m/f)",
              "General Operative - Production (Crisp Factory)"):
        assert arabic_for_english_title(t) == "", t


def test_machinist_gloss_is_metal_machining_not_generic_operator():
    from src.argos_titles import arabic_for_english_title
    out = arabic_for_english_title("Cutting machine mechanic / machinist (Zerspanungsmechaniker)")
    assert "فني تشغيل معادن" in out and "مشغل ماكينات" not in out


def test_forms_only_and_forklift_gloss():
    from src.argos_titles import arabic_for_english_title
    assert arabic_for_english_title("Part-time pre-sorting worker (w/m/d)") == ""
    assert arabic_for_english_title("Part-time local logistics specialist") == ""
    assert "رافعة شوكية" in arabic_for_english_title("Forklift driver m/f/d - Tempelhof")


def test_shift_hours_and_pay_figures_carried_verbatim():
    from src.argos_titles import arabic_for_english_title
    out = arabic_for_english_title("Order Picker & Packer (14:30 - 23:30)")
    assert out == "عامل تجميع الطلبات / عامل تعبئة (\u206614:30 - 23:30\u2069)"
    # pay digits preserved verbatim; EUR word uncovered keeps partial marker
    out2 = arabic_for_english_title("Warehouse worker 18.50 EUR")
    assert "18.50" in out2 and "ترجمة جزئية" in out2


def test_role_level_words_never_dropped_behind_partial():
    from src.argos_titles import arabic_for_english_title
    for t in ("QA Inspector/Packer",
              "WAREHOUSE OPERATIVE SPECIALIST - DC BOLOGNA",
              "Warehouse Operative/Stock Controller",
              "Manufacturing Technician/General Operative/Site Operatives",
              "Technical warehouse employee E/W",
              "Delivery Driver/ Warehouse Assistant"):
        assert arabic_for_english_title(t) == "", t


def test_spelled_out_gender_markers_are_ignorable():
    from src.argos_titles import arabic_for_english_title
    assert arabic_for_english_title("Warehouse Worker (male/female)") == "عامل مستودع"


def test_driving_duties_and_career_paths_fail_pending():
    from src.argos_titles import arabic_for_english_title
    for title in (
        "Warehouse operative (with some van driver duties)",
        "Warehouse worker with driving duties",
        "Warehouse worker / van driver",
        "Order picker | Growth to Reach Truck Driver | Benthuizen",
        "Order picker progression to forklift driver",
        "Warehouse worker with delivery driver duties",
    ):
        assert arabic_for_english_title(title) == "", title
    # Direct role names are still allowed; no blanket ban on driver roles.
    assert arabic_for_english_title("Delivery driver") == "سائق توصيل"
    assert arabic_for_english_title("Forklift driver") == "مشغل رافعة شوكية"
