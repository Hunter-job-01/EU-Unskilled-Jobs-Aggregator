import os
import sys

import pytest
import yaml

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.config import load_config  # noqa: E402


@pytest.fixture(scope="session")
def cfg():
    return load_config()


@pytest.fixture(scope="session")
def matrix(cfg):
    from src.keywords import KeywordMatrix
    return KeywordMatrix(cfg["keywords"])


def pytest_collection_modifyitems(config, items):
    if config.getoption("-m") and "live" in config.getoption("-m"):
        return
    skip_live = pytest.mark.skip(reason="live network test; run with -m live")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip_live)
