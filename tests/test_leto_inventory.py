"""Stage 4 inventory pinned to LETO v1 (aauslander480/Leto, LETO_v1_project @ d2b92ee).

Each test names the `leto/stage4_inventory.py` or `leto/pipeline.py` function it reproduces.
"""

import pandas as pd
import pytest

from pipeline import leto_inventory as inv

LOOKUP = {"068": "RC", "131": "LP", "611": "SU"}
BIG_CN = "1234567890123456789"  # 19 digits: lost if it ever passes through a float64


def _weights(rows):
    frame = pd.DataFrame(rows, columns=["STAND_ID", "SEG_ID", "PLT_CN", "WEIGHT"])
    frame["WEIGHT_ORIGINAL"] = frame["WEIGHT"]
    return frame


def _trees(rows):
    columns = ["CN", "PLT_CN", "CONDID", "STATUSCD", "SPCD", "DIA", "HT", "ACTUALHT", "CR",
               "TPA_UNADJ"]
    return pd.DataFrame(rows, columns=columns).astype("string")


def _one_plot_trees():
    return _trees([
        ("t1", "P1", "1", "1", "131", "8.0", "50", "50", "40", "6.0"),
        ("t2", "P1", "2", "1", "68", "5.0", None, "30", "35", "6.0"),  # second condition
        ("t3", "P1", "1", "2", "131", "9.0", "55", "55", "40", "6.0"),  # dead
    ])


# --- build_tree_list ----------------------------------------------------------------------


def test_tree_count_is_tpa_unadj_times_weight_over_live_trees_in_every_condition():
    weights = _weights([("MU_1", 1, "P1", 0.25)])
    result = inv.build_tree_list(weights, _one_plot_trees(), LOOKUP)
    trees = result.trees
    assert sorted(trees["TREE_ID_FIA"]) == ["t1", "t2"]
    assert trees["TREE_COUNT"].tolist() == [1.5, 1.5]


def test_species_is_zero_padded_fia_code_through_the_crosswalk():
    weights = _weights([("MU_1", 1, "P1", 1.0)])
    trees = inv.build_tree_list(weights, _one_plot_trees(), LOOKUP).trees.set_index("TREE_ID_FIA")
    assert trees.loc["t2", "SPECIES"] == "RC"
    assert trees.loc["t2", "Species_FIA"] == "68"


def test_height_falls_back_to_actualht_and_cr_becomes_crratio():
    weights = _weights([("MU_1", 1, "P1", 1.0)])
    trees = inv.build_tree_list(weights, _one_plot_trees(), LOOKUP).trees.set_index("TREE_ID_FIA")
    assert trees.loc["t2", "HT"] == 30.0
    assert trees.loc["t1", "CRRATIO"] == 40.0
    assert trees.loc["t1", "DIAMETER"] == 8.0


def test_rows_without_species_diameter_or_positive_count_are_dropped():
    fia = _trees([
        ("ok", "P1", "1", "1", "131", "8.0", "50", "50", "40", "6.0"),
        ("nospp", "P1", "1", "1", "999", "8.0", "50", "50", "40", "6.0"),
        ("nodia", "P1", "1", "1", "131", None, "50", "50", "40", "6.0"),
        ("zero", "P1", "1", "1", "131", "8.0", "50", "50", "40", "0"),
        ("notpa", "P1", "1", "1", "131", "8.0", "50", "50", "40", None),
    ])
    trees = inv.build_tree_list(_weights([("MU_1", 1, "P1", 1.0)]), fia, LOOKUP).trees
    assert trees["TREE_ID_FIA"].tolist() == ["ok"]


def test_tree_ids_are_sequential_within_each_stand_after_sorting_by_seg_plot_tree():
    fia = _trees([
        ("b", "P2", "1", "1", "131", "8.0", "50", "50", "40", "6.0"),
        ("a", "P2", "1", "1", "131", "8.0", "50", "50", "40", "6.0"),
        ("c", "P1", "1", "1", "611", "4.0", "20", "20", "30", "6.0"),
    ])
    weights = _weights([("MU_10", 10, "P2", 0.5), ("MU_10", 10, "P1", 0.5),
                        ("MU_9", 9, "P1", 1.0)])
    trees = inv.build_tree_list(weights, fia, LOOKUP).trees
    assert trees[["STAND_ID", "TREE_ID", "TREE_ID_FIA"]].values.tolist() == [
        ["MU_9", 1, "c"], ["MU_10", 1, "c"], ["MU_10", 2, "a"], ["MU_10", 3, "b"]]


