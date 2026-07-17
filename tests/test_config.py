"""
Tests that verify the config files are internally consistent and complete.
These are the first tests that must pass — they validate the scaffold before
any data is acquired.

Config-only tests run anywhere. Tests that touch the external data drive skip
when it is absent (see `_data_paths_or_skip`), so this file is CI-safe.
"""

import pytest


def test_extent_has_florida_fips(extent_geojson):
    features = extent_geojson["features"]
    assert len(features) == 1
    props = features[0]["properties"]
    assert props["fips"] == "12"
    assert props["fvs_variant"] == "SN"


def test_extent_geometry_is_valid(extent_geojson):
    from shapely.geometry import shape
    geom = shape(extent_geojson["features"][0]["geometry"])
    assert geom.is_valid
    # Florida bounding box sanity: latitude between 24 and 32, longitude between -88 and -79
    minx, miny, maxx, maxy = geom.bounds
    assert -88 < minx < -79
    assert -88 < maxx < -79
    assert 24 < miny < 32
    assert 24 < maxy < 32


def test_projection_config_fvs_cycles(projection_config):
    cfg = projection_config["projection"]
    assert cfg["horizon_years"] / cfg["cycle_years"] == cfg["n_cycles"]


def test_projection_config_carbon_pools(projection_config):
    """The intended pool set for when carbon is re-enabled. See the test below."""
    expected = {
        "aboveground_live", "belowground_live", "dead_wood",
        "forest_floor", "soil_organic"
    }
    actual = set(projection_config["fvs"]["carbon_pools"])
    assert actual == expected


def test_projection_config_carbon_is_disabled(projection_config):
    """Carbon must stay off until the restart bug is fixed.

    FVS stop/restart silently resets the FFE live-fuel state: Forest_Shrub_Herb
    collapses to a constant 0.02 and Total_Stand_Carbon is understated by ~8% at
    every 5-year barrier, while BA/Tpa/SDI remain bit-identical. Because the
    corruption is invisible to any summary-level check, this is a tripwire rather
    than a preference -- flipping the flag back must be a conscious act.

    Evidence: notes/restart-fidelity-findings.md (arms A/C/E, measured 2026-07-16).
    """
    assert projection_config["fvs"]["carbon_extension"] is False, (
        "carbon_extension must remain false for iteratively coupled runs; "
        "see notes/restart-fidelity-findings.md"
    )


def test_projection_config_harvest_seed_is_locked(projection_config):
    assert projection_config["harvest"]["random_seed"] == 42
    assert projection_config["harvest"]["forward_method"] == "pseudo_deterministic"


def test_bmp_rules_florida_exists(bmp_rules):
    assert "12" in bmp_rules["states"]
    fl = bmp_rules["states"]["12"]
    assert "citation" in fl
    assert "buffers" in fl


def test_bmp_rules_florida_buffer_widths(bmp_rules):
    buffers = bmp_rules["states"]["12"]["buffers"]
    # Verify against Florida FSB 2020 Manual
    assert buffers["ephemeral_intermittent"]["width_ft"] == 35
    assert buffers["perennial_small"]["width_ft"] == 50
    assert buffers["perennial_large"]["width_ft"] == 75
    assert buffers["waterbody"]["width_ft"] == 75


def test_ownership_classes_cover_all_expected(projection_config):
    classes = projection_config["ownership"]["classes"]
    values = list(classes.values())
    required = [
        "family_forest", "corporate_forest", "tribal_forest",
        "federal_forest", "state_forest", "local_forest",
        "unknown_forest", "non_forest", "water"
    ]
    for cls in required:
        assert cls in values, f"Missing ownership class: {cls}"


def test_ownership_pixel_values_match_harris_metadata(projection_config):
    """Pixel values confirmed from US_forest_ownership.tif.xml (Harris et al. 2025)."""
    classes = projection_config["ownership"]["classes"]
    assert classes[0] == "unknown_forest"
    assert classes[1] == "non_forest"
    assert classes[2] == "water"
    assert classes[3] == "family_forest"
    assert classes[4] == "corporate_forest"
    assert classes[5] == "tribal_forest"
    assert classes[6] == "federal_forest"
    assert classes[7] == "state_forest"
    assert classes[8] == "local_forest"


def test_ownership_mask_values(projection_config):
    """non_forest and water must be in the mask list so they are excluded from FVS."""
    mask = projection_config["ownership"]["mask_values"]
    assert 1 in mask  # non_forest
    assert 2 in mask  # water


def _data_paths_or_skip(config_dir):
    """Load data_paths.yaml, skipping if the external data drive is absent.

    These paths live on a workstation-mounted drive (/mnt/d). On a machine
    without it — a CI runner, another checkout — the drive's absence is an
    environmental fact, not a defect, so skip rather than fail. When the drive
    IS mounted the tests below still assert each file is really there.
    """
    import yaml
    from pathlib import Path
    with open(config_dir / "data_paths.yaml") as f:
        paths = yaml.safe_load(f)
    drive = Path(paths["drive"])
    if not drive.exists():
        pytest.skip(f"data drive not mounted: {drive} — see config/data_paths.yaml")
    return paths


def test_data_paths_drive_exists(config_dir):
    """Verify /mnt/d/ is mounted. Skips where the drive is not expected."""
    from pathlib import Path
    paths = _data_paths_or_skip(config_dir)
    assert Path(paths["drive"]).exists()


def test_data_paths_treemap_accessible(config_dir):
    from pathlib import Path
    paths = _data_paths_or_skip(config_dir)
    tif = Path(paths["raw"]["treemap_2022"]["tif"])
    assert tif.exists(), f"TreeMap TIF not found: {tif}"


def test_data_paths_ownership_accessible(config_dir):
    from pathlib import Path
    paths = _data_paths_or_skip(config_dir)
    tif = Path(paths["raw"]["ownership"]["tif"])
    assert tif.exists(), f"Ownership TIF not found: {tif}"


def test_data_paths_fia_sqlite_accessible(config_dir):
    from pathlib import Path
    paths = _data_paths_or_skip(config_dir)
    db = Path(paths["raw"]["fia_sqlite"]["db"])
    assert db.exists(), f"FIA SQLite not found: {db}"
