"""Tests for the YAML regime library (config/regimes.yaml + regime_library.py).

These validate the regimes as *silviculture* and as *FVS input*: that each prescription
is internally coherent (ordered, bounded, terminal harvest last) and that it renders to
keyword lines FVS will actually parse.
"""

from pathlib import Path
import sys

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline.s4_fvs.regime_library import (  # noqa: E402
    build_thins,
    cuts,
    get_regime,
    load_library,
    regime_names,
    render_keyfile,
)

INV_YEAR = 2022


@pytest.fixture(scope="module")
def library():
    return load_library()


@pytest.fixture(scope="module")
def names(library):
    return regime_names(library)


def test_library_loads_and_is_not_empty(names):
    assert len(names) >= 8, "expected a regime per owner class plus no_management"
    assert "no_management" in names


def test_library_matches_the_config_file_on_disk(library):
    on_disk = yaml.safe_load(open(Path(__file__).resolve().parents[1] / "config" / "regimes.yaml"))
    assert library["regimes"].keys() == on_disk["regimes"].keys()


@pytest.mark.parametrize("regime", yaml.safe_load(
    open(Path(__file__).resolve().parents[1] / "config" / "regimes.yaml"))["regimes"])
def test_every_regime_has_the_required_metadata(regime, library):
    block = get_regime(regime, library)
    for field in ("label", "policy_name", "intent", "cuts", "operations"):
        assert field in block, f"{regime}: missing {field!r}"
    assert block["intent"].strip(), f"{regime}: empty intent"


def test_cuts_flag_agrees_with_the_operation_list(names, library):
    """`cuts: false` must mean no operations, and vice versa — no silently inert regime."""
    for name in names:
        block = get_regime(name, library)
        assert bool(block["operations"]) == block["cuts"], (
            f"{name}: cuts={block['cuts']} but {len(block['operations'])} operations"
        )
    assert not cuts("no_management", library)


def test_operations_are_strictly_ordered_in_time(names, library):
    """Two cuts in one year would render as duplicate ThinDBH lines for the same cycle."""
    for name in names:
        offsets = [op["year_offset"] for op in get_regime(name, library)["operations"]]
        assert offsets == sorted(set(offsets)), f"{name}: operations not strictly ascending"


def test_operations_land_on_fvs_cycle_boundaries(names, library, projection_config):
    """FVS applies a keyword at a cycle boundary; an off-cycle year silently shifts."""
    cycle = projection_config["projection"]["cycle_years"]
    horizon = projection_config["projection"]["horizon_years"]
    constraints = load_library()["constraints"]
    assert constraints["offsets_must_be_multiples_of"] == cycle
    assert constraints["max_year_offset"] == horizon

    for name in names:
        for op in get_regime(name, library)["operations"]:
            off = op["year_offset"]
            assert off % cycle == 0, f"{name}: offset {off} is not a multiple of {cycle}"
            assert 0 < off <= horizon, f"{name}: offset {off} outside the {horizon}-yr horizon"


def test_proportions_and_dbh_windows_are_valid(names, library):
    for name in names:
        for op in get_regime(name, library)["operations"]:
            assert 0.0 < op["proportion"] <= 1.0, f"{name}: bad proportion {op['proportion']}"
            assert 0.0 <= op["min_dbh"] < op["max_dbh"], f"{name}: bad DBH window"


def test_regeneration_harvest_is_terminal(names, library):
    """Nothing can be scheduled after a clearcut — the stand is gone, and without a
    regeneration keyword (issue #17) FVS has no planted stand to cut next."""
    for name in names:
        ops = get_regime(name, library)["operations"]
        for i, op in enumerate(ops):
            if op["kind"] == "regeneration_harvest":
                assert op["proportion"] == 1.0, f"{name}: regeneration harvest must remove all"
                assert i == len(ops) - 1, f"{name}: operations scheduled after a clearcut"


def test_regimes_with_a_clearcut_declare_the_regeneration_gap(names, library):
    """A regime that clearcuts without replanting understates the next rotation. Say so."""
    for name in names:
        block = get_regime(name, library)
        if any(op["kind"] == "regeneration_harvest" for op in block["operations"]):
            assert "regeneration_gap" in block, (
                f"{name} clearcuts but does not document the missing PLANT/NATREGEN "
                f"keyword — see issue #18"
            )


def test_build_thins_resolves_offsets_to_absolute_years(library):
    thins = build_thins("pine_plantation_industrial", inv_year=INV_YEAR, library=library)
    assert [t.year for t in thins] == [INV_YEAR + 10, INV_YEAR + 25]
    assert thins[0].proportion == 0.40 and thins[0].max_dbh == 8.0
    assert thins[1].proportion == 1.0 and thins[1].max_dbh == 999.0


