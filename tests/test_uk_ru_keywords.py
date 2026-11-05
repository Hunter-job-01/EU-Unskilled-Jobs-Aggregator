"""uk/ru keyword coverage for the Telegram channel source (v6 stage E).
Self-contained; terms measured from live t.me/s posts 2026-11-05."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import yaml  # noqa: E402

from src.keywords import KeywordMatrix  # noqa: E402
from src.scorer import Scorer  # noqa: E402
from src.models import Job  # noqa: E402

_CFG = yaml.safe_load(open(os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "config", "config.yaml"), encoding="utf-8"))
MATRIX = KeywordMatrix(_CFG["keywords"])


def test_uk_ru_roles_match_live_style_titles():
    assert MATRIX.match_roles("ПРАЦІВНИК СКЛАДУ — м. Psary", ["uk"])
    assert MATRIX.match_roles("Работник склада, Прага", ["ru"])
    assert MATRIX.match_roles("Упаковщик на производство", ["ru"])
    assert MATRIX.match_roles("Пакування та сортування товару", ["uk"])


def test_exclusive_citizenship_disqualifies():
    assert MATRIX.match_disqualifiers("Тільки громадяни ЄС")
    assert MATRIX.match_disqualifiers("Только граждане ЕС")
    assert MATRIX.match_disqualifiers("Тільки з тимчасовим захистом")


def test_inclusive_nationality_lists_never_disqualify():
    # Measured live 2026-11-05: "граждан евросоюза" appears INSIDE
    # inclusive lists; a bare mention must not exclude.
    assert not MATRIX.match_disqualifiers(
        "Принимаем граждан украины, молдовы, беларуси, граждан евросоюза")
    assert not MATRIX.match_disqualifiers(
        "Молдова, Армения, Беларусь, Украина, граждан ЕВРОСОЮЗА (с русским языком)")


def test_scorer_keeps_uk_telegram_style_post():
    scorer = Scorer(_CFG)
    job = Job(title="ПРАЦІВНИК СКЛАДУ — м. Psary", company="Agency",
              country="PL", source="telegram_chan",
              url="https://t.me/chan/101",
              description="Працівник складу. Без знання мови. "
                          "Безкоштовне житло. 27 zł/h.",
              languages=["uk"])
    scorer.score(job)
    assert not job.excluded, job.exclusion_reason
    assert job.score > 0
