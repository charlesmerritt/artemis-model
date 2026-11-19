"""LETO's proximity merge and final unit identity on polygons (#169).

Hand-built on a 30 m grid in the project CRS: a parent stand is a rectangle of whole cells
(0.222 ac each), so its acres and the gaps between parents are set by choosing cells.
"""

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
from shapely.geometry import MultiPolygon, box

from pipeline.leto_proximity import (
    ProximityMatch,
    ProximitySettings,
    Unmatched,
    chain_qa,
    dissolve_units,
    final_identity,
    plan_groups,
    representative,
    unit_weights,
    unit_traits,
)
from pipeline.leto_riparian import RIPARIAN, UPLAND
from pipeline.spatial_ref import project_crs

CELL = 30.0
M2_PER_ACRE = 4046.8564224
FIPS = "12001"
SETTINGS = ProximitySettings()


def _rect(col, row, width, height):
    return box(col * CELL, row * CELL, (col + width) * CELL, (row + height) * CELL)


def _parents(*rows):
    """``(seg, geometry, own_code)`` rows as parent stands with planar acres."""
    frame = gpd.GeoDataFrame([{"SEG_ID": s, "OWN_CODE": o, "geometry": g} for s, g, o in rows],
                             crs=project_crs())
    frame["Acres"] = frame.area / M2_PER_ACRE
    return frame


def _traits(**by_seg):
    """``seg=(fortypcd, stdage)`` as the parent's dominant type and mean age."""
    return pd.DataFrame({int(k[1:]): {"FORTYPCD_DOM": ft, "STDAGE_MEAN": age}
                         for k, (ft, age) in by_seg.items()}).T.rename_axis("SEG_ID")


# ── planning: upstream's rules on polygons ─────────────────────────────────────────────


def test_a_small_stand_joins_the_closest_in_age_within_60_m_and_keeps_its_geometry():
    parents = _parents((1, _rect(0, 0, 2, 2), 3),            # 0.9 ac: small
                       (2, _rect(3, 0, 25, 2), 3),           # 30 m away, 10 years older
                       (3, _rect(0, 3, 30, 10), 3))          # 30 m away, 1 year older, large
    plan = plan_groups(parents, _traits(s1=(161, 20.0), s2=(161, 30.0), s3=(161, 21.0)),
                       minimum_acres=5.0, settings=SETTINGS)

    assert plan.groups.to_dict() == {1: 1, 2: 2, 3: 1}       # one proximity group: 1 and 3
    merge = plan.merges.iloc[0]
    assert (merge["SOURCE_SEG_ID"], merge["TARGET_SEG_ID"]) == (1, 3)
    assert merge["DISTANCE_METERS"] == pytest.approx(CELL)
    assert plan.qa["proximity_merges"] == 1


def test_a_candidate_beyond_the_distance_another_owner_or_forest_type_is_never_taken():
    parents = _parents((1, _rect(0, 0, 2, 2), 3),
                       (2, _rect(5, 0, 30, 2), 3),           # 90 m: too far
                       (3, _rect(0, 3, 30, 10), 4),          # another owner
                       (4, _rect(0, -11, 30, 10), 3))        # another forest type
    plan = plan_groups(parents, _traits(s1=(161, 20.0), s2=(161, 20.0), s3=(161, 20.0),
                                        s4=(503, 20.0)), minimum_acres=5.0, settings=SETTINGS)

    assert plan.merges.empty
    assert plan.qa["small_stands_remaining"] == 1


def test_matching_on_owner_alone_takes_another_forest_type():
    parents = _parents((1, _rect(0, 0, 2, 2), 3), (4, _rect(0, -11, 30, 10), 3))
    owner_only = ProximitySettings(match=(ProximityMatch.OWNER,))
    plan = plan_groups(parents, _traits(s1=(161, 20.0), s4=(503, 20.0)), minimum_acres=5.0,
                       settings=owner_only)

    assert plan.groups[1] == plan.groups[4]


def test_a_small_stand_with_no_candidate_fails_closed_when_retaining_is_off():
    parents = _parents((1, _rect(0, 0, 2, 2), 3))
    with pytest.raises(RuntimeError, match="No eligible proximity candidate"):
        plan_groups(parents, _traits(s1=(161, 20.0)), minimum_acres=5.0,
                    settings=ProximitySettings(unmatched=Unmatched.ERROR))


