"""v6 stage C (L2) strict-language gate. Self-contained."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import yaml  # noqa: E402

from src.language_gate import LanguageGate  # noqa: E402
from src.scorer import Scorer  # noqa: E402
from src.models import Job  # noqa: E402

_CFG = yaml.safe_load(open(os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "config", "config.yaml"), encoding="utf-8"))
GATE = LanguageGate(_CFG["language_requirements"])


def test_strict_requirements_detected():
    assert GATE.strict_requirement(
        "Warehouse worker. Fluent German required.") == "german"
    assert GATE.strict_requirement(
        "Magazynier. Wymagana biegla znajomosc jezyka niemieckiego.") == "german"
    assert GATE.strict_requirement(
        "Orderpicker. Vloeiend Nederlands vereist.") == "dutch"
    assert GATE.strict_requirement(
        "English C1 level required for this role") == "english"
    assert GATE.strict_requirement(
        "Sehr gute Deutschkenntnisse vorausgesetzt.") == "german"


def test_negations_and_offers_never_match():
    # Negated requirement.
    assert GATE.strict_requirement(
        "Keine Deutschkenntnisse erforderlich.") is None
    assert GATE.strict_requirement(
        "No German required for this role.") is None
    assert GATE.strict_requirement(
        "Fluent German not required, training provided.") is None
    assert GATE.strict_requirement(
        "Kein Englisch C1 erforderlich.") is None
    # Offered training carries no requirement verb.
    assert GATE.strict_requirement(
        "Wir bieten kostenlose Deutschkurse fuer B1 Niveau.") is None
    # Level term without a language name.
    assert GATE.strict_requirement(
        "A B2 certificate is required.") is None
    # Language name without a level.
    assert GATE.strict_requirement(
        "Grundkenntnisse Deutsch sind von Vorteil.") is None
    # Language name + level without a requirement verb.
    assert GATE.strict_requirement(
        "Our team speaks fluent German and Polish.") is None


def test_scorer_excludes_strict_requirement():
    scorer = Scorer(_CFG)
    job = Job(title="Lagerhelfer", company="X", country="DE",
              source="eures", url="https://example.invalid/1",
              description="Lagerhelfer gesucht. "
                          "Fließend Deutsch erforderlich.")
    scorer.score(job)
    assert job.excluded
    assert "strict local language requirement" in job.exclusion_reason
    assert job.score == 0


def test_scorer_keeps_negated_requirement():
    scorer = Scorer(_CFG)
    job = Job(title="Lagerhelfer", company="X", country="DE",
              source="eures", url="https://example.invalid/2",
              description="Lagerhelfer gesucht. "
                          "Keine Deutschkenntnisse erforderlich.")
    scorer.score(job)
    assert not job.excluded
