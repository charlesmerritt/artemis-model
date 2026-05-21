"""
pytest configuration and shared fixtures.
"""

import pytest
from pathlib import Path

# Project root — all tests resolve paths relative to this
PROJECT_ROOT = Path(__file__).parent.parent


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
