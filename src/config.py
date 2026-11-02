"""Configuration loading and country/tier resolution."""
from __future__ import annotations

import os
from typing import Optional

import yaml

DEFAULT_CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config", "config.yaml"
)

VALID_TIERS = (1, 2, 3)


class ConfigError(Exception):
    """Raised when the configuration or CLI selection is invalid."""


def load_config(path: Optional[str] = None) -> dict:
    cfg_path = path or DEFAULT_CONFIG_PATH
    if not os.path.isfile(cfg_path):
        raise ConfigError(f"config file not found: {cfg_path}")
    with open(cfg_path, "r", encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)
    for section in ("countries", "tiers", "keywords", "scoring", "batch", "storage"):
        if section not in cfg:
            raise ConfigError(f"config missing required section: {section}")
    return cfg


def tier_of(cfg: dict, country_code: str) -> Optional[int]:
    for tier, members in cfg["tiers"].items():
        if country_code in members:
            return int(tier)
    return None


def resolve_countries(
    cfg: dict,
    tier: Optional[str] = None,
    countries: Optional[list] = None,
) -> list:
    """Resolve the CLI selection into an ordered list of country codes.

    ``countries`` (explicit ISO codes) wins over ``tier``. ``tier`` accepts
    "1", "2", "3", "all" or None (defaults to tier 1, the daily batch).
    """
    all_countries = list(cfg["countries"].keys())
    if countries:
        resolved = []
        invalid = []
        for code in countries:
            code = code.strip().upper()
            (resolved if code in cfg["countries"] else invalid).append(code)
        if invalid:
            raise ConfigError(
                "unknown country code(s): {}. Valid: {}".format(
                    ", ".join(invalid), ", ".join(all_countries)
                )
            )
        return resolved
    if tier in (None, "1"):
        wanted = [1]
    elif tier == "all":
        wanted = [1, 2, 3]
    elif tier in ("2", "3"):
        wanted = [int(tier)]
    else:
        raise ConfigError(f"invalid tier: {tier!r} (use 1, 2, 3 or all)")
    out = []
    for t in wanted:
        out.extend([c for c in cfg["tiers"].get(t, []) if c in cfg["countries"]])
    return out
