"""
Tests that verify the config files are internally consistent and complete.
These are the first tests that must pass — they validate the scaffold before
any data is acquired.

Config-only tests run anywhere. Tests that touch project data resolve it through
`pipeline.data_access`, which answers from the /mnt/d drive or the R2 mirror, and
skip only where neither is reachable (see `_data_paths_or_skip`) — so this file
is CI-safe.
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


# The four objective forms in Diaz et al. (2015), "Scheduling model". See docs/references/.
OBJECTIVE_FORMS = {"maximize", "minimize", "evenflow", "evenflow_target"}


def test_scheduler_objectives_use_known_forms(projection_config):
    objectives = projection_config["harvest"]["objectives"]
    assert objectives, "the scheduler needs at least one objective"
    for obj in objectives:
        assert obj["form"] in OBJECTIVE_FORMS, (
            f"{obj['metric']}: unknown objective form {obj['form']!r}; "
            f"choices: {sorted(OBJECTIVE_FORMS)}"
        )
        assert obj["weight"] > 0


def _harvest_volume_objective(projection_config):
    objectives = projection_config["harvest"]["objectives"]
    return next(o for o in objectives if o["metric"] == "harvest_volume")


def test_harvest_volume_target_stays_dimensioned(projection_config):
    """The volume target must stay broken out by county and owner group.

    Diaz et al. (2015) set all targets at a single global level; their scheduler then
    shifted harvest between BLM Districts to hit the landscape total, concentrating it in
    one District — contrary to how BLM actually allocates sale quantities by
    Sustained-Yield Unit. Collapsing `dimensions` to a landscape total reproduces that
    artifact across Florida counties, so it is asserted rather than left to drift.
    """
    volume = _harvest_volume_objective(projection_config)
    assert volume["form"] == "evenflow_target"
    assert set(volume["dimensions"]) >= {"county", "owner_group"}


def test_harvest_volume_target_period_exists(projection_config, project_root):
    """Forward and hindcast objectives select periods available in every dimension."""
    import yaml

    volume = _harvest_volume_objective(projection_config)
    with open(project_root / volume["target_source"]) as f:
        targets = yaml.safe_load(f)
    target_groups = [targets["by_county"], targets["by_owner_group"]]
    for key in ("target_period", "hindcast_target_period"):
        period = volume[key]
        assert all(period in values for group in target_groups for values in group.values()), (
            f"{key} {period!r} is not available for every configured target"
        )


def test_hindcast_target_does_not_overlap_lcms_holdout(projection_config):
    volume = _harvest_volume_objective(projection_config)
    assert volume["hindcast_target_period"] == "pre_2015"


def test_one_objective_dominates_the_rest(projection_config):
    """Diaz et al. weighted the binding target 6x against 1x for everything else, so the
    scheduler hits the harvest target first and optimizes the rest within that constraint.
    A flat weight vector means no objective is primary."""
    weights = sorted(o["weight"] for o in projection_config["harvest"]["objectives"])
    if len(weights) > 1:
        assert weights[-1] > weights[-2], "no objective is weighted as primary"


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


# --- ownership_policy.yaml: the two OWN_CODE vocabularies must stay distinct ----------
# See notes/management-regimes-by-owner.md and GitHub issue #20. `OWN_CODE` names two
# different code systems in this project: the Harris raster values the ARTEMIS owner
# classes are built on, and the LETO codes the FVS inputs actually carry.

# LETO OWN_CODE -> OWN_TYPE, verified against the 2026-08-04 Hard_Ownership_Boundaries
# run (57,527 stands).
LETO_CODE_TO_TYPE = {
    0: "Unknown", 1: "Private", 2: "Corporate", 3: "Federal",
    4: "State", 5: "County", 6: "NGO", 7: "Other",
}


def test_leto_own_code_vocabulary_matches_the_observed_run(ownership_policy):
    assert ownership_policy["leto_own_code_to_type"] == LETO_CODE_TO_TYPE


def test_every_owner_class_names_both_vocabularies(ownership_policy):
    """A class that names only one code system is the bug this file exists to prevent."""
    for name, block in ownership_policy["classes"].items():
        assert "harris_values" in block, f"{name}: no harris_values"
        assert "leto_own_codes" in block, f"{name}: no leto_own_codes"


def test_leto_and_harris_code_systems_are_never_conflated(ownership_policy):
    """The two OWN_CODE vocabularies collide. Assert they are kept distinct.

    LETO 3 is Federal but Harris 3 is family; LETO 4 is State but Harris 4 is corporate.
    Treating one as the other assigns the wrong regime to every stand in the AOI. The
    config must therefore never claim the integers agree.
    """
    crosswalk = ownership_policy["leto_own_code_to_harris_value"]
    assert set(crosswalk) == set(LETO_CODE_TO_TYPE), "every LETO code needs a crosswalk entry"
    assert crosswalk[0] == 0, "Unknown is the one code that means the same in both systems"
    moved = sum(1 for k, v in crosswalk.items() if v is not None and v != k)
    assert moved >= 5, (
        "expected the LETO and Harris numbering to diverge for most classes; "
        "if this fails, one of the two vocabularies has been silently rewritten"
    )


def test_the_crosswalk_agrees_with_the_class_blocks(ownership_policy):
    """`leto_own_codes` on a class must land on that class's Harris values."""
    crosswalk = ownership_policy["leto_own_code_to_harris_value"]
    for name, block in ownership_policy["classes"].items():
        for code in block["leto_own_codes"]:
            assert crosswalk[code] in block["harris_values"], (
                f"{name}: LETO {code} crosswalks to Harris {crosswalk[code]}, "
                f"not in {block['harris_values']}"
            )