def test_the_combined_cap_stops_a_group_growing_past_it():
    parents = _parents((1, _rect(0, 0, 2, 2), 3), (2, _rect(0, 3, 30, 10), 3))   # 0.9 + 66.7 ac
    plan = plan_groups(parents, _traits(s1=(161, 20.0), s2=(161, 20.0)), minimum_acres=5.0,
                       settings=ProximitySettings(maximum_combined_acres=50.0))

    assert plan.merges.empty


# ── final identity ─────────────────────────────────────────────────────────────────────


def _pieces(*rows):
    """``(seg, geometry, riparian, owner_class, own_code)`` single-part pieces."""
    frame = gpd.GeoDataFrame(
        [{"SEG_ID": s, "RIPARIAN": r, "owner_class": o, "OWN_CODE": c,
          "unit_class": "riparian" if r == RIPARIAN else "managed", "geometry": g}
         for s, g, r, o, c in rows], crs=project_crs())
    frame["Acres"] = frame.area / M2_PER_ACRE
    return frame


def test_without_proximity_every_piece_is_its_own_unit_numbered_by_parent_then_class():
    pieces = _pieces((2, _rect(0, 0, 4, 4), UPLAND, "family", 3),
                     (1, _rect(5, 0, 4, 4), RIPARIAN, "family", 3),
                     (1, _rect(10, 0, 4, 4), UPLAND, "family", 3))
    segments = final_identity(pieces, FIPS, groups=None)

    assert list(segments["unit_id"]) == ["12001-1", "12001-2", "12001-3"]
    assert list(zip(segments["SEG_ID"], segments["RIPARIAN"])) == [(1, UPLAND), (1, RIPARIAN),
                                                                     (2, UPLAND)]
    assert (segments["MU_PART"] == 1).all()
    assert list(segments["BIO_MU_ID"]) == [1, 2, 3]          # upstream: its own MU_ID


def test_under_proximity_a_parents_upland_slivers_and_its_group_are_one_unit():
    pieces = _pieces((1, _rect(0, 0, 4, 4), UPLAND, "family", 3),
                     (1, _rect(6, 0, 4, 4), UPLAND, "family", 3),     # the stream cut it in two
                     (1, _rect(4, 0, 2, 4), RIPARIAN, "family", 3),
                     (5, _rect(0, 6, 4, 4), UPLAND, "family", 3))     # grouped with parent 1
    segments = final_identity(pieces, FIPS, groups=pd.Series({1: 1, 5: 1}))

    upland = segments[segments["RIPARIAN"] == UPLAND]
    assert upland["unit_id"].nunique() == 1
    assert sorted(upland["MU_PART"]) == [1, 2, 3]
    riparian = segments[segments["RIPARIAN"] == RIPARIAN]
    assert riparian["unit_id"].iloc[0] not in set(upland["unit_id"])
    assert (segments["BIO_MU_ID"] == 1).all()


def test_under_proximity_a_group_never_mixes_owner_classes():
    pieces = _pieces((1, _rect(0, 0, 4, 4), UPLAND, "family", 3),
                     (2, _rect(6, 0, 4, 4), UPLAND, "corporate", 4))
    segments = final_identity(pieces, FIPS, groups=pd.Series({1: 1, 2: 1}))

    assert segments["unit_id"].nunique() == 2


# ── units, traits, chain QA ────────────────────────────────────────────────────────────


def test_a_unit_is_one_multipart_feature_with_its_pieces_acres():
    pieces = _pieces((1, _rect(0, 0, 4, 4), UPLAND, "family", 3),
                     (5, _rect(0, 6, 2, 2), UPLAND, "family", 3))
    segments = final_identity(pieces, FIPS, groups=pd.Series({1: 1, 5: 1}))
    units = dissolve_units(segments)

    assert len(units) == 1
    unit = units.iloc[0]
    assert isinstance(unit.geometry, MultiPolygon)
    assert unit["Acres"] == pytest.approx(pieces["Acres"].sum())
    assert (unit["SEG_ID"], unit["SEG_IDS"], unit["PART_COUNT"]) == (1, "1,5", 2)


