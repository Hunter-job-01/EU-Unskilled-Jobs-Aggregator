from src.keywords import KeywordMatrix, fold_text


def test_fold_text_strips_diacritics():
    assert fold_text("Skladník") == "skladnik"
    assert fold_text("Magazynier Łódź") == "magazynier lodz"
    assert fold_text("Produktionshelfer") == "produktionshelfer"
    assert fold_text("Zakwaterowanie Übung ß") == "zakwaterowanie ubung ss"
    assert fold_text("") == ""


def test_role_matching_across_languages(matrix):
    hits = matrix.match_roles("Hledáme skladníka do skladu v Praze", ["cs"])
    assert "skladnik" in hits
    hits = matrix.match_roles("Warehouse worker needed immediately", ["pl"])
    assert "warehouse worker" in hits  # English always included
    hits = matrix.match_roles("Oferta: Pakowacz, praca w magazynie", ["pl"])
    assert "pakowacz" in hits
    assert matrix.match_roles("Senior software engineer wanted", ["en"]) == []


def test_positive_signals(matrix):
    text = "Pracownik magazynu. Oferujemy zakwaterowanie i pomoc z zezwoleniem na pracę."
    sig = matrix.match_signals(text)
    assert "housing" in sig
    assert "permit" in sig
    text2 = "Lagerhelfer gesucht. Unterkunft wird gestellt. Ohne Deutschkenntnisse."
    sig2 = matrix.match_signals(text2)
    assert "housing" in sig2
    assert "language_flex" in sig2


def test_negative_and_disqualifiers(matrix):
    assert "manager" in matrix.match_negative("Warehouse shift manager")
    assert matrix.match_disqualifiers("Only EU citizens may apply")
    assert matrix.match_disqualifiers("Wymagane obywatelstwo UE: tylko obywatele UE")
    # "English C1 required" moved to the negation-aware L2 language gate
    # (v6 stage C, src/language_gate.py) - see tests/test_language_gate.py.
    assert not matrix.match_disqualifiers("English C1 level required for this role")
    assert not matrix.match_disqualifiers("Magazynier, zakwaterowanie gratis")


def test_search_keywords_native_first(matrix):
    kws = matrix.search_keywords(["pl", "en"], cap=5)
    assert len(kws) == 5
    assert kws[0] == "magazynier"  # first configured Polish term
    assert matrix.search_keywords(["en"], cap=3) == [
        "warehouse worker", "warehouse operative", "order picker"]


def test_negated_benefits_do_not_match(matrix):
    text = "We do NOT offer visa sponsorship and no accommodation is provided."
    sig = matrix.match_signals(text)
    assert "permit" not in sig
    assert "housing" not in sig


def test_positive_benefits_still_match(matrix):
    sig = matrix.match_signals("We offer visa sponsorship and free accommodation.")
    assert "permit" in sig
    assert "housing" in sig


def test_negation_multilingual(matrix):
    assert "housing" not in matrix.match_signals("Wir bieten keine Unterkunft.")
    assert "housing" not in matrix.match_signals("Nie oferujemy zakwaterowania.")
    assert "permit" not in matrix.match_signals("Nem biztositunk munkavallalasi engedelyt.")
    assert "housing" in matrix.match_signals("Ubytovani zdarma. Nezalezi na veku.")


def test_negation_exemptions(matrix):
    # "no language required" is a positive flexibility signal, not a denial
    sig = matrix.match_signals("Warehouse worker. No language required.")
    assert "language_flex" in sig
    sig = matrix.match_signals("Lagerhelfer gesucht. Ohne Deutschkenntnisse.")
    assert "language_flex" in sig


def test_negation_scoped_to_sentence(matrix):
    sig = matrix.match_signals("No experience needed. Accommodation is provided.")
    assert "housing" in sig
    # first mention denied, second mention affirmed -> still counts
    sig = matrix.match_signals(
        "We do not offer accommodation in Berlin. Accommodation is provided in Dresden.")
    assert "housing" in sig


def test_yaml_no_cue_loads_as_string_and_suppresses(matrix):
    # Regression: YAML 1.1 parses bare `no` as False; cues must be quoted
    # strings and the English "no" must actually suppress.
    assert matrix._neg_rx.search("no accommodation")
    sig = matrix.match_signals("Warehouse role. No accommodation is provided.")
    assert "housing" not in sig


def test_non_eu_hyphen_is_not_negation(matrix):
    sig = matrix.match_signals("Open to non-EU citizens, work permit support available.")
    assert "permit" in sig


def test_no_experience_required_retains_housing(matrix):
    sig = matrix.match_signals("No experience required, housing provided.")
    assert "housing" in sig


def test_denial_list_suppresses_all_items(matrix):
    sig = matrix.match_signals("We offer no accommodation, visa support or relocation.")
    assert "housing" not in sig
    assert "agency_relocation" not in sig


def test_suffix_denial(matrix):
    assert "permit" not in matrix.match_signals(
        "Warehouse worker wanted. Sponsorship not available for this role.")
    assert "housing" not in matrix.match_signals("Unterkunft nicht verfugbar.")


def test_suffix_denial_keeps_affirmed_keyword(matrix):
    sig = matrix.match_signals("Housing provided, relocation not available.")
    assert "housing" in sig
    assert "agency_relocation" not in sig


def test_non_string_cue_fails_clearly():
    bad = {"roles": {}, "signals": {}, "negative_roles": [], "disqualifiers": [],
           "negation_cues": {"en": [False, "not"]}}  # YAML-bare `no` shape
    try:
        KeywordMatrix(bad)
    except ValueError as exc:
        assert "quote YAML" in str(exc)
    else:
        raise AssertionError("expected ValueError for non-string cue")