def test_output_columns_and_direct_tags_match_leto():
    trees = inv.build_tree_list(_weights([("MU_1", 1, "P1", 1.0)]), _one_plot_trees(),
                                LOOKUP).trees
    assert trees.columns.tolist() == [
        "STAND_ID", "TREE_ID", "SPECIES", "DIAMETER", "HT", "CRRATIO", "TREE_COUNT", "MU_ID",
        "SEG_ID", "PLT_CN", "WEIGHT", "WEIGHT_ORIGINAL", "Species_FIA", "TREE_ID_FIA",
        "TREE_SOURCE", "DONOR_STAND_ID", "NEAR_DIST"]
    assert set(trees["TREE_SOURCE"]) == {inv.TreeSource.FIA_WEIGHTED_DIRECT}
    assert set(trees["DONOR_STAND_ID"]) == {""}
    assert trees["NEAR_DIST"].isna().all()
    assert set(trees["MU_ID"]) == {"1"}


def test_plot_and_tree_control_numbers_keep_every_digit():
    fia = _trees([(BIG_CN, BIG_CN, "1", "1", "131", "8.0", "50", "50", "40", "6.0")])
    trees = inv.build_tree_list(_weights([("MU_1", 1, BIG_CN, 1.0)]), fia, LOOKUP).trees
    assert trees.loc[0, "PLT_CN"] == BIG_CN
    assert trees.loc[0, "TREE_ID_FIA"] == BIG_CN


def test_missing_species_qa_summarises_by_fia_species_and_by_stand():
    fia = _trees([
        ("ok", "P1", "1", "1", "131", "8.0", "50", "50", "40", "3.0"),
        ("x1", "P1", "1", "1", "999", "6.0", "40", "40", "40", "1.0"),
    ])
    result = inv.build_tree_list(_weights([("MU_1", 1, "P1", 1.0)]), fia, LOOKUP)
    by_species = result.missing_species
    assert by_species.columns.tolist() == [
        "Species_FIA", "Tree_Records", "Weighted_TPA", "Mean_Diameter", "Mean_Height",
        "Donor_Plots", "Stands"]
    assert by_species.iloc[0][["Species_FIA", "Tree_Records", "Weighted_TPA"]].tolist() == [
        "999", 1, 1.0]
    by_stand = result.missing_species_by_stand
    assert by_stand.iloc[0]["STAND_ID"] == "MU_1"
    assert by_stand.iloc[0]["Percent_Stand_TPA_Missing"] == pytest.approx(25.0)
    assert result.metrics["untranslated_live_tree_records"] == 1
    assert result.metrics["missing_weighted_tpa_percent"] == pytest.approx(25.0)


def test_missing_species_qa_is_empty_with_headers_when_every_species_maps():
    result = inv.build_tree_list(_weights([("MU_1", 1, "P1", 1.0)]), _one_plot_trees(), LOOKUP)
    assert result.missing_species.empty
    assert "Percent_Stand_TPA_Missing" in result.missing_species_by_stand.columns
    assert result.metrics["direct_stands"] == 1


def test_weights_missing_required_columns_are_rejected():
    with pytest.raises(ValueError, match="WEIGHT_ORIGINAL"):
        inv.build_tree_list(_weights([("MU_1", 1, "P1", 1.0)]).drop(columns="WEIGHT_ORIGINAL"),
                            _one_plot_trees(), LOOKUP)


# --- impute_missing_tree_lists ------------------------------------------------------------


def _stands(geoms, crs="EPSG:5070"):
    import geopandas as gpd
    seg = list(range(1, len(geoms) + 1))
    return gpd.GeoDataFrame({"STAND_ID": [f"MU_{s}" for s in seg], "SEG_ID": seg,
                             "OWN_TYPE": ["Family"] * len(seg)}, geometry=geoms, crs=crs)


def _direct(stand_seg_pairs):
    rows = []
    for stand_id, seg_id, plt in stand_seg_pairs:
        rows.append({"STAND_ID": stand_id, "TREE_ID": 1, "SPECIES": "LP", "DIAMETER": 8.0,
                     "HT": 50.0, "CRRATIO": 40.0, "TREE_COUNT": 6.0, "MU_ID": str(seg_id),
                     "SEG_ID": seg_id, "PLT_CN": plt, "WEIGHT": 1.0, "WEIGHT_ORIGINAL": 1.0,
                     "Species_FIA": "131", "TREE_ID_FIA": f"t{seg_id}",
                     "TREE_SOURCE": inv.TreeSource.FIA_WEIGHTED_DIRECT,
                     "DONOR_STAND_ID": "", "NEAR_DIST": float("nan")})
    return pd.DataFrame(rows)


