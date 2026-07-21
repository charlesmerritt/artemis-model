"""Small read-only smoke test against the mounted S1 production sources."""

from pathlib import Path
import sys
import warnings

import geopandas as gpd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline.s1_initial_state.data_sources import (
    ProductionDataPaths,
    load_treemap_lookup,
    preflight_production_data,
)
from pipeline.s1_initial_state.segmentation.leto import (
    LetoSegmentationConfig,
    build_leto_management_units,
)


@pytest.mark.production_data
def test_real_data_small_aoi_builds_weighted_management_units():
    paths = ProductionDataPaths.from_root(Path("/mnt/d"))
    preflight_production_data(paths)

    # This single 12-acre parcel is large enough to survive LETO's 5-acre cleanup.
    parcels = gpd.read_file(
        paths.parcels,
        layer="FL_5_Co_Parcels",
        rows=slice(3, 4),
    )
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message=r"Measured \(M\) geometry types are not supported.*",
            category=UserWarning,
        )
        streams = gpd.read_file(f"zip://{paths.streams}", mask=parcels)

    lookup = load_treemap_lookup(paths.treemap_vat)
    units, weights = build_leto_management_units(
        paths.treemap,
        lookup,
        parcels,
        paths.ownership,
        streams,
        LetoSegmentationConfig(seed=7),
    )

    assert not units.empty
    assert not weights.empty
    assert weights.groupby("MU_ID")["WEIGHT"].sum().between(0.999999, 1.000001).all()
