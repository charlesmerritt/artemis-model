"""
pytest configuration and shared fixtures.
"""

import sys

import pytest
from pathlib import Path

# Project root — all tests resolve paths relative to this
PROJECT_ROOT = Path(__file__).parent.parent

# Importable `pipeline.*` for every test module, so data-dependent tests can reach
# pipeline.data_access instead of each file re-inserting the root itself.
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture
def project_root():
    return PROJECT_ROOT


@pytest.fixture
def config_dir():
    return PROJECT_ROOT / "config"


@pytest.fixture
def data_dir():
    return PROJECT_ROOT / "data"


@pytest.fixture
def projection_config(config_dir):
    import yaml
    with open(config_dir / "projection.yaml") as f:
        return yaml.safe_load(f)


@pytest.fixture
def bmp_rules(config_dir):
    import yaml
    with open(config_dir / "bmp_rules.yaml") as f:
        return yaml.safe_load(f)


@pytest.fixture
def extent_geojson(config_dir):
    import json
    with open(config_dir / "extent.geojson") as f:
        return json.load(f)


@pytest.fixture(scope="session")
def data_access():
    """Path resolver that answers from the /mnt/d drive or the R2 mirror.

    Data-dependent tests use this instead of a bare `path.exists()`, so they run
    wherever either source is reachable and skip only when neither is.
    """
    from pipeline import data_access as module
    return module
