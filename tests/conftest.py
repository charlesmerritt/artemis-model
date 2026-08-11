"""
pytest configuration and shared fixtures.
"""

import sys
import pytest
from pathlib import Path

# Project root — all tests resolve paths relative to this
PROJECT_ROOT = Path(__file__).parent.parent

# Make `pipeline.*` importable for every test module, regardless of which test runs
# first, so data-dependent tests reach `pipeline.data_access` without each file
# re-inserting the root itself. Modules loaded by file path (importlib — e.g.
# paint_fvs_to_raster) still execute their own absolute imports such as
# `pipeline.ids`, which fail without the repo root on the path even though
# `python -m pipeline...` works fine.
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
def ownership_policy(config_dir):
    import yaml
    with open(config_dir / "ownership_policy.yaml") as f:
        return yaml.safe_load(f)


@pytest.fixture
def management_regimes(config_dir):
    import yaml
    with open(config_dir / "management_regimes.yaml") as f:
        return yaml.safe_load(f)


@pytest.fixture
def fallback_treelists(config_dir):
    import yaml
    with open(config_dir / "fallback_treelists.yaml") as f:
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
