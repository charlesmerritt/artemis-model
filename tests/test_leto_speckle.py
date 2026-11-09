"""Speckle merge after LETO's riparian and county splits (ADR 0005, #167).

Every layer here is hand-built on a 30 m grid: a unit is a rectangle of whole cells, so a
cell is 0.222 ac and a speckle (under 1 ac) is four cells or fewer. Each unit's parent stand
has its own standardized traits, so a test sets the ``force_merge`` cost by choosing them.
"""

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import box

from pipeline.leto_speckle import (
    MergeCost,
    SpeckleAction,
    SpeckleSettings,
    merge_speckle,
)
from pipeline.spatial_ref import project_crs

CELL = 30.0
M2_PER_ACRE = 4046.8564224
COST = MergeCost(weights={"FORTYPCD": 0.30, "STDAGE": 0.25, "BALIVE": 0.20, "QMD": 0.15,
                          "TPA": 0.10},
                 shared_edge_bonus=0.2, edge_length=CELL)
MERGE = SpeckleSettings(SpeckleAction.MERGE, minimum_unit_acres=1.0)
COUNTY = "12001"


def _unit(uid, col, row, width, height, *, seg, owner="corporate", unit_class="managed",
          county=COUNTY):
    geometry = box(col * CELL, row * CELL, (col + width) * CELL, (row + height) * CELL)
    return {"unit_id": f"{county}-{uid}", "county_fips": county, "SEG_ID": seg,
            "owner_class": owner, "unit_class": unit_class, "stand_age": float(seg),
            "Acres": geometry.area / M2_PER_ACRE, "geometry": geometry}


def _layer(*units):
    return gpd.GeoDataFrame(list(units), crs=project_crs())


def _traits(**by_seg):
    """Standardized parent-stand traits; ``seg=(stdage, fortypcd)``, the rest held equal."""
    rows = {int(k[1:]): {"STDAGE": age, "BALIVE": 0.0, "QMD": 0.0, "TPA": 0.0, "FORTYPCD": ft}
            for k, (age, ft) in by_seg.items()}
    return pd.DataFrame.from_dict(rows, orient="index")


def _merge(units, traits, settings=MERGE):
    return merge_speckle(units, traits, settings, COST)


def _qa(result):
    return result.qa.set_index("county_fips").loc[COUNTY]


def _unit_row(result, uid):
    return result.units.set_index("unit_id").loc[f"{COUNTY}-{uid}"]


def test_keep_returns_its_input_and_reports_every_speckle_kept():
    units = _layer(_unit(1, 0, 0, 10, 10, seg=1), _unit(2, 10, 0, 2, 2, seg=2))
    result = _merge(units, _traits(s1=(0.0, 161), s2=(0.0, 161)),
                    SpeckleSettings(SpeckleAction.KEEP, minimum_unit_acres=1.0))
    pd.testing.assert_frame_equal(result.units, units)
    qa = _qa(result)
    assert qa["speckle_kept"] == 1
    assert qa["merged_same_owner"] == 0


def test_a_speckle_joins_its_own_owner_and_class_before_a_closer_match_under_another_owner():
    units = _layer(
        _unit(1, 0, 0, 10, 10, seg=1, owner="corporate"),        # same owner, unlike traits
        _unit(2, 12, 0, 10, 10, seg=2, owner="family"),          # another owner, identical traits
        _unit(3, 10, 0, 2, 2, seg=3, owner="corporate"),         # the speckle between them
    )
    result = _merge(units, _traits(s1=(3.0, 503), s2=(0.0, 161), s3=(0.0, 161)))
    assert list(result.units["unit_id"]) == [f"{COUNTY}-1", f"{COUNTY}-2"]
    receiver = _unit_row(result, 1)
    assert receiver["SEG_ID"] == 1 and receiver["stand_age"] == 1.0   # it takes the receiver's
    assert result.units["Acres"].sum() == pytest.approx(units["Acres"].sum(), abs=1e-9)
    qa = _qa(result)
    assert qa["merged_same_owner"] == 1 and qa["merged_other_owner"] == 0


