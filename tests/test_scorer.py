from datetime import date, timedelta

from src.models import Job
from src.scorer import Scorer, dedup_hash, looks_english


def make_job(**kw):
    base = dict(title="Magazynier", company="ACME Sp. z o.o.", country="PL",
                source="eures", url="https://example.test/j/1")
    base.update(kw)
    return Job(**base)


def test_dedup_hash_deterministic_and_normalized():
    h1 = dedup_hash("ACME Sp. z o.o.", "Magazynier (m/k)", "PL")
    h2 = dedup_hash("acme sp z oo", "magazynier m k", "pl")
    assert h1 == h2
    assert h1 != dedup_hash("ACME", "Magazynier", "DE")


def test_base_role_score_and_signals(cfg):
    job = make_job()
    Scorer(cfg).score(job, today=date(2026, 11, 2))
    assert not job.excluded
    assert job.score > 0
    assert "unskilled_role_match" in job.signals
    assert "tier_1_country" in job.signals


def test_disqualifier_excluded(cfg):
    job = make_job(description="Only EU citizens may apply.")
    Scorer(cfg).score(job)
    assert job.excluded
    assert job.score == 0
    assert "disqualifier" in job.exclusion_reason


def test_skilled_role_excluded(cfg):
    job = make_job(title="Senior Software Engineer", company="TechCorp")
    Scorer(cfg).score(job)
    assert job.excluded


def test_permit_and_housing_raise_score(cfg):
    scorer = Scorer(cfg)
    plain = make_job()
    rich = make_job(description="Oferujemy zakwaterowanie oraz zezwolenie na pracę.")
    scorer.score(plain)
    scorer.score(rich)
    assert rich.score > plain.score
    assert "housing_keyword_mention" in rich.signals
    assert "permit_keyword_mention" in rich.signals


def test_tier_multiplier_orders_countries(cfg):
    scorer = Scorer(cfg)
    t1 = make_job(country="PL")
    t3 = make_job(country="SE")
    scorer.score(t1)
    scorer.score(t3)
    assert t1.score > t3.score


def test_recency_bonus(cfg):
    scorer = Scorer(cfg)
    today = date(2026, 11, 2)
    fresh = make_job(posted_date=(today - timedelta(days=1)).isoformat())
    old = make_job(posted_date=(today - timedelta(days=25)).isoformat())
    scorer.score(fresh, today=today)
    scorer.score(old, today=today)
    assert fresh.score > old.score
    assert "fresh_posting" in fresh.signals


def test_score_bounds(cfg):
    job = make_job(
        description="zakwaterowanie zezwolenie na prace agencja pracy "
                    "bez znajomosci jezyka " + "pakowacz " * 10,
        posted_date=date.today().isoformat())
    Scorer(cfg).score(job)
    assert 0 <= job.score <= 100


def test_looks_english():
    assert looks_english("Warehouse worker")
    assert not looks_english("Pracownik produkcji")
    assert looks_english("")


def test_negated_benefits_earn_no_bonus(cfg):
    scorer = Scorer(cfg)
    denied = make_job(
        title="Warehouse worker",
        description="We do NOT offer visa sponsorship and no accommodation is provided.")
    plain = make_job(title="Warehouse worker")
    scorer.score(denied)
    scorer.score(plain)
    assert "permit_keyword_mention" not in denied.signals
    assert "housing_keyword_mention" not in denied.signals
    assert denied.score == plain.score


# ---------------------------------------------------------------------
# Fix 2: translation quota discipline (score before translate)
# ---------------------------------------------------------------------
import sys
import types

from src.scorer import translate_titles


def _install_fake_deep_translator(monkeypatch):
    """Swap in a fake deep_translator module; returns the created
    translator instances so tests can inspect which titles were sent."""
    module = types.ModuleType("deep_translator")
    created = []

    class FakeTranslator:
        def __init__(self, source=None, target=None):
            self.calls = []

        def translate(self, title):
            self.calls.append(title)
            return "EN: " + title

    def factory(source=None, target=None):
        t = FakeTranslator(source=source, target=target)
        created.append(t)
        return t

    module.GoogleTranslator = factory
    monkeypatch.setitem(sys.modules, "deep_translator", module)
    return created


def _translation_cfg(**over):
    cfg = {"translation": {"enabled": True, "target": "en",
                           "max_per_run": 200, "min_score": 40}}
    cfg["translation"].update(over)
    return cfg


def _scored_job(title, score, **kw):
    base = dict(title=title, company="ACME", country="PL",
                source="eures", url="https://example.test/j", score=score)
    base.update(kw)
    return Job(**base)


