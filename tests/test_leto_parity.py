"""Behaviours pinned to LETO v1 (aauslander480/Leto, LETO_v1_project @ d2b92ee).

Each test names the LETO function it reproduces; research/leto_parity/README.md lists the gaps.
"""

import numpy as np
import pandas as pd
import pytest
from shapely.geometry import LineString, box

from pipeline import leto_attributes, leto_ca, leto_donors, leto_riparian

# --- Stage 2: CA configuration and remaining small stands --------------------------------


def test_default_cfg_is_the_verified_maine_baseline():
    # configs/maine_test.py "segmentation" + "small_stands"
    cfg = leto_ca.DEFAULT_CFG
    assert (cfg["maximum_iterations"], cfg["convergence_threshold"]) == (40, 0.01)
    assert cfg["shared_edge_bonus"] == 0.2
    assert cfg["similar_merge_min_shared_edges"] == 2
    assert cfg["remaining_small_stand_action"] is leto_ca.SmallStandAction.FORCE_MERGE


def test_seed_cells_round_like_leto():
    # stage2 main(): initial_seed_cells = max(1, int(round(INITIAL_SEED_ACRES / cell_acres)))
    assert leto_ca.seed_cells(100.0, 0.2224) == 450


def _two_segments():
    labels = np.zeros((4, 6), dtype=np.int32)
    labels[:, :4] = 1          # 16-cell stand
    labels[1:3, 4] = 2         # 2-cell sliver touching stand 1
    labels[0, 5] = 3           # isolated 1-cell fragment (no rook neighbour)
    labels[1:3, 5] = 0
    return labels


def _features(labels):
    zeros = np.zeros(labels.shape, dtype=np.float32)
    return {n: zeros for n in ("STDAGE", "BALIVE", "QMD", "TPA")}


def test_force_merge_ignores_the_maximum_size_and_keeps_isolated_fragments():
    # stage2 main() "HANDLING REMAINING UNDERSIZED SEGMENTS": force_merge ->
    # merge_small_segments(..., maximum_cells=None); a fragment with no neighbour survives.
    labels = _two_segments()
    out = leto_ca.resolve_small_stands(
        labels, _features(labels), np.ones_like(labels), np.zeros_like(labels),
        minimum_cells=4, action=leto_ca.SmallStandAction.FORCE_MERGE, cfg=leto_ca.DEFAULT_CFG)
    assert out[1, 4] == out[0, 0]           # sliver joined the big stand
    assert out[0, 5] > 0 and out[0, 5] != out[0, 0]   # isolated fragment kept as its own stand


def test_delete_and_retain_actions():
    labels = _two_segments()
    args = (_features(labels), np.ones_like(labels), np.zeros_like(labels))
    deleted = leto_ca.resolve_small_stands(labels, *args, minimum_cells=4,
                                           action=leto_ca.SmallStandAction.DELETE,
                                           cfg=leto_ca.DEFAULT_CFG)
    assert deleted[1, 4] == 0 and deleted[0, 5] == 0
    retained = leto_ca.resolve_small_stands(labels, *args, minimum_cells=4,
                                            action=leto_ca.SmallStandAction.RETAIN,
                                            cfg=leto_ca.DEFAULT_CFG)
    assert len(np.unique(retained[retained > 0])) == 3


# --- Stage 1: FIA-first segmentation attributes ------------------------------------------


@pytest.fixture
def fia():
    cond = pd.DataFrame({
        "PLT_CN": ["1", "1", "2", "3"],
        "CONDID": [1, 2, 1, 1],
        "COND_STATUS_CD": [1, 1, 1, 2],
        "CONDPROP_UNADJ": [0.4, 0.6, 1.0, 1.0],
        "STDAGE": [10, 30, 50, 70],
        "FORTYPCD": [161, 162, 503, 999],
        "BALIVE": [20.0, 80.0, 100.0, 1.0],
    })
    trees = pd.DataFrame({
        "PLT_CN": ["1", "1", "1", "1", "2"],
        "CONDID": [2, 2, 1, 2, 1],
        "STATUSCD": [1, 1, 1, 2, 1],
        "DIA": [6.0, 8.0, 20.0, 9.0, 0.0],
        "TPA_UNADJ": [6.0, 6.0, 6.0, 6.0, 6.0],
    })
    return cond, trees


def test_plot_lookup_uses_the_dominant_forest_condition(fia):
    # stage1 select_dominant_conditions + read_and_summarize_trees
    cond, trees = fia
    lookup = leto_attributes.plot_lookup(cond, trees).set_index("PLT_CN")
    assert lookup.loc["1", ["STDAGE", "FORTYPCD", "BALIVE"]].tolist() == [30, 162, 80.0]
    assert lookup.loc["1", "TPA"] == 12.0                     # live, dominant condition only
    assert lookup.loc["1", "QMD"] == pytest.approx(np.sqrt((36 + 64) / 2))
    assert np.isnan(lookup.loc["2", "TPA"])                   # DIA 0 is not a usable tree
    assert "3" not in lookup.index                            # not a forest condition


def test_value_records_prefer_fia_and_fall_back_to_the_treemap_rat(fia):
    # stage1 read_raster_value_lookup: FIA first, RAT (TPA_LIVE for TPA) when FIA is absent
    cond, trees = fia
    vat = pd.DataFrame({"Value": [10, 20, 30], "PLT_CN": ["1", "2", "4"],
                        "FORTYPCD": [1, 2, 3], "BALIVE": [1.0, 2.0, 3.0],
                        "QMD": [1.0, 2.0, 3.0], "TPA_LIVE": [5.0, 7.0, 9.0]})
    records = leto_attributes.value_records(vat, leto_attributes.plot_lookup(cond, trees))
    assert records.loc[10, "FORTYPCD"] == 162
    assert records.loc[20, "TPA"] == 7.0                     # FIA had no usable trees
    assert records.loc[30, "BALIVE"] == 3.0 and np.isnan(records.loc[30, "STDAGE"])


