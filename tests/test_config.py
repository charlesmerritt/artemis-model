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


# --- FVS keyword register (config/fvs_keywords.yaml) ---------------------------------
#
# The register is the assumptions ledger: the parameter field layout of every keyword we
# emit, plus the silvicultural numbers filling them. It is only worth having if it cannot
# drift from the renderer, so these tests tie the two together.

def _keyword_config():
    from pipeline.s4_fvs.regime_templates import KEYWORD_CONFIG
    return KEYWORD_CONFIG


def test_keyword_register_field_order_matches_what_is_rendered():
    """Each keyword's rendered fields must land in the positions the register documents."""
    from pipeline.s4_fvs.regime_templates import Regeneration, ThinDBH, render_schedule_block

    config = _keyword_config()
    fields = config["keyword_fields"]
    omitted = config["omitted_fields"]
    rendered = {
        "ThinDBH": ThinDBH(year=2052, proportion=1.0).render(),
        "Plant": Regeneration(year=2053, species="LP", trees_per_acre=605).render(),
        "Natural": Regeneration(year=2053, species="LP", trees_per_acre=400,
                                natural=True).render(),
    }
    for keyword, line in rendered.items():
        # keyword occupies cols 1-10, then one 10-column slot per field we actually write
        written = len(fields[keyword]) - len(omitted.get(keyword, []))
        assert len(line) == 10 * (1 + written), (
            f"{keyword} renders {len(line) // 10 - 1} fields; register documents "
            f"{len(fields[keyword])} ({fields[keyword]}) less "
            f"{omitted.get(keyword, [])} omitted"
        )

    for line in render_schedule_block(2022, 5, 10).splitlines():
        keyword = line[:10].strip()
        assert keyword in fields, f"{keyword} is rendered but missing from the register"


def test_only_trailing_fields_are_omitted():
    """FVS reads keyword parameters by column, so skipping a field in the middle would
    shift every field after it into the wrong slot."""
    config = _keyword_config()
    for keyword, skipped in config["omitted_fields"].items():
        declared = config["keyword_fields"][keyword]
        assert declared[len(declared) - len(skipped):] == skipped, (
            f"{keyword} omits {skipped}, which is not the tail of {declared}"
        )


def test_timeint_register_names_field_2_as_the_interval():
    """The bug the register exists to prevent: the interval belongs in field 2, not field 1."""
    assert _keyword_config()["keyword_fields"]["TimeInt"] == ["cycle_number", "interval_years"]


def test_every_registered_keyword_has_a_verification_source():
    config = _keyword_config()
    missing = set(config["keyword_fields"]) - set(config["verification"])
    assert not missing, f"keywords with no recorded verification: {sorted(missing)}"
    for keyword, source in config["verification"].items():
        assert source and source.strip(), f"{keyword} has an empty verification note"


def test_regime_defaults_cover_every_parameterized_family():
    """A family whose defaults are not in the register would carry hidden magic numbers."""
    from pipeline.s4_fvs.regime_templates import REGIMES

    registered = set(_keyword_config()["defaults"]["regimes"])
    # no_management takes no parameters; clearcut's only parameter is its year.
    assert registered == set(REGIMES) - {"no_management", "clearcut"}


def test_regeneration_defaults_are_complete():
    regen = _keyword_config()["defaults"]["regeneration"]
    required = {
        "natural_follows_stand_composition", "min_species_share", "fallback_species",
        "plant_species", "plant_tpa", "natural_tpa", "survival_pct", "age", "height_ft",
        "delay_years", "suppress_automatic_regeneration",
    }
    assert required <= set(regen)
    assert 0.0 < regen["min_species_share"] < 1.0
    assert 0.001 <= regen["survival_pct"] <= 100.0
    assert regen["plant_tpa"] > 0 and regen["natural_tpa"] > 0


def test_natural_regeneration_follows_diaz_stand_composition_rule():
    """Diaz et al. (2015) limit natural regeneration to species present in the stand,
    weighted by SDI share. Turning this off silently reverts to one species landscape-wide."""
    assert _keyword_config()["defaults"]["regeneration"]["natural_follows_stand_composition"]


def test_unresolved_assumptions_are_listed_with_a_question():
    """Placeholders are allowed; unrecorded placeholders are not."""
    questions = _keyword_config()["open_questions"]
    assert questions, "the register should name what is still unresolved"
    for entry in questions:
        assert entry["id"] and entry["question"].strip()
    # The two values known to be optimistic/unsourced must stay on the list until fixed.
    ids = {entry["id"] for entry in questions}
    assert {"survival_pct", "natural_tpa"} <= ids


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
