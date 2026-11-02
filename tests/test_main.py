import pytest

from main import main
from src.config import ConfigError, resolve_countries


def test_resolve_countries_tiers(cfg):
    assert resolve_countries(cfg, tier="1") == ["PL", "CZ", "DE", "LT", "HU", "RO", "SK", "NL"]
    assert len(resolve_countries(cfg, tier="all")) == 27
    assert resolve_countries(cfg, tier=None)[0] == "PL"


def test_resolve_countries_explicit_and_invalid(cfg):
    assert resolve_countries(cfg, countries=["pl", " de "]) == ["PL", "DE"]
    with pytest.raises(ConfigError):
        resolve_countries(cfg, countries=["XX"])


def test_cli_rejects_bad_source(tmp_path):
    assert main(["--sources", "nope"]) == 2


def test_cli_accepts_no_notify_flag():
    # The digest opt-out flag parses; bad sources still fail validation.
    assert main(["--sources", "nope", "--no-notify"]) == 2