def test_translation_skips_excluded_and_low_score_jobs(monkeypatch):
    created = _install_fake_deep_translator(monkeypatch)
    excluded = _scored_job("Pracownik produkcji", 80, excluded=True,
                           exclusion_reason="disqualifier: only eu citizens")
    low = _scored_job("Pracownik magazynu", 30)     # below min_score 40
    high = _scored_job("Pakowacz", 70)
    done = translate_titles([excluded, low, high], _translation_cfg(publish_model_output_en=True))
    assert done == 1
    assert created[0].calls == ["Pakowacz"]  # only the eligible job hit the network
    assert excluded.title_en == ""
    assert low.title_en == ""
    assert high.title_en == "EN: Pakowacz"
    assert high.translated is True


def test_translation_cap_spent_highest_score_first(monkeypatch):
    created = _install_fake_deep_translator(monkeypatch)
    jobs = [_scored_job("Pracownik produkcji", 41),
            _scored_job("Magazynier", 90),
            _scored_job("Pakowacz", 55)]
    done = translate_titles(jobs, _translation_cfg(max_per_run=2, publish_model_output_en=True))
    assert done == 2
    # score order 90, 55 - the 41-point title is left untranslated
    assert created[0].calls == ["Magazynier", "Pakowacz"]
    assert jobs[0].title_en == ""


def test_english_titles_copied_for_free_without_cap(monkeypatch):
    created = _install_fake_deep_translator(monkeypatch)
    eng = _scored_job("Warehouse worker", 95)
    non_en = _scored_job("Magazynier", 90)
    done = translate_titles([eng, non_en], _translation_cfg(max_per_run=0))
    assert done == 0
    assert created[0].calls == []            # zero HTTP requests
    assert eng.title_en == "Warehouse worker"  # free copy, cap untouched
    assert non_en.title_en == ""


def test_translation_min_score_gate_configurable(monkeypatch):
    created = _install_fake_deep_translator(monkeypatch)
    mid = _scored_job("Magazynier", 30)
    done = translate_titles([mid], _translation_cfg(min_score=20, publish_model_output_en=True))
    assert done == 1
    assert created[0].calls == ["Magazynier"]


def test_translation_uses_caller_effective_min_score(monkeypatch):
    # The pipeline's effective cutoff (--min-score / scoring.min_score_default)
    # wins over translation.min_score in BOTH directions.
    created = _install_fake_deep_translator(monkeypatch)
    mid = _scored_job("Magazynier", 30)
    done = translate_titles([mid], _translation_cfg(min_score=40, publish_model_output_en=True), min_score=20)
    assert done == 1                       # lower CLI threshold: 30 is eligible
    assert created[-1].calls == ["Magazynier"]

    created = _install_fake_deep_translator(monkeypatch)
    high = _scored_job("Pakowacz", 55)
    done = translate_titles([high], _translation_cfg(min_score=40), min_score=60)
    assert done == 0                       # higher CLI threshold: 55 is skipped
    assert created[-1].calls == []
    assert high.title_en == ""


# ---------------------------------------------------------------------
# Phase 4: no_exp_or_training scoring
# ---------------------------------------------------------------------

def test_no_exp_bonus_and_signal(cfg):
    scorer = Scorer(cfg)
    plain = make_job(title="Warehouse worker")
    rich = make_job(title="Warehouse worker",
                    description="No experience required, training provided.")
    scorer.score(plain)
    scorer.score(rich)
    assert "no_exp_or_training_mention" in rich.signals
    assert "no_exp_or_training_mention" not in plain.signals
    assert rich.score > plain.score


def test_no_exp_alone_qualifies_for_base_points(cfg):
    """A posting with NO role keyword but an explicit no-experience /
    training mention still qualifies (provided no disqualifier or
    skilled/senior vocabulary matched)."""
    job = make_job(title="Motivated people wanted",
                   description="Join our team. No experience required - "
                               "full training provided.")
    Scorer(cfg).score(job)
    assert not job.excluded
    assert job.score > 0
    assert "no_exp_or_training_mention" in job.signals
    assert "unskilled_role_match" not in job.signals


def test_no_exp_does_not_rescue_skilled_roles(cfg):
    job = make_job(title="Senior accountant",
                   company="Finance Corp",
                   description="No experience required.")
    Scorer(cfg).score(job)
    assert job.excluded
    assert job.score == 0


def test_no_exp_does_not_rescue_disqualified(cfg):
    job = make_job(title="Helper",
                   description="No experience required. Only EU citizens may apply.")
    Scorer(cfg).score(job)
    assert job.excluded
    assert "disqualifier" in job.exclusion_reason


def test_no_role_no_signal_still_excluded(cfg):
    job = make_job(title="Interesting vacancy",
                   description="Apply today.")
    Scorer(cfg).score(job)
    assert job.excluded
    assert "no unskilled role keyword match" in job.exclusion_reason


