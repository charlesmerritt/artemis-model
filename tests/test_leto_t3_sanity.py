"""Common sense on real data: the ``leto`` stage on the T3 county, Union (12125), v4 raster run.

Hand-built layers pin the rules; this checks the stage's output reads like a forest. It runs
the county twice, speckle ``keep`` (LETO's own units) and ``merge_delete`` (the default), and skips
when the inputs are not on this machine. ``ARTEMIS_DATA`` points at a ``data/`` folder (the
repo's own by default, so set it from a worktree):

    ARTEMIS_DATA=/path/to/artemis-model/data uv run pytest -q tests/test_leto_t3_sanity.py
"""

import dataclasses
from dataclasses import dataclass
import os
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest

from pipeline.data_access import data_paths, under_data_root
from pipeline.leto_speckle import SpeckleAction, UnitClass
from pipeline.leto_stage import (
    AREA_ACCOUNTING,
    INV_YEAR,
    QA_DIR,
    SPECKLE,
    UNITS_GPKG,
    UNITS_LAYER,
    Origin,
    load_settings,
    run_leto,
)
from pipeline.s1_initial_state.add_back_ids import read_vat
from pipeline.s3_management.owner_classes import MASKED

DATA = Path(os.environ.get("ARTEMIS_DATA", Path(__file__).resolve().parents[1] / "data"))
REPAIR = DATA / "processed" / "improved-rasters-evt2022-gate-v4" / "aoi_5county"
REGION = DATA / "regions" / "fl"
DATA_ROOT = DATA / "r2_cache" if (DATA / "r2_cache").is_dir() else None   # else the drive
T3 = "12125"
FIRST_DETECTED_CUT = 2001      # Hansen's first loss year: no add-back stand is older than its record

pytestmark = [
    pytest.mark.production_data,
    pytest.mark.skipif(not (REPAIR / "manifest.json").exists() or not REGION.is_dir(),
                       reason=f"the v4 raster run and Florida region inputs are not under {DATA}"),
]


@pytest.fixture(scope="module")
def t3(tmp_path_factory):
    raw = data_paths()["raw"]
    vat = read_vat(under_data_root(raw["treemap_2022"]["tif"] + ".vat.dbf", DATA_ROOT))
    counties = under_data_root(raw["counties"]["p010g_shp"], DATA_ROOT)
    base = load_settings(T3)
    runs = {}
    for action in (SpeckleAction.KEEP, base.speckle.action):        # LETO's own, the default
        run_dir = tmp_path_factory.mktemp(action.value)
        settings = dataclasses.replace(base, speckle=dataclasses.replace(base.speckle, action=action))
        run_leto(run_dir, repair=REPAIR, fips=[T3], vat=vat, counties=counties,
                 region_dir=REGION, settings=settings)
        units = gpd.read_file(run_dir / UNITS_GPKG, layer=UNITS_LAYER)
        runs[action] = Run(units[units["owner_class"] != MASKED],
                           pd.read_csv(run_dir / QA_DIR / SPECKLE).iloc[0],
                           pd.read_csv(run_dir / QA_DIR / AREA_ACCOUNTING).iloc[0])
    return runs


@dataclass(frozen=True)
class Run:
    units: gpd.GeoDataFrame                   # scheduled units: not masked
    speckle: pd.Series                        # the county's qa/leto/speckle.csv line
    accounting: pd.Series                     # its qa/leto/area_accounting.csv line


def _default_run(t3) -> Run:
    return t3[load_settings(T3).speckle.action]


def _default(t3) -> gpd.GeoDataFrame:
    return _default_run(t3).units


def _both(t3):
    return t3[SpeckleAction.KEEP].units, _default(t3)


def _acres(units, mask=None) -> float:
    return float(units.loc[mask, "Acres"].sum() if mask is not None else units["Acres"].sum())


# ── add-back land is young ──────────────────────────────────────────────────────────────


def test_add_back_forest_averages_a_young_stand_age(t3):
    young = _default(t3)
    young = young[young["origin"] == Origin.YOUNG_PLOT]
    assert len(young) > 0
    mean = np.average(young["stand_age"], weights=young["Acres"])
    assert 3.0 <= mean <= 8.0, f"add-back acres average {mean:.1f} yr"


def test_no_add_back_stand_is_older_than_the_cut_record(t3):
    young = _default(t3)
    young = young[young["origin"] == Origin.YOUNG_PLOT]
    assert young["stand_age"].notna().all()
    assert young["stand_age"].between(0, INV_YEAR - FIRST_DETECTED_CUT).all()


# ── speckle merge cleans up, and moves little ──────────────────────────────────────────