def test_attribute_rasters_fill_from_the_nearest_cell_and_store_like_leto():
    # stage1 write_lookup_raster: nearest assigned cell fills gaps; values stored rounded,
    # BALIVE/QMD/TPA in tenths
    tm = np.array([[10, 10, 30]], dtype=np.int64)
    records = pd.DataFrame({"STDAGE": [12.6, np.nan], "FORTYPCD": [161, 162],
                            "BALIVE": [28.74, 50.0], "QMD": [6.06, 7.0], "TPA": [124.71, 90.0]},
                           index=pd.Index([10, 30], name="Value"))
    grids, provenance = leto_attributes.attribute_rasters(tm, tm > 0, records)
    assert grids["STDAGE"].tolist() == [[13, 13, 13]]          # nearest fill, not the median
    assert grids["BALIVE"][0, 0] == pytest.approx(28.7)
    assert grids["TPA"][0, 0] == pytest.approx(124.7)
    assert provenance["STDAGE"].tolist() == [[1, 1, 3]]


# --- Stage 3/4: donor weights ------------------------------------------------------------


def test_management_units_inherit_their_parent_segments_donor_weights():
    # stage3 build_weights_table (per SEG_ID) + expand_parent_weights_to_management_units
    seg = np.array([[1, 1, 1, 1]])
    tm = np.array([[10, 10, 10, 20]])
    # MUs 1 (cells 0-1) and 2 (cells 2-3) are the riparian split of parent 1
    weights = leto_donors.parent_weights(seg, tm, {10: "A", 20: "B"})
    expanded = leto_donors.expand_to_units(weights, pd.DataFrame({"MU_ID": [1, 2],
                                                                   "PARENT_SEG": [1, 1]}))
    for mu_id in (1, 2):
        got = expanded[expanded["MU_ID"] == mu_id].set_index("PLT_CN")["WEIGHT"]
        assert got.to_dict() == {"A": 0.75, "B": 0.25}


def test_donor_filter_keeps_the_largest_donor_when_all_fall_below_threshold():
    # pipeline._filter_weights
    w = pd.DataFrame({"SEG_ID": [1, 1, 1, 2, 2], "PLT_CN": ["a", "b", "c", "d", "e"],
                      "WEIGHT": [0.03, 0.04, 0.93, 0.04, 0.02]})
    out = leto_donors.filter_weights(w, 0.05, key="SEG_ID")
    assert out[out["SEG_ID"] == 1].set_index("PLT_CN")["WEIGHT"].to_dict() == {"c": 1.0}
    assert out[out["SEG_ID"] == 2].set_index("PLT_CN")["WEIGHT"].to_dict() == {"d": 1.0}


# --- Stage 2: vector riparian split ------------------------------------------------------


def test_footprint_buffers_by_fcode_and_skips_zero_rules():
    flow = pd.DataFrame({"fcode": [46006, 46007],
                         "geometry": [LineString([(0, 0), (100, 0)]), LineString([(0, 50), (100, 50)])]})
    water = pd.DataFrame({"fcode": [39004], "geometry": [box(200, 0, 210, 10)]})
    rules = leto_riparian.FLORIDA_FIRST_PASS_RULES
    fp = leto_riparian.riparian_footprint(rules, flowlines=flow, waterbodies=water)
    assert fp.contains(LineString([(10, 0), (90, 0)]).buffer(22.8))     # 75 ft ~= 22.86 m
    assert not fp.intersects(LineString([(10, 50), (90, 50)]))          # ephemeral: 0 ft
    assert fp.contains(box(200, 0, 210, 10).buffer(22.8))               # waterbody shoreline


def test_split_explodes_parents_into_singlepart_riparian_and_upland_pieces():
    import geopandas as gpd
    stands = gpd.GeoDataFrame({"SEG_ID": [7]}, geometry=[box(0, 0, 300, 300)], crs="EPSG:5070")
    footprint = box(140, -10, 160, 310)                                 # a strip through it
    pieces = leto_riparian.split_stands(stands, footprint)
    assert sorted(pieces["RIPARIAN"].tolist()) == [0, 0, 1]
    assert (pieces["SEG_ID"] == 7).all()
    assert pieces["Acres"].sum() == pytest.approx(stands.to_crs(4326).pipe(
        leto_riparian.geodesic_acres).sum(), rel=1e-9)


def test_production_donor_filters_keep_the_largest_donor():
    # Both production paths must follow pipeline._filter_weights, not drop the whole unit.
    from pipeline.s1_initial_state.leto_initial_state import filter_and_normalize_weights
    from pipeline.s4_fvs.build_fvs_inputs import filter_and_renormalize_weights

    w = pd.DataFrame({"MU_ID": ["1", "1"], "PLT_CN": ["900000000000000001", "900000000000000002"],
                      "WEIGHT": [0.04, 0.03]})
    crosswalk = pd.DataFrame({"MU_ID": ["1"], "Stand_ID": ["1"], "Acres": [1.0],
                              "OWN_CODE": [3], "OWN_TYPE": ["Family"], "SMZ_Pct": [0.0]})
    for out in (filter_and_normalize_weights(w, crosswalk), filter_and_renormalize_weights(w)):
        assert out[["PLT_CN", "WEIGHT"]].values.tolist() == [["900000000000000001", 1.0]]
