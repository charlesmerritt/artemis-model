"""
Tests that verify the config files are internally consistent and complete.
These are the first tests that must pass — they validate the scaffold before
any data is acquired.

Config-only tests run anywhere. Tests that touch the external data drive skip
when it is absent (see `_data_paths_or_skip`), so this file is CI-safe.
"""

from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


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


# --- management_regimes.yaml: the unified owner-class -> regime direction -------------
# See notes/management-regimes-by-owner.md. The config is the direction; the executed
# rule still lives in pipeline/s3_management/regime_assignment.py. These tests hold the
# two together so the direction cannot drift out of agreement with the code unnoticed.

FOREST_OWNER_CLASSES = [
    "unknown_forest", "family_forest", "corporate_forest",
    "tribal_forest", "federal_forest", "state_forest", "local_forest",
]


def _resolve_params(params, inv_year):
    """Turn `*_offset` config params into the absolute years regime_templates consumes."""
    resolved = {}
    for key, value in params.items():
        if key.endswith("_offset"):
            resolved[key[: -len("_offset")]] = inv_year + value
        else:
            resolved[key] = value
    return resolved


def test_management_regimes_cover_every_forest_owner_class(management_regimes):
    """Every Harris forest class gets a regime; masked classes never do."""
    assert set(management_regimes["owner_classes"]) == set(FOREST_OWNER_CLASSES)
    assert set(management_regimes["masked_classes"]) == {"non_forest", "water"}


def test_management_regimes_raster_values_match_projection_config(
    management_regimes, projection_config
):
    """Owner-class raster values are the Harris pixel values, not a parallel numbering."""
    harris = projection_config["ownership"]["classes"]
    blocks = {**management_regimes["owner_classes"], **management_regimes["masked_classes"]}
    for name, block in blocks.items():
        assert harris[block["raster_value"]] == name


def test_management_regimes_own_code_equals_raster_value(management_regimes):
    """regime_assignment.py keys off LETO OWN_CODE while the raster carries Harris values.

    The assignment code only works because those two numberings coincide for the six
    named classes. Assert it rather than leaving it as a coincidence to rediscover.
    """
    from pipeline.s3_management.regime_assignment import (
        CORPORATE, FAMILY, FEDERAL, LOCAL, STATE, TRIBAL,
    )
    code_values = {
        "family_forest": FAMILY, "corporate_forest": CORPORATE, "tribal_forest": TRIBAL,
        "federal_forest": FEDERAL, "state_forest": STATE, "local_forest": LOCAL,
    }
    for name, own_code in code_values.items():
        block = management_regimes["owner_classes"][name]
        assert block["own_code"] == own_code
        assert block["raster_value"] == own_code


def test_management_regimes_reference_only_implemented_regimes(management_regimes):
    """No owner class may point at a regime the template library cannot render."""
    from pipeline.s4_fvs.regime_templates import REGIMES

    assert set(management_regimes["regimes"]) == set(REGIMES)
    assert management_regimes["riparian_override"]["regime"] in REGIMES
    for name, block in management_regimes["owner_classes"].items():
        for regime in block["eligible_regimes"]:
            assert regime in REGIMES, f"{name}: unknown regime {regime!r}"


def test_management_regimes_default_is_always_eligible(management_regimes):
    """A class cannot default to a regime it is not eligible for."""
    for name, block in management_regimes["owner_classes"].items():
        default = block["default"]
        if "by_forest_type" in default:
            defaults = [b["regime"] for b in default["by_forest_type"].values()]
        else:
            defaults = [default["regime"]]
        for regime in defaults:
            assert regime in block["eligible_regimes"], f"{name}: {regime} not eligible"


def test_tpo_crosswalk_names_match_tpo_targets_exactly(management_regimes, tpo_targets):
    """The scheduler looks up OWNER caps by these strings; a typo silently uncaps a class."""
    crosswalk = management_regimes["crosswalks"]["tpo_owner_group"]
    groups = {k: v for k, v in crosswalk.items() if not k.startswith("_")}
    available = set(tpo_targets["by_owner_group"])
    for group in groups:
        assert group in available, f"{group!r} is not a key in config/tpo_targets.yaml"


def test_every_owner_class_appears_once_in_each_crosswalk(management_regimes):
    """Both crosswalks must partition the owner classes — no class missing, none double-mapped."""
    for crosswalk in management_regimes["crosswalks"].values():
        mapped = [cls for members in crosswalk.values() for cls in members]
        assert sorted(mapped) == sorted(FOREST_OWNER_CLASSES)


def test_riparian_override_matches_assignment_code(management_regimes):
    """Riparian is unconditional: no entry, no owner class able to override it."""
    from pipeline.s3_management.regime_assignment import RIPARIAN_SMZ_PCT, assign_regime

    override = management_regimes["riparian_override"]
    assert override["regime"] == "no_management"
    assert override["smz_pct_threshold"] == RIPARIAN_SMZ_PCT

    for block in management_regimes["owner_classes"].values():
        unit = {"OWN_CODE": block["own_code"], "SMZ_Pct": override["smz_pct_threshold"]}
        assert assign_regime(unit) == ("no_management", {})


def test_config_direction_matches_assignment_code(management_regimes, projection_config):
    """The config's stated direction and the executed rule must agree.

    `assignment_status: current` means regime_assignment.py produces exactly this today.
    `proposed` means the direction has moved ahead of the code, and the block must name
    what it supersedes — which is then what the code has to produce. Either way an
    unannounced change on one side fails here.
    """
    from pipeline.s3_management.regime_assignment import assign_regime

    inv_year = projection_config["projection"]["base_year"]
    for name, block in management_regimes["owner_classes"].items():
        status = block["assignment_status"]
        assert status in ("current", "proposed"), f"{name}: bad assignment_status {status!r}"

        default = block["default"]
        if "by_forest_type" in default:
            cases = [
                ({"FORTYPCD": 161}, default["by_forest_type"]["pine"]),      # loblolly-shortleaf
                ({"FORTYPCD": 503}, default["by_forest_type"]["other"]),     # oak-hickory
            ]
        else:
            cases = [({}, default)]

        for extra, expected in cases:
            if status == "proposed":
                expected = block["supersedes"]
            unit = {"OWN_CODE": block["own_code"], "SMZ_Pct": 0.0, **extra}
            got_regime, got_params = assign_regime(unit, inv_year=inv_year)
            assert got_regime == expected["regime"], f"{name}: regime disagrees with code"
            assert got_params == _resolve_params(expected["params"], inv_year), (
                f"{name}: params disagree with code"
            )


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
