"""Greek role-keyword inflection coverage (CY thin-count fix). Self-contained."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import yaml  # noqa: E402

from src.keywords import fold_text  # noqa: E402


def _roles():
    cfg = yaml.safe_load(open(os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "config", "config.yaml"), encoding="utf-8"))
    return cfg["keywords"]["roles"]["el"]


def test_plural_and_feminine_cleaner_forms_present():
    roles = _roles()
    for form in ("καθαριστες", "καθαριστρια", "καθαριστριες", "καμαριερες"):
        assert form in roles, form


def test_live_observed_titles_match():
    roles = _roles()
    for title in ("ΚΑΘΑΡΙΣΤΕΣ/ΣΤΡΙΕΣ", "ΚΑΘΑΡΙΣΤΡΙΑ/ΗΣ",
                  "ΚΑΜΑΡΙΕΡΕΣ/MAIDS", "ΚΑΜΑΡΙΕΡΕΣ"):
        folded = fold_text(title)
        assert any(r in folded for r in roles), title
