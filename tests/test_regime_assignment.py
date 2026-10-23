"""Prescriptions are chosen per Harris owner class (config/management_regimes.yaml)."""

import pytest

from pipeline.s3_management.regime_assignment import assign_prescription, eligible_prescriptions


def test_corporate_pine_stand_takes_the_corporate_menu():
    p = assign_prescription({"OWN_CODE": 4, "FORTYPCD": 161, "stand_age": 22})
    assert p.owner_class == "corporate"
    # The declared default is the pulpwood rotation, rather than any allowed menu entry.
    assert p.prescription_id == "pine_plantation_short_rotation"
    assert p.params == {"year": 2027}
    assert p.regen_slot == "planted_pine_regen"


@pytest.mark.parametrize("owner", ["family", "corporate", "tribal", "federal", "state", "local", "unknown"])
def test_every_harris_forest_class_may_decline_to_harvest(owner):
    assert "no_management" in eligible_prescriptions(owner)