def test_build_thins_shifts_with_the_inventory_year(library):
    a = build_thins("nipf_light", inv_year=2022, library=library)
    b = build_thins("nipf_light", inv_year=2030, library=library)
    assert [t.year for t in b] == [t.year + 8 for t in a]


def test_no_management_builds_no_operations(library):
    assert build_thins("no_management", inv_year=INV_YEAR, library=library) == []


def test_unknown_regime_raises(library):
    with pytest.raises(ValueError, match="unknown regime"):
        build_thins("selective_wishful_thinking", library=library)


@pytest.mark.parametrize("regime", yaml.safe_load(
    open(Path(__file__).resolve().parents[1] / "config" / "regimes.yaml"))["regimes"])
def test_every_regime_renders_a_wellformed_keyfile(regime, library):
    key = render_keyfile("MU_1", "MU_1", regime, inv_year=INV_YEAR, library=library)
    assert "StdIdent" in key and "Process" in key
    assert f"InvYear   {INV_YEAR:>10d}" in key

    thin_lines = [ln for ln in key.splitlines() if ln.startswith("ThinDBH")]
    assert len(thin_lines) == len(get_regime(regime, library)["operations"])
    for line in thin_lines:
        # Fixed 10-column fields: keyword + 5 numeric fields.
        assert len(line) == 60, f"{regime}: ThinDBH line is {len(line)} chars, expected 60"
        assert len(line.split()) == 6


def test_rendered_years_are_inside_the_projection_window(names, library, projection_config):
    """A cut scheduled past the last cycle is silently never applied by FVS."""
    horizon = projection_config["projection"]["horizon_years"]
    for name in names:
        key = render_keyfile("MU_1", "MU_1", name, inv_year=INV_YEAR, library=library)
        for line in key.splitlines():
            if line.startswith("ThinDBH"):
                year = int(line.split()[1])
                assert INV_YEAR < year <= INV_YEAR + horizon, f"{name}: cut in {year}"


def test_owner_defaults_and_eligible_sets_resolve_to_real_regimes(management_regimes, names):
    """Every regime named in management_regimes.yaml must exist in the library."""
    for cls, block in management_regimes["owner_classes"].items():
        default = block["default"]
        defaults = (
            [b["regime"] for b in default["by_forest_type"].values()]
            if "by_forest_type" in default else [default["regime"]]
        )
        for regime in defaults:
            assert regime in names, f"{cls}: default regime {regime!r} not in the library"
            assert regime in block["eligible_regimes"], f"{cls}: default not eligible"
        for regime in block["eligible_regimes"]:
            assert regime in names, f"{cls}: eligible regime {regime!r} not in the library"


def test_every_owner_class_gets_a_distinct_default(management_regimes):
    """The point of this library: public classes no longer share one selection_harvest.

    Not every class needs a unique regime — `unknown` and `other` intentionally share the
    holding position — but the four public/conservation classes must differ from each
    other, which is what the old shared-parameter version could not express.
    """
    defaults = {}
    for cls, block in management_regimes["owner_classes"].items():
        d = block["default"]
        if "by_forest_type" not in d:
            defaults[cls] = d["regime"]
    public = {defaults[c] for c in ("federal", "state", "county", "ngo")}
    assert len(public) == 4, f"public classes still share regimes: {public}"


def test_riparian_override_regime_exists_and_does_not_cut(management_regimes, library):
    regime = management_regimes["riparian_override"]["regime"]
    assert regime in regime_names(library)
    assert not cuts(regime, library), "riparian must never be scheduled for entry"


def test_intensity_ordering_matches_stated_intent(library):
    """Sanity-check the regimes against the story the config tells about them.

    Corporate is the most aggressive, county/NGO among the lightest, and federal lighter
    than state. If someone retunes a number, this catches a change that inverts the
    intent without anyone noticing.
    """
    def removed(name):
        """Crude total intensity: sum of proportions removed over the horizon."""
        return sum(op["proportion"] for op in get_regime(name, library)["operations"])

    assert removed("pine_plantation_industrial") > removed("nipf_light")
    assert removed("public_active_thinning") > removed("public_uneven_aged"), \
        "state is meant to be more active than federal"
    assert removed("custodial_light") < removed("public_uneven_aged"), \
        "county custodial must stay lighter than the federal selection schedule"
    assert removed("no_management") == 0

    # Conservation restoration touches nothing large, whatever its total intensity.
    for op in get_regime("conservation_restoration", library)["operations"]:
        assert op["max_dbh"] <= 6.0, "restoration thinning must not remove large trees"
