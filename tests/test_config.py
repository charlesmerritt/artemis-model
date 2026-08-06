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


def test_projection_config_harvest_selection_is_annealing(projection_config):
    """Harvest is decided by the scheduler, not drawn from a fitted probability model.

    The previous `forward_method: pseudo_deterministic` drew a per-pixel harvest schedule
    from an LCMS-fitted model. Under the adopted architecture the scheduler selects one
    precomputed trajectory per stand from its ownership-class library by simulated
    annealing; the fitted model became validation evidence.
    See notes/trajectory-library-and-annealing.md.
    """
    harvest = projection_config["harvest"]
    assert harvest["selection_method"] == "simulated_annealing"
    assert "forward_method" not in harvest, (
        "forward_method is the retired pseudo-deterministic draw; use selection_method"
    )


def test_annealing_schedule_is_well_formed(projection_config):
    ann = projection_config["harvest"]["annealing"]
    assert 0.0 < ann["cooling_factor"] < 1.0, "geometric cooling must contract"
    assert 0.0 < ann["initial_accept_rate"] < 1.0
    assert ann["min_temperature"] > 0
    assert ann["iterations_per_temperature"] >= 1
    assert ann["restarts"] >= 1
    weights = ann["move_weights"]
    assert set(weights) == {"single_stand", "block", "period_swap"}
    assert sum(weights.values()) == pytest.approx(1.0)
    assert weights["block"] > 0, (
        "block moves are required: single-stand moves alone stall under a green-up penalty"
    )


# --- Ownership-class prescription libraries (config/prescriptions.yaml) ---------------
#
# The scheduler can only select what the library contains, so these tests are the guard
# on the decision space itself. See notes/trajectory-library-and-annealing.md section 3.

# Prescription families implemented in pipeline/s4_fvs/regime_templates.py.
REGIME_FAMILIES = {
    "no_management", "clearcut", "thin_from_below",
    "selection_harvest", "plantation_rotation",
}


def _families(entry):
    return [p["family"] for p in entry["prescriptions"]]


def test_prescription_families_are_implemented(prescriptions_config):
    """Every family named in the config must exist in regime_templates.REGIMES."""
    used = set()
    for entry in prescriptions_config["ownership_libraries"].values():
        used.update(_families(entry))
    used.update(_families(prescriptions_config["overrides"]["riparian"]))
    unknown = used - REGIME_FAMILIES
    assert not unknown, f"prescriptions.yaml names unimplemented families: {sorted(unknown)}"


def test_prescription_libraries_cover_every_unmasked_ownership_class(
    prescriptions_config, projection_config
):
    """Every forest ownership class needs a library, or its stands have no decision space."""
    classes = projection_config["ownership"]["classes"]
    masked = set(prescriptions_config["masked_classes"])
    expected = {name for name in classes.values() if name not in masked}
    assert set(prescriptions_config["ownership_libraries"]) == expected


def test_prescription_owner_codes_match_projection_config(
    prescriptions_config, projection_config
):
    classes = projection_config["ownership"]["classes"]
    for name, entry in prescriptions_config["ownership_libraries"].items():
        assert classes[entry["owner_code"]] == name, (
            f"{name} is owner_code {entry['owner_code']}, which projection.yaml calls "
            f"{classes[entry['owner_code']]!r}"
        )


def test_masked_classes_match_projection_mask_values(prescriptions_config, projection_config):
    classes = projection_config["ownership"]["classes"]
    masked = {classes[v] for v in projection_config["ownership"]["mask_values"]}
    assert set(prescriptions_config["masked_classes"]) == masked


def test_every_library_offers_no_management(prescriptions_config):
    """A stand must always be allowed to grow untreated.

    Without it a binding volume cap has no feasible answer, and "the plan harvested this
    stand" stops being a decision the scheduler made.
    """
    for name, entry in prescriptions_config["ownership_libraries"].items():
        assert "no_management" in _families(entry), f"{name} cannot choose to grow untreated"


def test_riparian_override_is_no_management_only(prescriptions_config):
    """No-entry is enforced by the absence of an alternative, not by a priced constraint.

    A penalty weight can be tuned; an empty menu cannot. This is the structural form of
    the "no entry, ever" decision in notes/methodology-directions.md item 2.
    """
    assert _families(prescriptions_config["overrides"]["riparian"]) == ["no_management"]


def test_public_libraries_exclude_clearcut(prescriptions_config):
    """No clearcut in the v1 public multiple-use eligible sets."""
    for name in ("federal_forest", "state_forest", "local_forest", "tribal_forest"):
        families = _families(prescriptions_config["ownership_libraries"][name])
        assert "clearcut" not in families, f"{name} offers clearcut"


def test_prescription_offsets_fall_within_the_projection_horizon(prescriptions_config):
    """Offsets are years after inventory; a treatment past the horizon never happens."""
    horizon = prescriptions_config["horizon_years"]
    for name, entry in prescriptions_config["ownership_libraries"].items():
        for presc in entry["prescriptions"]:
            for key, values in (presc.get("grid") or {}).items():
                if not key.endswith("_offset"):
                    continue
                for value in values:
                    assert 0 < value <= horizon, (
                        f"{name}/{presc['family']}: {key}={value} is outside the "
                        f"{horizon}-year horizon"
                    )


def test_prescription_base_year_matches_projection_config(prescriptions_config, projection_config):
    assert prescriptions_config["base_year"] == projection_config["projection"]["base_year"]
    assert prescriptions_config["horizon_years"] == projection_config["projection"]["horizon_years"]


def test_library_size_stays_within_the_run_budget(prescriptions_config):
    """Trajectory count per stand is the FVS run multiplier for the whole landscape.

    Each grid expands as a cartesian product, so one extra value in one list multiplies
    the run count for every stand in that class. The ceiling is a budget decision
    (notes/trajectory-library-and-annealing.md section 4), so it is asserted rather than
    left to drift.
    """
    for name, entry in prescriptions_config["ownership_libraries"].items():
        total = 0
        for presc in entry["prescriptions"]:
            size = 1
            for values in (presc.get("grid") or {}).values():
                size *= len(values)
            total += size
        assert 2 <= total <= 12, f"{name} library has {total} trajectories per stand"


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
