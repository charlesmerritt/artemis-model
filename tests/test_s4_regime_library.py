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


TERMINAL_KINDS = ("regeneration_harvest", "retention_harvest")


def test_regeneration_and_retention_harvests_are_terminal(names, library):
    """Nothing can be scheduled after a stand-replacing entry.

    Without a regeneration keyword (issue #17) FVS has no planted cohort to cut next, so
    a later operation would be applied to whatever grew back by default — meaningless.
    Retention harvests count: the residual is a seed source, not a merchantable stand.
    """
    for name in names:
        ops = get_regime(name, library)["operations"]
        for i, op in enumerate(ops):
            if op["kind"] in TERMINAL_KINDS:
                assert i == len(ops) - 1, (
                    f"{name}: operations scheduled after a {op['kind']}"
                )


def test_harvest_kinds_have_the_proportion_their_name_implies(names, library):
    """A clearcut removes everything; a retention harvest must actually retain."""
    for name in names:
        for op in get_regime(name, library)["operations"]:
            if op["kind"] == "regeneration_harvest":
                assert op["proportion"] == 1.0, (
                    f"{name}: regeneration_harvest removes {op['proportion']} — "
                    f"if it retains trees it should be kind: retention_harvest"
                )
            elif op["kind"] == "retention_harvest":
                assert 0.5 <= op["proportion"] < 1.0, (
                    f"{name}: retention_harvest proportion {op['proportion']} is not a "
                    f"regeneration harvest with a residual"
                )
                assert op["max_dbh"] >= 999.0, (
                    f"{name}: a retention harvest spans the full diameter range; a "
                    f"bounded window is a thin, not a regeneration entry"
                )


def test_retention_pct_matches_the_rendered_proportion(names, library):
    """`retention_pct: 15` and `proportion: 0.85` must not drift apart."""
    for name in names:
        block = get_regime(name, library)
        if "retention_pct" not in block:
            continue
        harvests = [op for op in block["operations"] if op["kind"] == "retention_harvest"]
        assert harvests, f"{name}: declares retention_pct but has no retention harvest"
        for op in harvests:
            expected = round(1.0 - block["retention_pct"] / 100.0, 4)
            assert op["proportion"] == expected, (
                f"{name}: retention_pct {block['retention_pct']}% implies proportion "
                f"{expected}, got {op['proportion']}"
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


def test_the_two_regime_libraries_are_independent(management_regimes, names):
    """This library is used by config/scenarios.yaml, not by management_regimes.yaml.

    There are two prescription libraries in the repo and that is a known, deliberate
    state, not an accident to be papered over:

      - `config/regimes.yaml` (this one) declares operations as data and is what the
        Diaz-style scenario factorial in `config/scenarios.yaml` resolves against.
      - `config/management_regimes.yaml` carries its own `prescriptions` block, rendered
        through the Python builders in `pipeline/s4_fvs/regime_templates.py`, and is what
        `regime_assignment.py` assigns from today.

    They overlap in intent and do not share names. Reconciling them is an open decision.
    This test pins the split so neither library silently starts depending on the other —
    the failure mode that would make the split expensive instead of merely untidy.
    """
    prescriptions = set(management_regimes["prescriptions"])
    assert "regime_library" not in management_regimes, (
        "management_regimes.yaml now points at an external library; if that is intended, "
        "this split is being resolved and these tests need rewriting"
    )
    shared = prescriptions & set(names)
    assert shared == {"no_management"}, (
        f"the two libraries share regime names besides no_management: {sorted(shared)}. "
        "Either they are being merged (good — rewrite this test) or a name collided by "
        "accident (bad — one of them means something different now)."
    )
    for cls, block in management_regimes["owner_classes"].items():
        for regime in block["eligible"]:
            assert regime in prescriptions, (
                f"{cls}: eligible prescription {regime!r} is not in management_regimes.yaml"
            )


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
