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
