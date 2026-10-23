"""The trajectory library: every stand's eligible options, rendered to FVS keyfiles and run."""

import stat

import pandas as pd
import pytest

from pipeline.s3_management.regime_assignment import load_regimes_config
from pipeline.s4_fvs import regime_templates as rt
from pipeline.s4_fvs import trajectory_library as tl

CONFIG = load_regimes_config()


def _stands():
    return pd.DataFrame({
        "unit_id": ["u1", "u2", "rip"],
        "PLT_CN": ["1234567890123456789", "1234567890123456789", "1234567890123456789"],
        "stand_age": [22, 22, 22],
        "unit_class": ["upland", "upland", "riparian"],
        "owner_class": ["corporate", "family", "corporate"],
        "forest_branch": ["pine", "pine", "pine"],
    })


def test_the_open_menu_never_offers_planted_pine_to_hardwood():
    menu = tl.menu_for(tl.OPEN_MENU, CONFIG, "corporate", "hardwood")

    assert "pine_plantation_short_rotation" not in menu
    assert {"natural_short_rotation", "hardwood_clearcut_regen", "no_management"} <= set(menu)


def test_a_repeating_rotation_clearcuts_again_inside_the_horizon_with_its_own_regeneration():
    entries = tl.build_entries(CONFIG, "pine_plantation_short_rotation", 0.0,
                               tl.RotationMode.REPEAT, stand_sdi=None)
    clearcuts = [e for e in entries if e.thin.proportion >= 1.0]

    assert len(clearcuts) >= 2
    assert all(e.regen for e in clearcuts)
    assert all(e.thin.year <= tl.LAST_ENTRY_YEAR for e in entries)


def test_a_single_rotation_clearcuts_once():
    entries = tl.build_entries(CONFIG, "pine_plantation_short_rotation", 0.0,
                               tl.RotationMode.SINGLE, stand_sdi=None)

    assert sum(e.thin.proportion >= 1.0 for e in entries) == 1


def test_an_offset_moves_regeneration_with_its_harvest_and_drops_what_leaves_the_horizon():
    entries = tl.build_entries(CONFIG, "pine_plantation_short_rotation", 22.0,
                               tl.RotationMode.SINGLE, stand_sdi=None)

    shifted = tl.shift_entries(entries, 25)

    assert all(e.thin.year <= tl.LAST_ENTRY_YEAR for e in shifted)
    for before, after in zip(entries, shifted):
        assert after.thin.year == before.thin.year + 25
        assert [r.year for r in after.regen] == [r.year + 25 for r in before.regen]
    assert tl.shift_entries(entries, 100) == []


def test_a_single_rotation_renders_byte_identical_to_regime_templates():
    template, params = tl.resolve_first_rotation(CONFIG, "pine_plantation_short_rotation", 22.0)
    entries = tl.build_entries(CONFIG, "pine_plantation_short_rotation", 22.0,
                               tl.RotationMode.SINGLE, stand_sdi=None)

    ours = tl.render("1234567890123456789", template, entries)
    theirs = rt.render_keyfile("S1234567890123456789", "1234567890123456789", template, params,
                               num_cycle=tl.NUM_CYCLE)

    assert ours == theirs


def test_a_leto_inventory_keyfile_reads_leto_table_names():
    text = tl.render("MU7", "no_management", [], tables=tl.LETO_TABLES)

    assert "SELECT * FROM FVS_StandInit WHERE" in text
    assert "SELECT * FROM FVS_TreeInit WHERE" in text


def test_enumerate_shares_runs_by_plot_and_gives_riparian_only_no_management():
    keyfiles: dict[str, str] = {}

    lib = tl.enumerate_library(tl.OPEN_MENU, _stands(), sdi={}, keyfiles=keyfiles, config=CONFIG)

    assert lib.loc[lib["unit_id"] == "rip", "prescription"].tolist() == ["no_management"]
    assert not lib.duplicated(["unit_id", "prescription"]).any()
    assert set(lib["run_hash"]) == set(keyfiles)
    # u1 and u2 share a plot, so an option both may take is one run, not two.
    both = lib[lib["unit_id"].isin(["u1", "u2"])].groupby("prescription")["run_hash"].nunique()
    assert (both == 1).all()
    assert lib["PLT_CN"].eq("1234567890123456789").all()
    offsets = {tl.split_label(p)[1] for p in lib["prescription"]}
    assert offsets <= set(tl.OFFSETS_0_25)


def test_split_label_round_trips():
    assert tl.split_label(tl.variant_label("public_thin_restore", 10)) == ("public_thin_restore", 10)
    assert tl.split_label("no_management") == ("no_management", 0)


def test_merge_harvest_year_rows_takes_removals_from_the_cut_and_state_from_after_it():
    raw = pd.DataFrame({
        "PLT_CN": ["1"] * 3, "prescription": ["p"] * 3, "Year": [2027, 2032, 2032],
        "RmvCode": [0, 1, 2], "RMCuFt": [0.0, 900.0, 0.0], "MCuFt": [1000.0, 1100.0, 200.0],
        "RTpa": [0.0, 300.0, 0.0], "RTCuFt": [0.0, 950.0, 0.0], "RBdFt": [0.0, 10.0, 0.0],
    })

    out = tl.merge_harvest_year_rows(raw).set_index("Year")

    assert len(out) == 2
    assert out.loc[2032, "RMCuFt"] == 900.0 and out.loc[2032, "MCuFt"] == 200.0


def test_stand_sdi_tables_key_on_exact_stand_ids():
    trees = pd.DataFrame({"STAND_CN": ["1234567890123456789"] * 2, "SPECIES": ["131", "611"],
                          "HISTORY": [1, 1], "TREE_COUNT": [10.0, 5.0], "DIAMETER": [10.0, 10.0]})

    sdi = tl.stand_sdi_tables(trees)

    assert set(sdi) == {"1234567890123456789"}
    assert set(sdi["1234567890123456789"]) == {"LP", "SU"}


def test_a_failed_fvs_run_is_recorded_and_writes_no_trajectory(tmp_path):
    fake = tmp_path / "FVSsn"
    fake.write_text("#!/bin/sh\nexit 2\n")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    data_db = tmp_path / "FVS_Data.db"
    data_db.write_bytes(b"")
    paths = tl.RunPaths(work=tmp_path / "work", data_db=data_db, fvs_bin=fake)

    tl.run_fvs({"h1": "keyfile text"}, {"h1": "1"}, paths, workers=1)

    failures = pd.read_csv(paths.failures)
    assert failures.to_dict("records") == [{"run_hash": "h1", "error": "FVS stop code 2"}]
    assert not paths.cycles.exists()


@pytest.mark.parametrize("policy", [tl.OPEN_MENU])
def test_the_open_menu_is_the_decks_repeat_offsets_25(policy):
    assert policy.rotations is tl.RotationMode.REPEAT
    assert policy.forest_types is tl.ForestTypeRule.FILTER
    assert policy.offsets == (0, 5, 10, 15, 20, 25)
