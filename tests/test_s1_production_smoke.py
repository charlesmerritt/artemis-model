"""Small read-only smoke test against the mounted S1 production sources."""

from pathlib import Path
import sys
import time
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

SMALL_PARCEL_ID = "00-2N-17-04358-000"
OVERSIZED_PARCEL_ID = "16-2N-18-10103-000"


def _load_parcel(paths: ProductionDataPaths, parcel_id: str, acreage_ceiling: float):
    parcels = gpd.read_file(
        paths.parcels,
        layer="FL_5_Co_Parcels",
        where=f"PARCELID = '{parcel_id}'",
    )
    assert parcels["PARCELID"].tolist() == [parcel_id]
    assert 0 < parcels["ACRES"].iloc[0] <= acreage_ceiling
    return parcels


def _load_streams(paths: ProductionDataPaths, parcels: gpd.GeoDataFrame):
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message=r"Measured \(M\) geometry types are not supported.*",
            category=UserWarning,
        )
        return gpd.read_file(f"zip://{paths.streams}", mask=parcels)


@pytest.mark.production_data
def test_real_data_small_aoi_builds_weighted_management_units():
    paths = ProductionDataPaths.from_root(Path("/mnt/d"))
    preflight_production_data(paths)

    parcels = _load_parcel(paths, SMALL_PARCEL_ID, acreage_ceiling=13.0)
    streams = _load_streams(paths, parcels)

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
    assert set(units["MU_ID"]) == set(weights["MU_ID"])
    assert weights.groupby("MU_ID")["WEIGHT"].sum().between(0.999999, 1.000001).all()


@pytest.mark.production_data
def test_real_oversized_parcel_exercises_seeded_subdivision():
    paths = ProductionDataPaths.from_root(Path("/mnt/d"))
    preflight_production_data(paths)
    parcels = _load_parcel(paths, OVERSIZED_PARCEL_ID, acreage_ceiling=250.0)
    streams = _load_streams(paths, parcels)
    lookup = load_treemap_lookup(paths.treemap_vat)

    started = time.perf_counter()
    units, weights = build_leto_management_units(
        paths.treemap,
        lookup,
        parcels,
        paths.ownership,
        streams,
        LetoSegmentationConfig(seed=7),
    )
    elapsed_seconds = time.perf_counter() - started

    assert len(units) >= 2
    assert units["Acres"].max() <= 200.0
    assert set(units["MU_ID"]) == set(weights["MU_ID"])
    assert weights.groupby("MU_ID")["WEIGHT"].sum().between(0.999999, 1.000001).all()
    print(
        "oversized production smoke: "
        f"parcel={OVERSIZED_PARCEL_ID} elapsed_seconds={elapsed_seconds:.3f} "
        f"units={len(units)} weight_rows={len(weights)}"
    )