def test_with_no_same_owner_neighbour_a_speckle_takes_another_owners_class_and_drift_is_reported():
    units = _layer(
        _unit(1, 0, 0, 10, 10, seg=1, owner="family"),
        _unit(2, 12, 0, 10, 10, seg=2, owner="corporate", unit_class="riparian"),
        _unit(3, 10, 0, 2, 2, seg=3, owner="corporate"),
    )
    result = _merge(units, _traits(s1=(0.0, 161), s2=(0.0, 161), s3=(0.0, 161)))
    assert _unit_row(result, 1)["owner_class"] == "family"
    assert f"{COUNTY}-3" not in set(result.units["unit_id"])
    speckle = units["Acres"].iloc[2]
    qa = _qa(result)
    assert qa["merged_other_owner"] == 1 and qa["merged_riparian"] == 0
    assert qa["owner_drift_acres_family"] == pytest.approx(speckle, abs=1e-4)
    assert qa["owner_drift_acres_corporate"] == pytest.approx(-speckle, abs=1e-4)


def test_a_managed_speckle_with_only_a_riparian_neighbour_becomes_grow_only():
    units = _layer(_unit(1, 0, 0, 10, 10, seg=1, unit_class="riparian"),
                   _unit(2, 10, 0, 2, 2, seg=2))
    result = _merge(units, _traits(s1=(0.0, 161), s2=(0.0, 161)))
    assert list(result.units["unit_class"]) == ["riparian"]
    assert _qa(result)["grow_only_acres"] == pytest.approx(units["Acres"].iloc[1], abs=1e-4)


def test_riparian_speckle_never_joins_a_managed_unit():
    units = _layer(_unit(1, 0, 0, 10, 10, seg=1),
                   _unit(2, 10, 0, 2, 2, seg=2, unit_class="riparian"))
    result = _merge(units, _traits(s1=(0.0, 161), s2=(0.0, 161)))
    assert list(result.units["unit_class"]) == ["managed", "riparian"]
    assert _qa(result)["speckle_kept"] == 1


def test_a_young_speckle_joins_young_land_not_the_mature_neighbour_it_shares_more_edge_with():
    units = _layer(
        _unit(1, 0, 0, 10, 10, seg=1),                           # mature: four shared edges
        _unit(2, 11, 0, 20, 2, seg=2),                           # young: two shared edges
        _unit(3, 10, 0, 1, 4, seg=3),                            # a young speckle, 0.89 ac
    )
    result = _merge(units, _traits(s1=(2.5, 161), s2=(-1.5, 161), s3=(-1.5, 161)))
    assert _unit_row(result, 2)["Acres"] == pytest.approx(units["Acres"].iloc[1:].sum())
    assert _unit_row(result, 1)["Acres"] == pytest.approx(units["Acres"].iloc[0])


def test_a_tie_goes_to_the_lower_unit_id_by_number():
    units = _layer(_unit(10, 0, 0, 10, 10, seg=1), _unit(9, 12, 0, 10, 10, seg=2),
                   _unit(3, 10, 0, 2, 2, seg=3))
    result = _merge(units, _traits(s1=(0.0, 161), s2=(0.0, 161), s3=(0.0, 161)))
    assert _unit_row(result, 9)["Acres"] > _unit_row(result, 10)["Acres"]


def test_a_speckle_never_merges_across_a_county_line():
    units = _layer(_unit(1, 0, 0, 10, 10, seg=1, county="12003"),
                   _unit(2, 10, 0, 2, 2, seg=2))
    result = _merge(units, _traits(s1=(0.0, 161), s2=(0.0, 161)))
    assert len(result.units) == 2
    assert _qa(result)["speckle_kept"] == 1


def test_masked_units_neither_merge_nor_receive():
    units = _layer(_unit(1, 0, 0, 10, 10, seg=1, owner="masked"),
                   _unit(2, 10, 0, 2, 2, seg=2), _unit(3, 20, 0, 2, 2, seg=3, owner="masked"),
                   _unit(4, 22, 0, 10, 10, seg=4))
    result = _merge(units, _traits(s1=(0.0, 161), s2=(0.0, 161), s3=(0.0, 161), s4=(0.0, 161)))
    assert len(result.units) == 4
    assert _qa(result)["speckle_kept"] == 1                       # masked units are not speckle


def test_it_repeats_until_stable_and_conserves_acres():
    units = _layer(_unit(1, 0, 0, 10, 10, seg=1), _unit(2, 10, 0, 1, 2, seg=2),
                   _unit(3, 11, 0, 1, 2, seg=3), _unit(4, 12, 0, 1, 1, seg=4))
    result = _merge(units, _traits(s1=(0.0, 161), s2=(0.0, 161), s3=(0.0, 161), s4=(0.0, 161)))
    assert list(result.units["unit_id"]) == [f"{COUNTY}-1"]
    assert result.units["Acres"].sum() == pytest.approx(units["Acres"].sum(), abs=1e-9)
    assert result.units.geometry.area.sum() == pytest.approx(units.geometry.area.sum())
    assert _qa(result)["units_after"] == 1


