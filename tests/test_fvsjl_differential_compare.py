"""The FVSsn vs FVSjl differential: stand accounting and the Phase 0 gates."""

import pandas as pd

from research.fvsjl_engine_differential.compare import (
    Regime,
    compare_regime,
    gate_failures,
)


def summary(rows):
    return pd.DataFrame(rows, columns=["StandID", "Year", "Tpa", "BA", "SDI", "TCuFt", "RTCuFt"])


def row(stand, year, ba=100.0, tcuft=1000.0):
    return (stand, year, 300.0, ba, 200.0, tcuft, 900.0)


FULL = [2022, 2027, 2032]


def test_a_stand_that_crashes_mid_run_is_partial_and_left_out_of_the_final_year_totals():
    fortran = summary([row("a", y) for y in FULL] + [row("b", 2022, tcuft=50_000)])
    fvsjl = summary([row("a", y) for y in FULL] + [row("b", y, tcuft=50_000) for y in FULL])

    result = compare_regime(fortran, fvsjl, cns={"a", "b", "c"})

    assert result.missing_stands == 1  # c: no rows at all
    assert result.partial_stands == 1  # b: rows, but never reached 2032
    assert result.final_tcuft == (1000.0, 1000.0)  # a only; b's 2022 row is not its final year


def test_within_one_percent_shares_are_per_merged_row():
    fortran = summary([row("a", 2022, ba=100), row("a", 2027, ba=100)])
    fvsjl = summary([row("a", 2022, ba=100.5), row("a", 2027, ba=110)])

    assert compare_regime(fortran, fvsjl, cns={"a"}).within_1pct["BA"] == 0.5


def passing(**changes):
    result = compare_regime(summary([row("a", y) for y in FULL]),
                            summary([row("a", y) for y in FULL]), cns={"a"})
    return result._replace(**changes)


def test_matching_engines_pass_every_gate():
    assert gate_failures(Regime.THIN, passing()) == []


def test_fidelity_below_95_percent_fails_a_gated_regime_but_not_the_plantation():
    low = passing(within_1pct={"Tpa": 1.0, "BA": 0.908, "TCuFt": 0.912, "RTCuFt": 1.0})

    assert gate_failures(Regime.THIN, low) == ["BA within 1% 0.908 < 0.95",
                                               "TCuFt within 1% 0.912 < 0.95"]
    assert gate_failures(Regime.PLANT, low) == []


def test_final_year_totals_more_than_0_2_percent_apart_fail():
    assert gate_failures(Regime.NONE, passing(final_tcuft=(1000.0, 1003.0))) == [
        "final-year TCuFt differs by 0.30% > 0.2%"]


def test_any_ghost_volume_stand_fails_every_regime():
    assert gate_failures(Regime.PLANT, passing(ghost_stands=1)) == ["1 FVSjl ghost-volume stands"]
