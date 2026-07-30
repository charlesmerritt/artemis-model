"""Tests for FVS regime templates + assignment."""

from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline.s4_fvs.regime_templates import (
    REGIMES,
    ThinDBH,
    build_thins,
    render_keyfile,
)
from pipeline.s3_management.regime_assignment import assign_regime, is_pine


def test_thindbh_renders_fixed_width_fields():
    line = ThinDBH(year=2032, proportion=0.4, max_dbh=8.0).render()
    # keyword (10) + 5 fields (10 each) = 60 chars
    assert len(line) == 60
    assert line.startswith("ThinDBH")
    assert line[10:20].strip() == "2032"     # year


def test_thindbh_field_order_year_mindbh_maxdbh_proportion_species():
    line = ThinDBH(year=2040, proportion=0.5, min_dbh=0, max_dbh=999, species=0).render()
    fields = [line[i:i + 10].strip() for i in range(0, 60, 10)]
    assert fields == ["ThinDBH", "2040", "0", "999", "0.50", "0"]


def test_thindbh_rejects_out_of_range_proportion():
    with pytest.raises(ValueError, match="proportion"):
        ThinDBH(year=2030, proportion=1.5).render()


def test_no_management_has_no_thins():
    assert build_thins("no_management", {}) == []


def test_clearcut_removes_everything_once():
    thins = build_thins("clearcut", {"year": 2052})
    assert len(thins) == 1
    assert thins[0].proportion == 1.0
    assert thins[0].min_dbh == 0.0 and thins[0].max_dbh == 999.0


def test_thin_from_below_targets_small_trees():
    thins = build_thins("thin_from_below", {"year": 2032, "max_dbh": 8, "proportion": 0.4})
    assert len(thins) == 1
    assert thins[0].max_dbh == 8.0
    assert thins[0].proportion == 0.4


def test_selection_harvest_repeats_on_interval():
    thins = build_thins("selection_harvest",
                        {"start_year": 2032, "end_year": 2062, "interval": 10, "proportion": 0.2})
    assert [t.year for t in thins] == [2032, 2042, 2052, 2062]
    assert all(t.proportion == 0.2 for t in thins)


def test_plantation_rotation_thins_then_clearcuts():
    thins = build_thins("plantation_rotation",
                        {"thin_year": 2037, "clearcut_year": 2052})
    assert len(thins) == 2
    assert thins[0].proportion < 1.0          # commercial thin
    assert thins[1].proportion == 1.0         # final clearcut
    assert thins[1].year == 2052


def test_build_thins_rejects_unknown_regime():
    with pytest.raises(ValueError, match="unknown regime"):
        build_thins("burn_it_all", {})


def test_render_keyfile_includes_scaffold_and_thins():
    key = render_keyfile("MU_123", "MU_123", "thin_from_below",
                         {"year": 2032, "max_dbh": 8, "proportion": 0.4})
    assert "StandCN" in key and "MU_123" in key
    assert "FVS_TreeInit_Plot" in key and "%Stand_CN%" in key
    assert "ThinDBH" in key
    assert key.rstrip().endswith("Process")


def test_render_keyfile_no_management_has_no_thindbh():
    key = render_keyfile("MU_1", "MU_1", "no_management")
    assert "ThinDBH" not in key


def test_all_registered_regimes_render():
    params = {
        "no_management": {},
        "clearcut": {"year": 2052},
        "thin_from_below": {"year": 2032},
        "selection_harvest": {"start_year": 2032},
        "plantation_rotation": {"thin_year": 2037, "clearcut_year": 2052},
    }
    for name in REGIMES:
        key = render_keyfile("MU_1", "MU_1", name, params[name])
        assert "Process" in key


# ---- assignment ----------------------------------------------------------------------

def test_is_pine_detects_code_and_name():
    assert is_pine({"FORTYPCD": 161})          # loblolly-shortleaf group
    assert is_pine({"ForTypName": "Loblolly pine"})
    assert not is_pine({"FORTYPCD": 500})      # oak-hickory
    assert not is_pine({"forest_type": "Oak-gum-cypress"})


def test_assign_regime_riparian_is_no_management():
    regime, _ = assign_regime({"OWN_CODE": 4, "SMZ_Pct": 80.0, "FORTYPCD": 161})
    assert regime == "no_management"


def test_assign_regime_public_owner_gets_selection():
    regime, params = assign_regime({"OWN_CODE": 6, "SMZ_Pct": 0})
    assert regime == "selection_harvest"
    assert params["interval"] == 10


def test_assign_regime_family_gets_light_thin():
    regime, _ = assign_regime({"OWN_CODE": 3, "SMZ_Pct": 5})
    assert regime == "thin_from_below"


def test_assign_regime_corporate_pine_vs_hardwood():
    pine, _ = assign_regime({"OWN_CODE": 4, "SMZ_Pct": 0, "FORTYPCD": 161})
    hardwood, _ = assign_regime({"OWN_CODE": 4, "SMZ_Pct": 0, "FORTYPCD": 500})
    assert pine == "plantation_rotation"
    assert hardwood == "clearcut"


def test_assign_regime_unknown_owner_defaults_to_thin():
    regime, _ = assign_regime({"SMZ_Pct": 0})
    assert regime == "thin_from_below"