def test_every_county_reports_drift_for_every_owner_class():
    units = _layer(_unit(1, 0, 0, 10, 10, seg=1, owner="family"), _unit(2, 10, 0, 2, 2, seg=2),
                   _unit(3, 30, 0, 10, 10, seg=3, county="12003"))
    result = _merge(units, _traits(s1=(0.0, 161), s2=(0.0, 161), s3=(0.0, 161)))
    other = result.qa.set_index("county_fips").loc["12003"]
    assert other["owner_drift_acres_family"] == 0.0 and other["owner_drift_acres_corporate"] == 0.0


def test_the_qa_columns_are_the_same_whatever_owners_a_county_has():
    """The stage joins counties' QA tables as text, so their headers must agree."""
    family = _merge(_layer(_unit(1, 0, 0, 10, 10, seg=1, owner="family")), _traits(s1=(0.0, 161)))
    state = _merge(_layer(_unit(1, 0, 0, 10, 10, seg=1, owner="state")), _traits(s1=(0.0, 161)))
    assert list(family.qa.columns) == list(state.qa.columns)
    assert "owner_drift_acres_tribal" in family.qa.columns


# ── merge, then delete what is left (the default) ─────────────────────────────────────


MERGE_DELETE = SpeckleSettings(SpeckleAction.MERGE_DELETE, minimum_unit_acres=1.0)


def test_the_default_merges_speckle_then_deletes_what_is_left():
    assert SpeckleSettings().action is SpeckleAction.MERGE_DELETE


def test_speckle_that_cannot_merge_is_deleted_and_reported():
    units = _layer(
        _unit(1, 0, 0, 10, 10, seg=1),
        _unit(2, 10, 0, 2, 2, seg=2),                                     # merges into 1
        _unit(3, 40, 0, 2, 2, seg=3),                                     # isolated
        _unit(4, 10, 2, 2, 2, seg=4, unit_class="riparian"),              # riparian, managed nbrs
        _unit(5, 50, 0, 10, 10, seg=5, county="12003"), _unit(6, 60, 0, 2, 2, seg=6),  # county line
        _unit(7, 80, 0, 2, 2, seg=7, owner="masked"),                     # masked: never speckle
    )
    traits = _traits(**{f"s{i}": (0.0, 161) for i in range(1, 8)})
    result = _merge(units, traits, MERGE_DELETE)

    assert sorted(result.units["unit_id"]) == [f"{COUNTY}-1", f"{COUNTY}-7", "12003-5"]
    assert sorted(result.deleted["unit_id"]) == [f"{COUNTY}-3", f"{COUNTY}-4", f"{COUNTY}-6"]
    cell = CELL * CELL / M2_PER_ACRE
    qa = _qa(result)
    assert qa["merged_same_owner"] == 1
    assert qa["speckle_kept"] == 0
    assert qa["speckle_deleted"] == 3
    assert qa["speckle_deleted_acres"] == pytest.approx(12 * cell, abs=1e-4)
    assert qa["speckle_deleted_riparian_acres"] == pytest.approx(4 * cell, abs=1e-4)
    assert (result.units["Acres"].sum() + result.deleted["Acres"].sum()
            == pytest.approx(units["Acres"].sum(), abs=1e-9))


def test_deleting_speckle_is_not_counted_as_owner_drift():
    units = _layer(_unit(1, 0, 0, 10, 10, seg=1), _unit(2, 40, 0, 2, 2, seg=2, owner="family"))
    result = _merge(units, _traits(s1=(0.0, 161), s2=(0.0, 161)), MERGE_DELETE)
    assert _qa(result)["owner_drift_acres_family"] == 0.0


def test_merge_alone_keeps_what_it_cannot_merge():
    units = _layer(_unit(1, 0, 0, 10, 10, seg=1), _unit(2, 40, 0, 2, 2, seg=2))
    result = _merge(units, _traits(s1=(0.0, 161), s2=(0.0, 161)))
    assert len(result.units) == 2 and result.deleted.empty
    assert _qa(result)["speckle_kept"] == 1 and _qa(result)["speckle_deleted"] == 0