def test_a_stand_without_trees_copies_the_nearest_runnable_stand_by_edge_distance():
    from shapely.geometry import box
    # stand 3 is missing; stand 2's edge is 10 m away, stand 1's about 51 m
    stands = _stands([box(0, 0, 10, 10), box(65, 0, 75, 10), box(60, 20, 70, 30)])
    stands.loc[1, "OWN_TYPE"] = "Corporate"  # any owner may donate
    direct = pd.concat([_direct([("MU_1", 1, "P1")]), _direct([("MU_2", 2, "P2")]),
                        _direct([("MU_2", 2, "P2")]).assign(TREE_ID=2, TREE_ID_FIA="t2b")])
    trees, still_missing = inv.impute_missing_tree_lists(stands, direct)
    imputed = trees[trees["STAND_ID"] == "MU_3"]
    assert still_missing.empty
    assert imputed["TREE_ID"].tolist() == [1, 2]
    assert set(imputed["TREE_SOURCE"]) == {inv.TreeSource.IMPUTED_NEAREST}
    assert set(imputed["DONOR_STAND_ID"]) == {"MU_2"}
    assert set(imputed["PLT_CN"]) == {"P2"}
    assert set(imputed["SEG_ID"]) == {3} and set(imputed["MU_ID"]) == {"3"}
    assert imputed["NEAR_DIST"].iloc[0] == pytest.approx(10.0)


def test_equidistant_donors_break_on_the_lowest_seg_id():
    from shapely.geometry import box
    # stand 2 touches both 1 and 3 (distance 0)
    stands = _stands([box(0, 0, 10, 10), box(10, 0, 20, 10), box(20, 0, 30, 10)])
    direct = pd.concat([_direct([("MU_3", 3, "P3")]), _direct([("MU_1", 1, "P1")])])
    trees, _ = inv.impute_missing_tree_lists(stands, direct)
    assert set(trees.loc[trees["STAND_ID"] == "MU_2", "DONOR_STAND_ID"]) == {"MU_1"}


def test_imputation_requires_a_projected_crs_and_a_runnable_stand():
    from shapely.geometry import box
    geographic = _stands([box(0, 0, 1, 1), box(2, 0, 3, 1)], crs="EPSG:4326")
    with pytest.raises(ValueError, match="projected"):
        inv.impute_missing_tree_lists(geographic, _direct([("MU_1", 1, "P1")]))
    with pytest.raises(ValueError, match="runnable"):
        inv.impute_missing_tree_lists(_stands([box(0, 0, 1, 1)]), _direct([])
                                      .reindex(columns=inv.TREE_COLUMNS))


def test_nothing_is_imputed_when_every_stand_has_trees():
    from shapely.geometry import box
    direct = _direct([("MU_1", 1, "P1")])
    trees, still_missing = inv.impute_missing_tree_lists(_stands([box(0, 0, 1, 1)]), direct)
    assert len(trees) == 1 and still_missing.empty


# --- prepare_stands / build_final_stands / validate_final_inventory -----------------------


def _units():
    return pd.DataFrame({"MU_ID": [1, 2, 3], "ParentSEG_ID": [7, 7, 8],
                         "Acres": [4.0, 1.0, 2.0], "STDAGE_MEAN": [2.5, 3.5, 10.49],
                         "OWN_TYPE": ["Family", "Family", "Corporate"]})


def test_stand_ids_follow_the_management_unit():
    # stage3.read_stand_polygons / pipeline._prepare_stands_dataframe
    stands = inv.prepare_stands(_units())
    assert stands[["STAND_ID", "Stand_ID", "MU_ID"]].values.tolist()[0] == ["MU_1", "1", "1"]
    assert stands["SEG_ID"].tolist() == [1, 2, 3]
    assert (stands["INV_YEAR"].unique().tolist(), stands["VARIANT"].unique().tolist(),
            stands["STATE"].unique().tolist()) == ([2022], ["SN"], ["FL"])


def test_age_is_stdage_mean_rounded_half_to_even_as_leto_actually_runs():
    # read_stand_polygons sets AGE with Series.round(), so _prepare_stands_dataframe's
    # floor(x + 0.5) branch never runs: 2.5 -> 2, 3.5 -> 4
    assert inv.prepare_stands(_units())["AGE"].tolist() == [2, 4, 10]


