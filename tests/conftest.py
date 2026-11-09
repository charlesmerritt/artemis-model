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
def tpo_targets(config_dir):
    import yaml
    with open(config_dir / "tpo_targets.yaml") as f:
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

# The ArcPy reference fixture imports arcpy at module level; it is executed as a
# subprocess under an ArcGIS Pro Python (see test_s1_leto_arcpy_parity.py), never
# imported by the test suite itself. --doctest-modules would import it and fail.
collect_ignore = ["arcpy_reference"]


@pytest.fixture
def landfire_dist(tmp_path):
    """Write one LANDFIRE annual disturbance product in its CONUS folder layout; return the root.

    ``landfire_dist(version, yy, codes, legend, transform=..., nodata=32767)``: ``codes`` is
    the int16 raster and ``legend`` the rows of ``CSV_Data/<name>.csv`` as a DataFrame,
    written in ``encoding`` (LF2024 and LF2025 are ``"utf-8-sig"``: they start with a BOM).
    """
    import numpy as np
    import rasterio
    from rasterio.transform import Affine

    root = tmp_path / "landfire"

    def write(version, yy, codes, legend, *, transform=Affine(30, 0, 0, 0, -30, 30),
              nodata=32767, encoding="utf-8"):
        name = f"LF{version}_Dist{yy}"
        product = root / f"{name}_CONUS" / f"{name}_CONUS"
        (product / "Tif").mkdir(parents=True)
        (product / "CSV_Data").mkdir()
        codes = np.atleast_2d(np.asarray(codes, dtype=np.int16))
        with rasterio.open(product / "Tif" / f"{name}_CONUS.tif", "w", driver="GTiff",
                           height=codes.shape[0], width=codes.shape[1], count=1, dtype="int16",
                           crs="EPSG:5070", transform=transform, nodata=nodata) as dst:
            dst.write(codes, 1)
        legend.to_csv(product / "CSV_Data" / f"{name}.csv", index=False, encoding=encoding)
        return root

    return write
