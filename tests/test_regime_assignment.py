"""Prescriptions are chosen per Harris owner class (config/management_regimes.yaml)."""

import pytest

from pipeline.s3_management.regime_assignment import assign_prescription, eligible_prescriptions


def test_corporate_pine_stand_takes_the_corporate_menu():
    p = assign_prescription({"OWN_CODE": 4, "FORTYPCD": 161, "stand_age": 22})
    assert p.owner_class == "corporate"
    assert p.prescription_id in eligible_prescriptions("corporate", "pine")


@pytest.mark.parametrize("owner", ["family", "corporate", "tribal", "federal", "state", "local", "unknown"])
def test_every_harris_forest_class_may_decline_to_harvest(owner):
    assert "no_management" in eligible_prescriptions(owner)
