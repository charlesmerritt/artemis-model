"""Tests for weighted PLT_CN assignment (pipeline/s3_management/assign_plt_cn.py)."""

from pathlib import Path
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline.ids import IdPrecisionError
from pipeline.s3_management.assign_plt_cn import (
    NODATA_ID,
    build_weighted_plt_cn,
    load_tmid_plt_lookup,
    majority_plt_cn,
)

ND = NODATA_ID


def test_build_weighted_plt_cn_area_weights_within_unit():
    # MU 1 covers two TM10 pixels (->A) and one TM20 pixel (->B); MU 2 pixel is nodata.
    mu = np.array([[1, 1], [1, 2]])
    tm = np.array([[10, 20], [10, ND]])
    weights = build_weighted_plt_cn(mu, tm, {10: "A", 20: "B"})

    mu1 = weights[weights["MU_ID"] == "1"].set_index("PLT_CN")
    assert mu1.loc["A", "WEIGHT"] == pytest.approx(2 / 3)
    assert mu1.loc["B", "WEIGHT"] == pytest.approx(1 / 3)
    # Weights sum to 1 within the unit.
    assert weights.groupby("MU_ID")["WEIGHT"].sum().loc["1"] == pytest.approx(1.0)
    # MU 2 had only a nodata pixel -> absent.
    assert "2" not in set(weights["MU_ID"])


def test_build_weighted_plt_cn_drops_unmapped_values():
    mu = np.array([[1, 1]])
    tm = np.array([[10, 99]])  # 99 not in the lookup
    weights = build_weighted_plt_cn(mu, tm, {10: "A"})
    assert set(weights["PLT_CN"]) == {"A"}
    assert weights.iloc[0]["WEIGHT"] == pytest.approx(1.0)


def test_build_weighted_plt_cn_raises_without_overlap():
    mu = np.array([[ND, ND]])
    tm = np.array([[10, 20]])
    with pytest.raises(ValueError, match="overlapping"):
        build_weighted_plt_cn(mu, tm, {10: "A"})


def test_majority_plt_cn_picks_highest_cell_count():
    mu = np.array([[1, 1, 1], [2, ND, ND]])
    tm = np.array([[10, 10, 20], [30, ND, ND]])
    weights = build_weighted_plt_cn(mu, tm, {10: "A", 20: "B", 30: "C"})
    maj = majority_plt_cn(weights).set_index("MU_ID")
    assert maj.loc["1", "PLT_CN"] == "A"   # 2 pixels vs 1
    assert maj.loc["2", "PLT_CN"] == "C"


def test_load_tmid_plt_lookup_reads_value_and_plt(tmp_path):
    csv = tmp_path / "lookup.csv"
    pd.DataFrame({"Value": [2623, 10], "PLT_CN": ["17498047010478", "42"]}).to_csv(csv, index=False)
    lut = load_tmid_plt_lookup(csv)
    assert lut[2623] == "17498047010478"
    assert lut[10] == "42"


def test_load_tmid_plt_lookup_validates_columns(tmp_path):
    csv = tmp_path / "bad.csv"
    pd.DataFrame({"foo": [1], "bar": [2]}).to_csv(csv, index=False)
    with pytest.raises(ValueError, match="Value"):
        load_tmid_plt_lookup(csv)


def test_load_tmid_plt_lookup_keeps_a_19_digit_control_number_exact(tmp_path):
    # A control number wider than 2**53. The old int(float(...)) path lost the last
    # digits here; a truncated PLT_CN silently joins to the wrong FIA plot.
    wide = "1234567890123456789"
    csv = tmp_path / "lookup.csv"
    csv.write_text(f"Value,PLT_CN\n2623,{wide}\n")
    assert load_tmid_plt_lookup(csv)[2623] == wide


def test_load_tmid_plt_lookup_repairs_r_scientific_notation(tmp_path):
    # What r/02 wrote while it still cast PLT_CN through as.numeric().
    csv = tmp_path / "lookup.csv"
    csv.write_text("Value,PLT_CN\n2623,1.7498047010478e+13\n")
    assert load_tmid_plt_lookup(csv)[2623] == "17498047010478"


def test_load_tmid_plt_lookup_rejects_a_truncated_control_number(tmp_path):
    # 1.234567890123457e+18 has already lost its low digits; repairing it would
    # invent a plot ID, so this must fail rather than guess.
    csv = tmp_path / "lookup.csv"
    csv.write_text("Value,PLT_CN\n2623,1.234567890123457e+18\n")
    with pytest.raises(IdPrecisionError, match="lost digits"):
        load_tmid_plt_lookup(csv)


def test_weighted_table_emits_plt_cn_as_exact_digits():
    wide = "1234567890123456789"
    weights = build_weighted_plt_cn(np.array([[1, 1]]), np.array([[10, 20]]),
                                    {10: wide, 20: "17498047010478"})
    assert set(weights["PLT_CN"]) == {wide, "17498047010478"}
    # MU_ID leaves as "1", never "1.0" — the FVS-input builder joins on this string.
    assert set(weights["MU_ID"]) == {"1"}
