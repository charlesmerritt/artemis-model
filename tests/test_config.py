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
# See notes/management-regimes-by-owner.md. Keyed on the LETO ownership vocabulary
# (OWN_CODE/OWN_TYPE from FVS_StandInit.csv), NOT the Harris raster values — the two
# use the same column name with different meanings. See issue #19.

LETO_OWNER_CLASSES = [
    "unknown", "private", "corporate", "federal", "state", "county", "ngo", "other",
]

# LETO OWN_CODE -> OWN_TYPE, verified against the 2026-08-04 Hard_Ownership_Boundaries
# run (57,527 stands). This is the vocabulary the FVS inputs actually carry.
LETO_CODE_TO_TYPE = {
    0: "Unknown", 1: "Private", 2: "Corporate", 3: "Federal",
    4: "State", 5: "County", 6: "NGO", 7: "Other",
}


def _resolve_params(params, inv_year):
    """Turn `*_offset` config params into the absolute years regime_templates consumes."""
    resolved = {}
    for key, value in params.items():
        if key.endswith("_offset"):
            resolved[key[: -len("_offset")]] = inv_year + value
        else:
            resolved[key] = value
    return resolved


def test_management_regimes_cover_every_leto_owner_class(management_regimes):
    assert set(management_regimes["owner_classes"]) == set(LETO_OWNER_CLASSES)


def test_leto_own_codes_match_the_observed_vocabulary(management_regimes):
    """Codes and type strings must match what FVS_StandInit.csv actually contains."""
    for name, block in management_regimes["owner_classes"].items():
        code = block["leto_own_code"]
        assert LETO_CODE_TO_TYPE[code] == block["leto_own_type"], (
            f"{name}: LETO code {code} is {LETO_CODE_TO_TYPE[code]!r}, "
            f"not {block['leto_own_type']!r}"
        )
    codes = [b["leto_own_code"] for b in management_regimes["owner_classes"].values()]
    assert sorted(codes) == sorted(LETO_CODE_TO_TYPE), "LETO codes must be covered exactly once"


def test_leto_and_harris_code_systems_are_never_conflated(management_regimes):
    """The two OWN_CODE vocabularies collide. Assert they are kept distinct.

    LETO 3 is Federal but Harris 3 is family_forest; LETO 4 is State but Harris 4 is
    corporate_forest. Treating one as the other assigns the wrong regime to every stand
    in the AOI. The config must therefore never claim the integers agree.
    """
    crosswalk = management_regimes["crosswalks"]["leto_own_code_to_harris_raster_value"]
    collisions = 0
    for name, block in management_regimes["owner_classes"].items():
        leto = block["leto_own_code"]
        harris = block["harris_raster_value"]
        assert crosswalk[leto] == harris, f"{name}: crosswalk disagrees with the class block"
        if harris is not None and harris != leto:
            collisions += 1
    # Only Unknown (0 -> 0) survives as an identity mapping; everything else moves.
    assert collisions >= 5, (
        "expected the LETO and Harris numbering to diverge for most classes; "
        "if this fails, one of the two vocabularies has been silently rewritten"
    )
    assert crosswalk[0] == 0, "Unknown is the one code that means the same in both systems"


def test_harris_raster_values_resolve_to_the_named_harris_class(
    management_regimes, projection_config
):
    """Where a Harris equivalent is claimed, it must be the real Harris class."""
    harris = projection_config["ownership"]["classes"]
    for name, block in management_regimes["owner_classes"].items():
        value, cls = block["harris_raster_value"], block["harris_class"]
        if value is None:
            assert cls is None, f"{name}: harris_class set without a raster value"
            continue
        assert harris[value] == cls, f"{name}: Harris {value} is {harris[value]!r}, not {cls!r}"


def test_classes_harris_cannot_express_are_recorded(management_regimes):
    """NGO and Other have no Harris equivalent — the reason to key off LETO at all."""
    for name in ("ngo", "other"):
        block = management_regimes["owner_classes"][name]
        assert block["harris_class"] is None
        assert block["harris_raster_value"] is None
    absent = management_regimes["harris_classes_absent_from_leto"]
    assert "tribal_forest" in absent, "Harris tribal has no LETO counterpart; keep it visible"


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
    """The owner crosswalks must partition the classes — none missing, none double-mapped."""
    for name in ("tpo_owner_group", "lamps_mha_group"):
        crosswalk = management_regimes["crosswalks"][name]
        mapped = [cls for members in crosswalk.values() for cls in members]
        assert sorted(mapped) == sorted(LETO_OWNER_CLASSES), f"{name} does not partition"