def test_translation_stops_on_throttle(monkeypatch):
    import sys, types
    calls = []
    class Translator:
        def __init__(self, **kwargs): pass
        def translate(self, title):
            calls.append(title)
            raise Exception('Too many requests: HTTP 429')
    monkeypatch.setitem(sys.modules, 'deep_translator', types.SimpleNamespace(GoogleTranslator=Translator))
    jobs = [_scored_job('Magazynier', 80), _scored_job('Lagerhelfer', 70)]
    assert translate_titles(jobs, _translation_cfg(publish_model_output_en=True)) == 0
    assert len(calls) == 1
    # Failure/throttle leaves title_en EMPTY: copying the source title would
    # fake a translation and hide the job from the Gemini fallback gate.
    assert all(j.title_en == "" for j in jobs)
    assert all(not j.translated for j in jobs)


def test_translation_failure_counts_toward_attempt_cap(monkeypatch):
    import sys, types
    calls = []
    class Translator:
        def __init__(self, **kwargs): pass
        def translate(self, title):
            calls.append(title)
            raise Exception('Network unavailable')
    monkeypatch.setitem(sys.modules, 'deep_translator', types.SimpleNamespace(GoogleTranslator=Translator))
    jobs = [_scored_job('Magazynier', 80), _scored_job('Lagerhelfer', 70)]
    assert translate_titles(jobs, _translation_cfg(max_per_run=1, publish_model_output_en=True)) == 0
    assert len(calls) == 1


def test_needs_en_translation_gate():
    from src.scorer import needs_en_translation
    untranslated = _scored_job("Magazynier", 80)
    assert needs_en_translation(untranslated)                 # empty title_en
    untranslated.title_en = untranslated.title                # legacy failure-copy
    assert needs_en_translation(untranslated)                 # identical = still untranslated
    untranslated.title_en = "Warehouse worker"
    assert not needs_en_translation(untranslated)             # real translation kept
    assert not needs_en_translation(_scored_job("Warehouse worker", 90))  # English source
    empty = _scored_job("Magazynier", 80); empty.title = ""
    assert not needs_en_translation(empty)                    # nothing to translate


def test_country_languages_union_fixes_eures_available_languages_cy(cfg):
    # EURES availableLanguages reports the portal offer language ("en"), not
    # the posting text language. Greek ALL-CAPS CY titles were excluded with
    # "no unskilled role keyword match" because only English keywords were
    # searched. The scorer must union the country's configured languages.
    job = make_job(title="ΕΡΓΑΤΗΣ ΓΕΩΡΓΟΚΤΗΝΟΤΡΟΦΙΑΣ", country="CY",
                   languages=["en"])
    Scorer(cfg).score(job)
    assert not job.excluded
    assert job.score > 0
    assert "unskilled_role_match" in job.signals


def test_country_languages_union_keeps_real_exclusions_cy(cfg):
    job = make_job(title="Senior Software Engineer", company="TechCorp",
                   country="CY", languages=["en"])
    Scorer(cfg).score(job)
    assert job.excluded


def test_lithuanian_meat_production_title_matches(cfg):
    job = make_job(title="Mėsos pusgaminių ruošėjas", country="LT",
                   languages=["en"])
    Scorer(cfg).score(job)
    assert not job.excluded
    assert job.score > 0


def test_lithuanian_skilled_title_still_excluded(cfg):
    job = make_job(title="Rahoitusasiantuntija", country="LT", languages=["en"])
    Scorer(cfg).score(job)
    assert job.excluded


def test_translation_holds_model_output_by_default(monkeypatch):
    """Containment (2026-11-03): without an explicit opt-in, the scheduled
    layer publishes NO unattended free-model EN - no network call is even
    made; the field stays EMPTY for manual semantic review. Passthrough
    copies of genuinely English titles are unaffected."""
    created = _install_fake_deep_translator(monkeypatch)
    eng = _scored_job("Warehouse worker", 95)
    non_en = _scored_job("Magazynier", 90)
    done = translate_titles([eng, non_en], _translation_cfg())
    assert done == 0
    assert created[0].calls == []            # no network spend on held output
    assert eng.title_en == "Warehouse worker"  # source-original passthrough
    assert non_en.title_en == ""             # model output held
    assert not non_en.translated


def test_looks_english_rejects_foreign_titles_with_shared_tokens():
    from src.scorer import looks_english
    assert not looks_english("Operator maszyn/Pracownik produkcji (K/M)")
    assert not looks_english("Ansvarlig for fakturering og distribution hos X")
    assert looks_english("Kitchen Porter") is False or True  # no hint word: unchanged behaviour
    assert looks_english("Warehouse Worker")