def test_a_units_traits_are_its_parents_weighted_by_piece_acres():
    pieces = _pieces((1, _rect(0, 0, 3, 1), UPLAND, "family", 3),     # 3 cells
                     (2, _rect(0, 2, 1, 1), UPLAND, "family", 3))     # 1 cell
    segments = final_identity(pieces, FIPS, groups=pd.Series({1: 1, 2: 1}))
    parent = pd.DataFrame({"STDAGE": [1.0, 5.0], "FORTYPCD": [161, 503]},
                          index=pd.Index([1, 2], name="SEG_ID"))
    traits = unit_traits(segments, parent)

    row = traits.loc["12001-1"]
    assert row["STDAGE"] == pytest.approx(2.0)
    assert row["FORTYPCD"] == 161


def test_chain_qa_reports_each_units_span_and_flags_a_chain_past_the_distance():
    pieces = _pieces((1, _rect(0, 0, 2, 2), UPLAND, "family", 3),
                     (2, _rect(4, 0, 2, 2), UPLAND, "family", 3),     # 60 m from 1
                     (3, _rect(8, 0, 2, 2), UPLAND, "family", 3))     # 180 m from 1
    segments = final_identity(pieces, FIPS, groups=pd.Series({1: 1, 2: 1, 3: 1}))
    table, summary = chain_qa(segments, SETTINGS)

    row = table.set_index("unit_id").loc["12001-1"]
    assert row["MU_SPAN_M"] == pytest.approx(180.0)
    assert bool(row["CHAIN_FLAG"]) and row["PART_COUNT"] == 3
    assert summary["chain_flagged_units"] == 1 and summary["multipart_management_units"] == 1


def test_chain_qa_of_single_part_units_has_no_span():
    pieces = _pieces((1, _rect(0, 0, 2, 2), UPLAND, "family", 3))
    table, summary = chain_qa(final_identity(pieces, FIPS, groups=None), SETTINGS)

    assert table["MU_SPAN_M"].tolist() == [0.0]
    assert summary["multipart_management_units"] == 0
    assert np.isfinite(summary["maximum_observed_span_meters"])


# ── donor weights per unit ─────────────────────────────────────────────────────────────


def _weights(*rows):
    """``(seg, treemap_value, plt_cn, weight)`` parent donor weights."""
    return pd.DataFrame(rows, columns=["SEG_ID", "TREEMAP_VALUE", "PLT_CN", "WEIGHT"])


def _two_parent_unit(acres_1, acres_2):
    pieces = _pieces((1, _rect(0, 0, 1, 1), UPLAND, "family", 3),
                     (2, _rect(0, 2, 1, 1), UPLAND, "family", 3))
    pieces["Acres"] = [acres_1, acres_2]
    return final_identity(pieces, FIPS, groups=pd.Series({1: 1, 2: 1}))


def test_a_units_representative_plot_is_its_largest_donor_combined_by_acres():
    segments = _two_parent_unit(3.0, 7.0)
    parent = _weights((1, 11, "A", 0.6), (1, 22, "B", 0.4), (2, 22, "B", 1.0))
    weights = unit_weights(parent, segments)

    combined = weights.set_index("PLT_CN")["WEIGHT"]
    assert combined["A"] == pytest.approx(0.18) and combined["B"] == pytest.approx(0.82)
    top = representative(weights)
    assert top.loc["12001-1", "PLT_CN"] == "B"          # parent 1's own would be A
    assert top.loc["12001-1", "TREEMAP_VALUE"] == 22


def test_a_tie_goes_to_the_lower_treemap_value():
    segments = final_identity(_pieces((1, _rect(0, 0, 1, 1), UPLAND, "family", 3)), FIPS,
                              groups=None)
    top = representative(unit_weights(_weights((1, 22, "B", 0.5), (1, 11, "A", 0.5)), segments))
    assert top.loc["12001-1", "TREEMAP_VALUE"] == 11


def test_a_unit_of_one_parent_stand_keeps_its_weights_exactly():
    pieces = _pieces((1, _rect(0, 0, 1, 1), UPLAND, "family", 3),
                     (1, _rect(0, 2, 1, 1), UPLAND, "family", 3))
    pieces["Acres"] = [0.37, 2.9]
    segments = final_identity(pieces, FIPS, groups=pd.Series({1: 1}))
    parent = _weights((1, 11, "A", 0.1), (1, 22, "B", 0.7), (1, 33, "C", 0.2))

    weights = unit_weights(parent, segments).set_index("PLT_CN")["WEIGHT"]
    assert weights.to_dict() == {"B": 0.7, "C": 0.2, "A": 0.1}