def test_fvs_db_group_is_not_an_ownership_vocabulary(management_regimes):
    """DB_GROUP flattens the owner and management axes; regimes must not key off it.

    Verified against the LETO run: DB_GROUP == OWN_TYPE for all 39,824 upland stands and
    'Riparian' for all 17,703 riparian stands, so the 9 groups are 8 owners + a geometry
    class that overrides ownership entirely.
    """
    db = management_regimes["crosswalks"]["fvs_db_group"]
    assert "Riparian" in db["groups"]
    assert len(db["groups"]) == 9
    owner_types = {b["leto_own_type"] for b in management_regimes["owner_classes"].values()}
    assert set(db["groups"]) - {"Riparian"} == owner_types
    assert "Riparian" not in owner_types, "Riparian is a management class, not an owner"


def test_riparian_override_is_unconditional(management_regimes):
    """Riparian beats ownership: rank 1 in the precedence ladder, no exemptions."""
    from pipeline.s3_management.regime_assignment import RIPARIAN_SMZ_PCT, assign_regime

    override = management_regimes["riparian_override"]
    assert override["regime"] == "no_management"
    assert override["mgmt_class_value"] == 1
    assert override["smz_pct_threshold"] == RIPARIAN_SMZ_PCT

    ladder = management_regimes["precedence"]
    assert ladder[0]["rule"] == "riparian_override" and ladder[0]["rank"] == 1

    # The SMZ fallback still holds for every owner class the code can currently see.
    for block in management_regimes["owner_classes"].values():
        harris = block["harris_raster_value"]
        if harris is None:
            continue
        unit = {"OWN_CODE": harris, "SMZ_Pct": override["smz_pct_threshold"]}
        assert assign_regime(unit) == ("no_management", {})


def test_config_direction_matches_assignment_code(management_regimes, projection_config):
    """The config's direction and the executed rule must agree, via the Harris crosswalk.

    `regime_assignment.py` still speaks Harris codes, so this compares through
    `harris_raster_value` rather than `leto_own_code` — passing a LETO code straight in
    is the bug tracked as issue #19, pinned by the test below.

    `assignment_status: current` means the code reproduces this regime today. `proposed`
    means the direction has moved ahead and must name what it `supersedes`.
    """
    from pipeline.s3_management.regime_assignment import assign_regime

    inv_year = projection_config["projection"]["base_year"]
    for name, block in management_regimes["owner_classes"].items():
        status = block["assignment_status"]
        assert status in ("current", "proposed"), f"{name}: bad assignment_status {status!r}"

        harris = block["harris_raster_value"]
        expected_block = block["supersedes"] if status == "proposed" else block["default"]

        if "by_forest_type" in expected_block:
            cases = [
                ({"FORTYPCD": 161}, expected_block["by_forest_type"]["pine"]),
                ({"FORTYPCD": 503}, expected_block["by_forest_type"]["other"]),
            ]
        else:
            cases = [({}, expected_block)]

        for extra, expected in cases:
            # Classes Harris cannot express reach the code's unknown-owner fallback.
            own_code = harris if harris is not None else None
            unit = {"OWN_CODE": own_code, "SMZ_Pct": 0.0, **extra}
            got_regime, got_params = assign_regime(unit, inv_year=inv_year)
            assert got_regime == expected["regime"], (
                f"{name}: code gives {got_regime!r}, config says {expected['regime']!r}"
            )
            assert got_params == _resolve_params(expected["params"], inv_year), (
                f"{name}: params disagree with code"
            )


def test_leto_own_code_fed_to_assignment_code_gives_the_wrong_regime(management_regimes):
    """Pin the live bug (issue #19) so a fix cannot land unnoticed.

    `regime_assignment.assign_regime` reads `OWN_CODE` and interprets it as a Harris
    value. The LETO FVS_StandInit.csv column of the same name uses a different system,
    so feeding it through today mis-assigns. This test asserts the *current wrong*
    behaviour on purpose: when #19 is fixed it must fail and be replaced.
    """
    from pipeline.s3_management.regime_assignment import assign_regime

    federal = management_regimes["owner_classes"]["federal"]
    assert federal["leto_own_code"] == 3
    # LETO 3 = Federal, but Harris 3 = family_forest, so the code gives the family regime.
    regime, _ = assign_regime({"OWN_CODE": 3, "SMZ_Pct": 0.0})
    assert regime == "thin_from_below", (
        "if this now returns selection_harvest, issue #19 is fixed — delete this test"
    )
    assert regime != federal["default"]["regime"], (
        "LETO Federal should map to selection_harvest; the code disagrees, which is the bug"
    )

    state = management_regimes["owner_classes"]["state"]
    assert state["leto_own_code"] == 4
    # LETO 4 = State, but Harris 4 = corporate_forest, so a state stand gets clearcut.
    regime, _ = assign_regime({"OWN_CODE": 4, "SMZ_Pct": 0.0, "FORTYPCD": 503})
    assert regime == "clearcut", (
        "if this no longer returns clearcut, issue #19 is fixed — delete this test"
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
