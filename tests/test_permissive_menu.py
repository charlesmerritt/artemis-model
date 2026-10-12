"""Every owner class may run short and long clearcut rotations and a thin (2026-10-08).

The menu is deliberately permissive: owner class no longer restricts which silvicultural
system a stand may take, only which one it takes by default. Forest type still filters
the menu, so a hardwood stand is never offered a planted-pine rotation.
"""

import pytest

from pipeline.s3_management.regime_assignment import eligible_prescriptions, load_regimes_config

OWNERS = ["family", "corporate", "tribal", "federal", "state", "local", "unknown"]
BRANCHES = ["pine", "hardwood", "other"]


def _rotation_years(config: dict, name: str) -> int | None:
    spec = config["prescriptions"][name]
    if spec["template"] not in {"clearcut", "plantation_rotation"}:
        return None
    return spec["schedule"]["rotation_years"]


@pytest.mark.parametrize("owner", OWNERS)
@pytest.mark.parametrize("branch", BRANCHES)
def test_every_owner_and_type_has_a_short_and_a_long_clearcut_rotation(owner, branch):
    config = load_regimes_config()
    rotations = [
        r for r in (_rotation_years(config, p) for p in eligible_prescriptions(owner, branch))
        if r is not None
    ]
    assert min(rotations) <= 30, f"{owner}/{branch}: no short rotation in {rotations}"
    assert max(rotations) >= 35, f"{owner}/{branch}: no long rotation in {rotations}"


@pytest.mark.parametrize("owner", OWNERS)
@pytest.mark.parametrize("branch", BRANCHES)
def test_every_owner_and_type_may_thin(owner, branch):
    config = load_regimes_config()
    templates = {config["prescriptions"][p]["template"] for p in eligible_prescriptions(owner, branch)}
    assert templates & {"thin_from_below", "thin_from_below_repeated"}


@pytest.mark.parametrize("owner", OWNERS)
def test_hardwood_is_never_offered_a_planted_pine_rotation(owner):
    assert "pine_plantation_short_rotation" not in eligible_prescriptions(owner, "hardwood")
    assert "pine_plantation_long_rotation" not in eligible_prescriptions(owner, "hardwood")
