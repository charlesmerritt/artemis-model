"""Characterize current regeneration gaps; these assertions are not desired behavior.

Run from the repository root: python -m research.regeneration_audit.probe
No FIA data or FVS binary required. Update/retire probes when the behavior is repaired.
"""

from unittest.mock import patch

import geopandas as gpd
import pandas as pd
from shapely.geometry import Point

from pipeline.s3_management.regime_assignment import assign_prescription
from pipeline.s4_fvs import build_fvs_inputs as inputs
from pipeline.s4_fvs.fallback_treelists import resolve_regeneration
from pipeline.s4_fvs.regime_library import render_keyfile
from pipeline.s4_fvs.regime_templates import build_regeneration
from pipeline.spatial_ref import project_crs


def main():
    planted = build_regeneration("plantation_rotation", {
        "clearcut_year": 2030, "stand_sdi": {"SA": 100},
    })
    assert [(r.species, r.trees_per_acre) for r in planted] == [("LP", 605.0)]
    print("Plantation with only SA in its supplied composition -> LP, 605 TPA")

    natural = build_regeneration("clearcut", {"year": 2030})
    assert [(r.species, r.trees_per_acre) for r in natural] == [("LP", 400.0)]
    print("Clearcut without composition -> LP, 400 TPA; no neighbor lookup")

    key = render_keyfile("MU_1", "1234567890123456789", "pine_plantation_industrial")
    assert "ThinDBH" in key and "Estab" not in key
    print("YAML-library plantation keyfile -> harvest present, Estab absent")

    prescription = assign_prescription({"OWN_CODE": 4, "FORTYPCD": 503, "stand_age": 40})
    assert prescription.regen_slot == "hardwood_regen"
    assert "regen_delay_years" not in prescription.params
    regen = build_regeneration(prescription.template, prescription.params)
    assert regen[0].year == prescription.params["year"] + 1
    print("Hardwood prescription's configured 3-year delay -> rendered 1-year delay")

    assert resolve_regeneration("planted_pine_regen").tree_source == "REGEN_FIXED"
    print("Regeneration resolver -> fixed slot unconditionally")

    units = gpd.GeoDataFrame({
        "MU_ID": ["recipient", "dead", "live"], "FORTYPCD": [161, 161, 161],
    }, geometry=[Point(0, 0), Point(100, 0), Point(200, 0)], crs=project_crs())
    trees = pd.DataFrame({
        "MU_ID": ["live"], "STAND_ID": ["MU_live"], "TREE_COUNT": [200.0],
        "TREE_SOURCE": ["FIA_WEIGHTED_DIRECT"],
    })
    # Stale caller-supplied runnable membership: a valid second donor is available.
    with patch.object(inputs, "plt_cn_for_slot", side_effect=RuntimeError("fixed requested")) as pin:
        result = inputs.impute_nearest_runnable(
            units, trees, {"dead", "live"}, on_missing_fallback="skip",
        )
    assert pin.call_count == 1 and "recipient" not in set(result.MU_ID)
    print("Nearest donor lacks rows -> fixed requested despite valid same-type donor at 200m")

    own_trees = pd.DataFrame({"STAND_CN": ["1234567890123456789"], "TREE_COUNT": [100.0]})
    weights = pd.DataFrame({
        "MU_ID": ["recipient", "recipient"],
        "PLT_CN": ["1234567890123456789", "1234567890123456790"], "WEIGHT": [0.5, 0.5],
    })
    partial, runnable = inputs.build_tree_init(weights, own_trees)
    assert runnable == {"recipient"} and partial.TREE_COUNT.sum() == 50.0
    print("Half the weighted donor coverage missing -> stand still runnable at 50% stocking")


if __name__ == "__main__":
    main()