# ---------------------------------------------------------------------
# Phase 4, Part A: full 27-country multilingual coverage
# ---------------------------------------------------------------------

ALL_LANGUAGE_CODES = [
    "en", "pl", "de", "cs", "sk", "lt", "hu", "ro", "nl", "es", "it",
    "fr", "pt", "sv", "da", "fi", "el", "bg", "hr", "sl", "et", "lv",
]


def test_roles_cover_every_eu_language(cfg):
    """Every official EU language used by the 27 member states has a
    non-empty role list - broad unskilled roles, not only warehouse."""
    roles = cfg["keywords"]["roles"]
    for lang in ALL_LANGUAGE_CODES:
        assert roles.get(lang), f"keywords.roles.{lang} missing or empty"


def test_every_country_language_has_keywords(cfg):
    """Each configured country's languages resolve to role keywords, so
    no member state silently searches with English only."""
    roles = cfg["keywords"]["roles"]
    for code, country in cfg["countries"].items():
        for lang in country["languages"]:
            assert roles.get(lang), f"{code}: no role keywords for {lang}"


def test_role_scope_is_broader_than_warehouse(matrix):
    """Hospitality, agriculture, cleaning, retail and laundry roles all
    match - the matrix is not restricted to warehouse/factory work."""
    for text in (
        "Kitchen helper wanted for hotel kitchen",
        "Room attendant - hotel housekeeping",
        "Farm worker for greenhouse picking",
        "Laundry worker needed",
        "Supermarket shelf stacker",
        "Dishwasher for restaurant",
        "Delivery helper",
    ):
        assert matrix.match_roles(text, ["en"]), text


def test_no_exp_signal_covers_every_language(cfg):
    signals = cfg["keywords"]["signals"]
    assert "no_exp_or_training" in signals
    for lang in ALL_LANGUAGE_CODES:
        assert signals["no_exp_or_training"].get(lang), \
            f"no_exp_or_training.{lang} missing or empty"


def test_no_exp_signal_matches_multilingual(matrix):
    samples = [
        ("No experience required, training provided.", "no experience required"),
        ("Warehouse operative. Entry level, will train.", "entry level"),
        ("Magazynier. Bez doświadczenia, do przyuczenia.", "bez doswiadczenia"),
        ("Lagerhelfer. Ohne Erfahrung, Quereinsteiger willkommen.", "ohne erfahrung"),
        ("Hilfskraft gesucht, keine Vorkenntnisse nötig.", "keine vorkenntnisse"),
        ("Skladník. Bez praxe, zaučíme.", "bez praxe"),
        ("Pomocná sila. Bez praxe, zaškolíme.", "zaskolime"),
        ("Magazijnmedewerker. Zonder ervaring, inwerktraject.", "zonder ervaring"),
        ("Mozo de almacén. Sin experiencia, con formación.", "sin experiencia"),
        ("Magazziniere. Senza esperienza, con formazione.", "senza esperienza"),
        ("Manutentionnaire. Sans expérience, débutant accepté.", "sans experience"),
        ("Operador de armazém. Sem experiência, com formação.", "sem experiencia"),
        ("Lagerarbetare. Ingen erfarenhet, utbildning ges.", "ingen erfarenhet"),
        ("Lagermedarbejder. Ingen erfaring, oplæring.", "oplaering"),
        ("Varastotyöntekijä. Ei kokemusta, koulutetaan.", "ei kokemusta"),
        ("Sandėlio darbuotojas. Be patirties, apmokome.", "be patirties"),
        ("Noliktavas darbinieks. Bez pieredzes, apmācība.", "bez pieredzes"),
        ("Laotöötaja. Ilma kogemuseta, väljaõpe.", "ilma kogemuseta"),
        ("Raktáros. Tapasztalat nélkül, betanított munka.", "tapasztalat nelkul"),
        ("Muncitor depozit. Fără experiență, training asigurat.", "fara experienta"),
        ("Складов работник. Без опит, обучение се осигурява.", "без опит"),
        ("Skladištar. Bez iskustva, osigurana obuka.", "bez iskustva"),
        ("Skladiščnik. Brez izkušenj, zagotovljeno usposabljanje.", "brez izkusenj"),
        ("Εργάτης αποθήκης. Χωρίς προϋπηρεσία, παρέχεται εκπαίδευση.", "χωρις προϋπηρεσια"),
    ]
    for text, expected in samples:
        sig = matrix.match_signals(text)
        assert "no_exp_or_training" in sig, f"no match in: {text}"
        assert expected in sig["no_exp_or_training"], \
            f"{expected} not in {sig['no_exp_or_training']} for: {text}"


def test_no_exp_category_is_negation_exempt(matrix):
    """The whole category is protected from the negation filter: these
    phrases are positive BECAUSE they negate a requirement."""
    assert "no_exp_or_training" in matrix.NEGATION_EXEMPT_CATEGORIES
    for text in (
        "No experience required.",
        "Ohne Erfahrung.",
        "Bez doświadczenia.",
        "Sin experiencia.",
        "Sans expérience.",
        "Sem experiência.",
        "Zonder ervaring.",
        "Fără experiență.",
        "Tapasztalat nélkül.",
    ):
        sig = matrix.match_signals("Warehouse worker. " + text)
        assert "no_exp_or_training" in sig, f"suppressed: {text}"


def test_training_only_phrases_also_match(matrix):
    sig = matrix.match_signals("Production worker. Full training provided on site.")
    assert "no_exp_or_training" in sig
    sig = matrix.match_signals("Pracownik produkcji. Zapewniamy szkolenie.")
    assert "no_exp_or_training" in sig


def test_no_exp_signal_does_not_leak_into_unrelated_text(matrix):
    sig = matrix.match_signals("Senior accountant, 5 years of experience.")
    assert "no_exp_or_training" not in sig