def test_classes_neither_vocabulary_can_express_are_recorded(ownership_policy):
    """NGO and Other have no Harris class; tribal has no LETO code. Keep both visible."""
    gaps = ownership_policy["vocabulary_gaps"]
    assert "tribal" in gaps["harris_absent_from_leto"]
    assert set(gaps["leto_absent_from_harris"]) == {"ngo", "other"}
    crosswalk = ownership_policy["leto_own_code_to_harris_value"]
    for name, block in gaps["leto_absent_from_harris"].items():
        assert crosswalk[block["leto_own_code"]] is None, f"{name}: claims a Harris value"
    assert ownership_policy["classes"]["tribal"]["leto_own_codes"] == []


def test_issue_20_is_still_open_in_the_assignment_code():
    """`classify_owner` reads OWN_CODE as a *Harris* value; LETO stands carry LETO codes.

    This asserts the bug rather than the fix, so the tripwire fires the day someone
    resolves issue #20 and forgets this call site. Delete it then.
    """
    from pipeline.s3_management.owner_classes import classify_owner

    # LETO 3 = Federal. Harris 3 = family, so a federal stand is classified private.
    assert classify_owner({"OWN_CODE": 3}).owner_class == "private_family", (
        "if this no longer returns private_family, issue #20 is fixed — delete this test"
    )
    # LETO 4 = State. Harris 4 = corporate, so a state stand is classified corporate.
    assert classify_owner({"OWN_CODE": 4}).owner_class in (
        "private_industrial", "private_corporate_other",
    ), "if this no longer returns a corporate class, issue #20 is fixed — delete this test"


def _data_paths_or_skip(config_dir, data_access):
    """Load data_paths.yaml, skipping only when no data source is reachable.

    The paths below name files on the workstation drive (/mnt/d). Off that
    workstation the same files are in the R2 bucket, so absence of the mount no
    longer means absence of the data: `data_access.exists` checks both, and the
    assertions below hold wherever either answers. Only a machine with neither —
    a bare CI runner, with no R2 credentials — skips.

    These checks stay cheap: a hit in the bucket is confirmed from object
    metadata, so nothing here downloads the multi-gigabyte rasters it names.
    """
    import yaml
    from pathlib import Path
    with open(config_dir / "data_paths.yaml") as f:
        paths = yaml.safe_load(f)
    if not Path(paths["drive"]).exists() and not data_access.r2_available():
        pytest.skip(
            f"data drive not mounted ({paths['drive']}) and no R2 fallback "
            "(needs rclone plus RCLONE_CONFIG_R2_* credentials)"
        )
    return paths


def test_data_paths_source_available(config_dir, data_access):
    """At least one declared data source — the drive or the bucket — answers."""
    from pathlib import Path
    paths = _data_paths_or_skip(config_dir, data_access)
    assert Path(paths["drive"]).exists() or data_access.r2_available()


def test_data_paths_treemap_accessible(config_dir, data_access):
    paths = _data_paths_or_skip(config_dir, data_access)
    tif = paths["raw"]["treemap_2022"]["tif"]
    assert data_access.exists(tif), data_access.unavailable_reason(tif)


def test_data_paths_ownership_accessible(config_dir, data_access):
    paths = _data_paths_or_skip(config_dir, data_access)
    tif = paths["raw"]["ownership"]["tif"]
    assert data_access.exists(tif), data_access.unavailable_reason(tif)


def test_data_paths_fia_sqlite_accessible(config_dir, data_access):
    paths = _data_paths_or_skip(config_dir, data_access)
    db = paths["raw"]["fia_sqlite"]["db"]
    assert data_access.exists(db), data_access.unavailable_reason(db)


def test_data_paths_tpo_guidance_accessible(config_dir, data_access):
    """The TPO workbook the harvest-target parser reads."""
    paths = _data_paths_or_skip(config_dir, data_access)
    xlsx = paths["raw"]["tpo_guidance"]["xlsx"]
    assert data_access.exists(xlsx), data_access.unavailable_reason(xlsx)