def test_the_default_leaves_no_speckle(t3):
    keep, merge = _both(t3)
    minimum = load_settings(T3).speckle.minimum_unit_acres
    assert (keep["Acres"] < minimum).sum() > 0
    assert (merge["Acres"] < minimum).sum() == 0
    assert len(merge) < len(keep)


def test_most_speckle_merges_rather_than_being_deleted(t3):
    qa = _default_run(t3).speckle
    assert qa["speckle_deleted"] < 0.5 * qa["speckle_before"]


def test_deleted_speckle_is_under_one_percent_of_the_county(t3):
    keep, merge = _both(t3)
    deleted = _default_run(t3).speckle["speckle_deleted_acres"]
    assert deleted < 0.01 * _acres(keep), f"{deleted:.0f} ac deleted"


def test_merged_plus_deleted_acres_equal_letos_own(t3):
    keep, merge = _both(t3)
    deleted = _default_run(t3).accounting["deleted_speckle_acres"]
    assert deleted == pytest.approx(_default_run(t3).speckle["speckle_deleted_acres"], abs=1e-3)
    assert _acres(merge) + deleted == pytest.approx(_acres(keep), abs=0.01)


def test_under_one_percent_of_add_back_acres_joins_mature_managed_land(t3):
    """ADR 0005's step order often leaves a young speckle one neighbour, and it can be mature.

    Its acres then take a mature age and become harvest-eligible (the ADR 0003 failure), so
    bound them: each merged young-plot speckle is followed to the unit that absorbed it.
    """
    keep, merge = _both(t3)
    minimum = load_settings(T3).speckle.minimum_unit_acres
    young = keep[(keep["origin"] == Origin.YOUNG_PLOT) & (keep["Acres"] < minimum)
                 & ~keep["unit_id"].isin(merge["unit_id"])]
    absorbed = gpd.sjoin(young.set_geometry(young.representative_point())[["Acres", "geometry"]],
                         merge[["origin", "unit_class", "geometry"]], predicate="within")
    assert len(young) - len(absorbed) <= _default_run(t3).speckle["speckle_deleted"]   # the rest
    mature = absorbed[(absorbed["origin"] == Origin.TREEMAP)
                      & (absorbed["unit_class"] == UnitClass.MANAGED)]
    add_back = _acres(keep, keep["origin"] == Origin.YOUNG_PLOT)
    assert mature["Acres"].sum() < 0.01 * add_back, (
        f"{mature['Acres'].sum():.1f} of {add_back:.0f} add-back ac joined mature managed land")


def test_riparian_acres_shrink_only_by_the_riparian_speckle_deleted(t3):
    """Riparian area never joins managed land, so only its deleted speckle can leave it."""
    keep, merge = _both(t3)
    before, after = (_acres(u, u["unit_class"] == UnitClass.RIPARIAN) for u in (keep, merge))
    assert after >= before - _default_run(t3).speckle["speckle_deleted_riparian_acres"] - 0.01


def test_speckle_merge_moves_under_one_percent_of_acres_between_owner_classes(t3):
    keep, merge = (u.groupby("owner_class")["Acres"].sum() for u in _both(t3))
    moved = merge.sub(keep, fill_value=0.0).abs().sum() / 2
    assert moved < 0.01 * keep.sum(), f"{moved:.0f} ac changed owner class"


# ── the landscape is plausible ──────────────────────────────────────────────────────────


def test_every_scheduled_unit_has_a_stand_age_and_a_representative_plot(t3):
    units = _default(t3)
    assert units["stand_age"].notna().all()
    assert units["PLT_CN"].notna().all() and (units["PLT_CN"] != "").all()


def test_riparian_and_unknown_owner_shares_are_plausible(t3):
    units = _default(t3)
    total = _acres(units)
    riparian = _acres(units, units["unit_class"] == UnitClass.RIPARIAN) / total
    unknown = _acres(units, units["owner_class"] == "unknown") / total
    assert 0.01 <= riparian <= 0.15, f"riparian {riparian:.1%}"
    assert unknown < 0.05, f"unknown owner {unknown:.1%}"


def test_one_cell_speckle_is_a_small_share_of_the_county(t3):
    units = _default(t3)
    cell_acres = 900 / 4046.8564224
    assert _acres(units, units["Acres"] < cell_acres) < 0.005 * _acres(units)


def test_plot_control_numbers_are_exact_strings(t3):
    plots = _default(t3)["PLT_CN"]
    assert pd.api.types.is_string_dtype(plots)
    fia = plots[~plots.str.startswith("Y")]
    assert fia.str.fullmatch(r"\d+").all()       # never '1.23e+14' or '123.0'
