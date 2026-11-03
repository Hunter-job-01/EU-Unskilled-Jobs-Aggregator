"""Sector CV variant selection (v3 template split A/B/C).

Maps a vacancy to one of the owner's three evidence-supported sector CV
PDFs: warehouse-logistics (A) / factory-production (B) /
hospitality-general (C, includes agriculture and seasonal work).
The PDFs themselves are private files; this module only names variants.
Matching reuses the same stem families as role_labels (title folding),
and never invents a variant: no match -> None, and the card must say so.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from ..keywords import fold_text


class CVVariant(Enum):
    WAREHOUSE_LOGISTICS = "warehouse-logistics"
    FACTORY_PRODUCTION = "factory-production"
    HOSPITALITY_GENERAL = "hospitality-general"


@dataclass
class VariantMatch:
    variant: CVVariant
    matched_stem: str


_STEMS = [
    (CVVariant.WAREHOUSE_LOGISTICS, [
        "warehouse", "picker", "packer", "packing", "logistic",
        "magazyn", "sklad", "lager", "kommission", "raktar",
        "noliktav", "almacen", "magazzin", "armazem",
        "pakow", "balic", "verpack", "order picker", "sort",
    ]),
    (CVVariant.FACTORY_PRODUCTION, [
        "production", "factory", "assembly", "manufactur", "food process",
        "produkc", "vyrob", "gamyb", "montaz", "produktions",
    ]),
    (CVVariant.HOSPITALITY_GENERAL, [
        "kitchen", "dishwash", "housekeep", "clean", "hotel", "hospitality",
        "restaurant", "kuchenhilfe", "pomoc kuchen", "aide de cuisine",
        "aiuto cucina", "ayudante de cocina", "keukenhulp", "afwasser",
        "sprzat", "uklid", "uprat", "takar", "schoon", "nettoy", "limp",
        "pulizi", "siivoo", "maid", "room attendant",
        "farm", "harvest", "agricultur", "greenhouse", "fruit pick",
        "orchard", "zbiory", "szklarn", "ernte", "gewachs", "serre",
        "serra", "seasonal", "picking", "grape", "olive",
    ]),
]


def select_cv_variant(title: str, description: str = "") -> "VariantMatch | None":
    """Title first (stronger signal), then description. None if no match."""
    title_f = fold_text(title or "")
    desc_f = fold_text(description or "")
    for variant, stems in _STEMS:
        for stem in stems:
            if stem in title_f:
                return VariantMatch(variant, stem)
    for variant, stems in _STEMS:
        for stem in stems:
            if stem in desc_f:
                return VariantMatch(variant, stem)
    return None
