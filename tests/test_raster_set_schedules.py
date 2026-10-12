"""Pixel-class landscapes over the improved rasters, for experiments/2026-10-12_raster-set-schedules.

The 2026-10-08 open-menu schedule ran on pixel classes (`TreeMap plot x county x owner`) of the
*published* TreeMap. These tests pin the two things that change on an improved raster: a
provenance-4 pixel is its own class, initialised from the scaled establishment list rather
than the donor plot's trees (AGENTS.md, ADR 0002), and that class survives the riparian carve.
"""

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

EXP = Path(__file__).resolve().parents[1] / "experiments/2026-10-12_raster-set-schedules"
spec = importlib.util.spec_from_file_location("raster_set_landscape", EXP / "landscape.py")
L = importlib.util.module_from_spec(spec)
sys.modules["raster_set_landscape"] = L
spec.loader.exec_module(L)

NODATA = 0
COUNTIES = {1: "Baker", 2: "Union"}
SMZ = {1: "perennial_small"}


def grids():
    """A 3x4 landscape: two counties, a young patch, a stream down column 1."""
    tm = np.array([[11, 11, 12, NODATA],
                   [11, 11, 12, 12],
                   [13, 11, 12, 12]], dtype=np.int64)
    prov = np.array([[1, 1, 4, 0],
                     [1, 1, 4, 1],
                     [1, 1, 1, 1]], dtype=np.uint8)
    county = np.array([[1, 1, 1, 0],
                       [1, 1, 1, 2],
                       [1, 1, 2, 2]], dtype=np.uint8)
    owner = np.array([[3, 3, 3, 3],
                      [3, 3, 4, 4],
                      [1, 3, 4, 4]], dtype=np.uint8)
    smz = np.array([[0, 1, 0, 0],
                    [0, 1, 0, 0],
                    [0, 1, 0, 0]], dtype=np.uint8)
    return tm, prov, county, owner, smz


def pieces():
    tm, prov, county, owner, smz = grids()
    return L.pixel_pieces(tm, NODATA, prov, county, owner, smz, COUNTIES, SMZ)


def test_a_provenance_4_pixel_is_its_own_scaled_young_class():
    p = pieces()
    young = p[p["origin"] == L.PixelOrigin.SCALED_YOUNG]
    assert set(young["tm_id"]) == {12}
    assert young["pixel_count"].sum() == 2
    # The same TM_ID on a published pixel stays a TreeMap class.
    assert p[(p["tm_id"] == 12) & (p["origin"] == L.PixelOrigin.TREEMAP)]["pixel_count"].sum() == 3


def test_pixels_off_the_counties_or_off_treemap_are_dropped():
    p = pieces()
    assert p["pixel_count"].sum() == 11       # 12 cells, one NODATA (which is also off-county)


def test_without_a_provenance_raster_every_pixel_is_a_treemap_class():
    tm, _, county, owner, smz = grids()
    p = L.pixel_pieces(tm, NODATA, None, county, owner, smz, COUNTIES, SMZ)
    assert set(p["origin"]) == {L.PixelOrigin.TREEMAP}


def test_unit_ids_keep_the_published_form_and_mark_young_classes():
    assert L.unit_id(11, L.PixelOrigin.TREEMAP, "Baker", 3) == "TM11_Baker_O3"
    assert L.unit_id(12, L.PixelOrigin.SCALED_YOUNG, "Baker", 3) == "YTM12_Baker_O3"


def test_a_young_class_reads_its_own_fvs_stand_never_the_donor_plot():
    xwalk = pd.DataFrame({"tm_id": [12], "PLT_CN": ["718604473290487"], "FORTYPCD": [141]})
    young = L.stand_plot(pd.DataFrame({"tm_id": [12], "origin": [L.PixelOrigin.SCALED_YOUNG]}), xwalk)
    published = L.stand_plot(pd.DataFrame({"tm_id": [12], "origin": [L.PixelOrigin.TREEMAP]}), xwalk)
    assert young["PLT_CN"].iat[0] == "Y12"
    assert published["PLT_CN"].iat[0] == "718604473290487"
    # Forest type still comes from the donor: it sets the menu's branch.
    assert young["FORTYPCD"].iat[0] == 141


