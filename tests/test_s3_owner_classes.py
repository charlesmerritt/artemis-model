"""Tests for owner-class assignment (pipeline/s3_management/owner_classes.py)."""

from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline.s3_management.owner_classes import (
    MASKED,
    OwnerAssignment,
    classify_owner,
    classify_owners,
    doruc_signal,
    harris_value_to_class,
    refine_private_class,
    tpo_group_for,
)


# ---- raster → base class ---------------------------------------------------------------

@pytest.mark.parametrize("value,expected", [
    (0, "unknown"),
    (3, "private_family"),
    (4, "private_industrial"),   # corporate defaults to industrial, demotable by parcel
    (5, "tribal"),
    (6, "federal"),
    (7, "state"),
    (8, "local"),
])
def test_harris_values_map_to_owner_classes(value, expected):
    assert harris_value_to_class(value) == expected


def test_missing_ownership_is_unknown_not_an_error():
    assert harris_value_to_class(None) == "unknown"
    assert classify_owner({}).owner_class == "unknown"


def test_unknown_ownership_is_flagged_so_its_area_stays_reportable():
    assignment = classify_owner({"OWN_CODE": 0})
    assert assignment.owner_class == "unknown"
    assert assignment.has("unknown_ownership")


def test_masked_values_are_not_forest_and_carry_no_tpo_group():
    assignment = classify_owner({"OWN_CODE": 2})   # water
    assert assignment.owner_class == MASKED
    assert assignment.tpo_group is None


# ---- DOR use codes ---------------------------------------------------------------------

def test_doruc_timberland_and_government_signals():
    assert doruc_signal(54) == "timberland"      # timberland, site index 90+
    assert doruc_signal(59) == "timberland"      # timberland, unclassified
    assert doruc_signal(88) == "government"      # federal
    assert doruc_signal(82) == "government"      # forest/parks — any level of government
    assert doruc_signal(None) is None


def test_doruc_82_does_not_identify_the_level_of_government():
    """The reason the raster, not the parcel, assigns federal/state/local.

    DOR_UC 82 covers federal, state, county, and municipal forest and park land in one
    value, so it can confirm 'public' and nothing more.
    """
    assert doruc_signal(82) == "government"
    for harris_value in (6, 7, 8):
        assignment = classify_owner({"OWN_CODE": harris_value, "DORUC": 82})
        assert assignment.owner_class == harris_value_to_class(harris_value)
        assert not assignment.has("owner_conflict")


# ---- private refinement ----------------------------------------------------------------

def test_timberland_doruc_keeps_a_corporate_unit_industrial():
    cls, flags = refine_private_class("private_industrial", doruc=55, acres=40)
    assert cls == "private_industrial"
    assert flags == ()


def test_large_corporate_acreage_alone_keeps_it_industrial():
    cls, _ = refine_private_class("private_industrial", doruc=None, acres=5000)
    assert cls == "private_industrial"


def test_small_non_industrial_corporate_parcel_is_demoted():
    cls, flags = refine_private_class("private_industrial", doruc=97, acres=12)  # outdoor rec
    assert cls == "private_corporate_other"
    assert "demoted_to_other_corporate" in flags


def test_corporate_with_no_parcel_evidence_keeps_the_class_but_is_flagged():
    """Segmentation-derived units inherit no parcel attributes; that area must stay visible."""
    cls, flags = refine_private_class("private_industrial")
    assert cls == "private_industrial"
    assert "unrefined" in flags


def test_refinement_never_touches_a_non_corporate_class():
    for base in ("private_family", "federal", "state", "local", "tribal", "unknown"):
        cls, flags = refine_private_class(base, doruc=54, acres=9000)
        assert (cls, flags) == (base, ())


# ---- conflicts -------------------------------------------------------------------------

def test_government_doruc_on_private_raster_flags_a_conflict_without_overriding():
    assignment = classify_owner({"OWN_CODE": 3, "DORUC": 88, "ACRES": 200})
    assert assignment.owner_class == "private_family"   # the raster still wins
    assert assignment.has("owner_conflict")


# ---- TPO groups ------------------------------------------------------------------------

def test_tpo_groups_route_owner_classes_onto_the_three_harvest_budgets():
    assert tpo_group_for("federal") == "Federal (NF)"
    assert tpo_group_for("state") == "Other public"
    assert tpo_group_for("local") == "Other public"
    assert tpo_group_for("private_industrial") == "Private"
    assert tpo_group_for("private_family") == "Private"
    assert tpo_group_for("tribal") == "Private"     # FIA puts tribal in owner group 40


# ---- frame API -------------------------------------------------------------------------

def test_classify_owners_adds_the_columns_the_scheduler_budgets_on():
    pd = pytest.importorskip("pandas")
    units = pd.DataFrame([
        {"unit_id": "a", "OWN_CODE": 4, "DORUC": 54, "ACRES": 3000},
        {"unit_id": "b", "OWN_CODE": 6, "DORUC": 82, "ACRES": 900},
        {"unit_id": "c", "OWN_CODE": 4, "DORUC": 97, "ACRES": 5},
    ])
    out = classify_owners(units)
    assert list(out["owner_class"]) == ["private_industrial", "federal", "private_corporate_other"]
    assert list(out["owner_group"]) == ["Private", "Federal (NF)", "Private"]
    assert "demoted_to_other_corporate" in out.loc[2, "owner_flags"]


def test_assignment_is_hashable_and_immutable():
    assignment = OwnerAssignment("federal", "Federal (NF)", ("unrefined",))
    assert hash(assignment)
    with pytest.raises(Exception):
        assignment.owner_class = "state"