def test_final_stands_carry_the_largest_retained_donor_and_only_runnable_stands():
    stands = inv.prepare_stands(_units())
    weights = _weights([("MU_1", 1, "P1", 0.4), ("MU_1", 1, "P2", 0.6),
                        ("MU_2", 2, "P1", 1.0), ("MU_3", 3, "P3", 1.0)])
    trees = pd.DataFrame({"STAND_ID": ["MU_1", "MU_3"]})
    final = inv.build_final_stands(stands, weights, trees)
    assert final["STAND_ID"].tolist() == ["MU_1", "MU_3"]
    assert final["PLT_CN"].tolist() == ["P2", "P3"]
    assert final.columns[:4].tolist() == ["STAND_ID", "VARIANT", "INV_YEAR", "STATE"]


def test_final_inventory_validation_rejects_tree_less_stands_and_orphan_trees():
    stands = inv.prepare_stands(_units())
    trees = pd.DataFrame({"STAND_ID": ["MU_1", "MU_2", "MU_3"]})
    inv.validate_final_inventory(stands, stands, trees)
    with pytest.raises(RuntimeError, match="without tree records"):
        inv.validate_final_inventory(stands, stands, trees.iloc[:2])
    with pytest.raises(RuntimeError, match="unknown final stands"):
        inv.validate_final_inventory(stands, stands.iloc[:2], trees)
    with pytest.raises(RuntimeError, match="duplicate STAND_ID"):
        inv.validate_final_inventory(pd.concat([stands, stands.iloc[:1]]), stands, trees)


# --- build_inventory: pipeline.run_pipeline "Stage 4" end to end --------------------------


def _inventory_inputs():
    import geopandas as gpd
    from shapely.geometry import box
    units = gpd.GeoDataFrame({
        "MU_ID": [1, 2, 3], "Acres": [4.0, 1.0, 2.0], "STDAGE_MEAN": [20.0, 30.0, 40.0],
        "DB_GROUP": ["Family", "Riparian", "Corporate"]},
        geometry=[box(0, 0, 10, 10), box(10, 0, 20, 10), box(100, 0, 110, 10)],
        crs="EPSG:5070")
    weights = pd.DataFrame({"MU_ID": [1, 1, 2, 3], "PLT_CN": ["P1", "P9", "P2", "P3"],
                            "WEIGHT": [0.97, 0.03, 1.0, 1.0]})
    fia = _trees([
        ("a", "P1", "1", "1", "131", "8.0", "50", "50", "40", "6.0"),
        ("b", "P9", "1", "1", "131", "3.0", "20", "20", "40", "6.0"),
        ("c", "P2", "1", "2", "131", "8.0", "50", "50", "40", "6.0"),  # dead only
        ("d", "P3", "1", "1", "611", "9.0", "60", "60", "50", "6.0"),
    ])
    return units, weights, fia


def test_inventory_filters_donors_imputes_gaps_and_validates_coverage():
    result = inv.build_inventory(*_inventory_inputs(), LOOKUP)
    trees = result.trees.set_index("STAND_ID")
    assert "b" not in set(result.trees["TREE_ID_FIA"])  # the 3% donor is filtered out
    assert trees.loc["MU_2", "TREE_SOURCE"] == inv.TreeSource.IMPUTED_NEAREST
    assert trees.loc["MU_2", "DONOR_STAND_ID"] == "MU_1"
    assert result.final_stands["STAND_ID"].tolist() == ["MU_1", "MU_2", "MU_3"]
    assert result.final_stands["DB_GROUP"].tolist() == ["Family", "Riparian", "Corporate"]
    assert result.final_stands["PLT_CN"].tolist() == ["P1", "P2", "P3"]
    assert result.still_missing.empty
    assert result.imputation_diagnostics["FAILURE_REASON"].tolist() == ["NO_LIVE_TREES"]
    overall = result.imputation_summary.iloc[0]
    assert (overall["IMPUTED_MUS"], overall["DISTANCE_UNITS"]) == (1, "Meters")


def test_inventory_writes_leto_file_names(tmp_path):
    result = inv.build_inventory(*_inventory_inputs(), LOOKUP)
    paths = inv.write_inventory(result, tmp_path)
    assert sorted(p.name for p in paths.values()) == sorted([
        "Segment_PLT_CN_Weights_Filtered.csv", "Segment_Stand_List.csv", "FVS_StandInit.csv",
        "FVS_TreeInit.csv", "Segment_FVS_Stands_No_Live_Trees.csv", "QA_Missing_FVS_Species.csv",
        "QA_Missing_FVS_Species_By_Stand.csv", "QA_Imputation_Diagnostics.csv",
        "QA_Imputation_Summary.csv"])
    trees = pd.read_csv(tmp_path / "FVS_TreeInit.csv", dtype={"PLT_CN": "string"})
    assert len(trees) == len(result.trees)