def test_the_carve_conserves_area_and_drops_unattributed_owners():
    tm, prov, county, owner, smz = grids()
    p = pieces()
    rip = L.riparian_stands(L.riparian_pixels(tm, NODATA, prov, county, owner, smz, COUNTIES))
    upland = L.upland_remainders(p)
    attributed = p[p["owner_code"].isin(L.OWNER_CODES)]["pixel_count"].sum()
    assert upland["pixel_count"].sum() + rip["pixel_count"].sum() == attributed
    # Owner class 1 (non-forest) never becomes a stand.
    assert not (upland["owner_code"] == 1).any()


def test_riparian_stands_split_at_county_and_owner_and_take_the_majority_class():
    tm, prov, county, owner, smz = grids()
    rip = L.riparian_stands(L.riparian_pixels(tm, NODATA, prov, county, owner, smz, COUNTIES))
    # One 8-connected corridor down column 1, all Baker, all owner 3: one stand of 3 pixels.
    assert len(rip) == 1
    assert rip["pixel_count"].iat[0] == 3
    assert rip["tm_id"].iat[0] == 11
    assert rip["unit_id"].iat[0] == "RIP1_Baker_O3"


def test_a_majority_tie_breaks_to_the_smallest_tm_id_as_2026_08_24_did():
    flat = pd.DataFrame({"corridor_id": [1, 1], "county": ["Baker"] * 2, "owner_code": [3, 3],
                         "tm_id": [20, 10], "origin": [L.PixelOrigin.TREEMAP] * 2})
    assert L.riparian_stands(flat)["tm_id"].iat[0] == 10


def test_young_fvs_inputs_expand_to_the_establishment_list_tpa():
    lists = pd.DataFrame({
        "TM_ID": ["12", "12", "15"], "PLT_CN": ["7186", "7186", "9999"],
        "STATUSCD": [1, 1, 1], "TPA_UNADJ": [300.0, 81.0, 500.0], "SPCD": [111, 131, 611],
        "DIA": [2.94, 2.94, 3.1], "HT": [15.7, 15.7, 20.0], "CR": [np.nan] * 3,
        "establishment_mode": ["scaled_young", "scaled_young", "donor_as_is"],
        "establishment_age": [5, 5, np.nan],
    })
    donors = pd.DataFrame({"STAND_CN": ["7186", "9999"], "STAND_ID": ["a", "b"], "AGE": [40.0, 30.0],
                           "BASAL_AREA_FACTOR": [-24.0, -24.0], "INV_PLOT_SIZE": [300.0, 300.0],
                           "BRK_DBH": [5.0, 5.0], "NUM_PLOTS": [4.0, 4.0], "SITE_INDEX": [90.0, 80.0]})
    stand, trees = L.young_fvs_inputs(lists, donors)
    # Only scaled_young lists become stands; donor_as_is pixels read the donor plot itself.
    assert stand["STAND_CN"].tolist() == ["Y12"]
    assert stand["AGE"].iat[0] == 5
    assert stand["SITE_INDEX"].iat[0] == 90.0          # site comes from the donor plot
    per_acre = trees["TREE_COUNT"] * stand["INV_PLOT_SIZE"].iat[0] / stand["NUM_PLOTS"].iat[0]
    assert per_acre.tolist() == pytest.approx([300.0, 81.0])
    assert trees["SPECIES"].tolist() == ["111", "131"]
    assert (trees["HISTORY"] == 1).all()


def test_young_trees_at_or_above_the_breakpoint_are_refused():
    lists = pd.DataFrame({"TM_ID": ["12"], "PLT_CN": ["7186"], "STATUSCD": [1],
                          "TPA_UNADJ": [300.0], "SPCD": [111], "DIA": [6.0], "HT": [30.0],
                          "CR": [np.nan], "establishment_mode": ["scaled_young"],
                          "establishment_age": [5]})
    donors = pd.DataFrame({"STAND_CN": ["7186"], "STAND_ID": ["a"], "AGE": [40.0],
                           "BASAL_AREA_FACTOR": [-24.0], "INV_PLOT_SIZE": [300.0],
                           "BRK_DBH": [5.0], "NUM_PLOTS": [4.0]})
    with pytest.raises(ValueError, match="breakpoint"):
        L.young_fvs_inputs(lists, donors)
