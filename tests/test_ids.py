"""Tests for exact identifier handling (pipeline/ids.py).

These guard the join keys the whole projection hangs on: PLT_CN, STAND_CN, TM_ID, MU_ID.
The failure they exist to catch is silent — a control number that has been through a float
still looks like a control number, so the join just returns fewer rows.
"""

from pathlib import Path
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline.ids import (
    MAX_EXACT_FLOAT_INT,
    IdPrecisionError,
    as_id_series,
    exact_int_limit,
    normalize_id,
    read_id_csv,
    report_key_overlap,
)

# A real-shaped 15-digit FIA control number, and one wide enough to break a double.
CN_15 = "236048879010661"
CN_19 = "1234567890123456789"


def test_digit_strings_pass_through_unchanged():
    s = as_id_series(pd.Series([CN_15, "17498047010478"]), column="PLT_CN")
    assert s.tolist() == [CN_15, "17498047010478"]


def test_leading_zeros_are_preserved():
    # FVS STAND_ID values are zero-padded; stripping the padding breaks the join.
    assert as_id_series(pd.Series(["010006100083"]), column="STAND_ID").tolist() == ["010006100083"]


def test_integer_column_becomes_exact_digits():
    s = as_id_series(pd.Series([1, 2], dtype="int64"), column="MU_ID")
    assert s.tolist() == ["1", "2"]


def test_float_mu_id_does_not_become_dot_zero():
    # The GeoPackage-with-a-NULL case: MU_ID comes back float64, and .astype(str)
    # would produce "1.0" against a weights table holding "1".
    assert pd.Series([1.0, 2.0]).astype(str).tolist() == ["1.0", "2.0"]  # the bug
    assert as_id_series(pd.Series([1.0, 2.0]), column="MU_ID").tolist() == ["1", "2"]


def test_scientific_notation_from_r_is_repaired():
    # R's write.csv renders a double-typed control number this way.
    s = as_id_series(pd.Series(["1.7498047010478e+13"]), column="PLT_CN")
    assert s.tolist() == ["17498047010478"]


def test_pandas_style_float_string_is_repaired():
    s = as_id_series(pd.Series([f"{CN_15}.0"]), column="PLT_CN")
    assert s.tolist() == [CN_15]


def test_value_too_wide_for_a_double_is_rejected_not_repaired():
    # int(float("1234567890123456789")) == 1234567890123456768: the digits are gone,
    # so passing it off as a key would mis-assign a stand.
    assert int(float(CN_19)) != int(CN_19)
    with pytest.raises(IdPrecisionError, match="lost digits"):
        as_id_series(pd.Series([float(CN_19)]), column="PLT_CN")
    with pytest.raises(IdPrecisionError, match="lost digits"):
        as_id_series(pd.Series(["1.234567890123457e+18"]), column="PLT_CN")


def test_exact_int_limit_follows_the_dtype():
    assert exact_int_limit(np.dtype("float64")) == 2**53 == MAX_EXACT_FLOAT_INT
    assert exact_int_limit(np.dtype("float32")) == 2**24
    assert exact_int_limit(np.dtype("float16")) == 2**11
    assert exact_int_limit(pd.Float32Dtype()) == 2**24     # pandas extension dtype
    assert exact_int_limit(object) == MAX_EXACT_FLOAT_INT  # unrecognised -> widest bound


def test_float32_rounds_an_ordinary_control_number_and_is_rejected():
    # The 2**53 bound belongs to float64. A float32 gives out at 2**24, so a perfectly
    # ordinary 15-digit CN is already rounded while still looking small enough to trust:
    # checking it against 2**53 would emit the corrupted value as an exact-looking key.
    assert int(np.float32(int(CN_15))) != int(CN_15)
    assert abs(float(np.float32(int(CN_15)))) < MAX_EXACT_FLOAT_INT  # slips past the wide bound
    with pytest.raises(IdPrecisionError, match="float32"):
        as_id_series(pd.Series([int(CN_15)], dtype="float32"), column="PLT_CN")


def test_float32_still_accepts_ids_it_can_represent_exactly():
    # Not a blanket rejection of narrow floats: small MU_IDs are exact in float32.
    assert as_id_series(pd.Series([1, 2], dtype="float32"), column="MU_ID").tolist() == ["1", "2"]


def test_float16_bound_is_narrower_still():
    assert as_id_series(pd.Series([1024], dtype="float16"), column="MU_ID").tolist() == ["1024"]
    # 3000 is above the float16 exact-integer limit of 2**11 but still inside its range,
    # so this exercises the precision bound rather than an overflow to inf.
    with pytest.raises(IdPrecisionError, match="float16"):
        as_id_series(pd.Series([3000], dtype="float16"), column="MU_ID")


def test_wide_control_number_survives_as_a_string():
    # The same 19-digit CN is fine as long as it never touched a float.
    assert as_id_series(pd.Series([CN_19], dtype="string"), column="PLT_CN").tolist() == [CN_19]
    assert int(CN_19) > MAX_EXACT_FLOAT_INT


def test_non_numeric_string_ids_pass_through():
    # Management-unit ids from sketch_management_units are labels, not numbers. They
    # cannot have been through a float, so there is nothing to repair or reject.
    ids = ["mu_12125_00000001", "mu_12125_00000002"]
    assert as_id_series(pd.Series(ids), column="unit_id").tolist() == ids


def test_non_integer_numbers_are_rejected():
    # A fractional value is not an identifier; accepting it would mean inventing a key.
    with pytest.raises(IdPrecisionError, match="whole number"):
        as_id_series(pd.Series([1.5]), column="PLT_CN")
    with pytest.raises(IdPrecisionError, match="whole number"):
        as_id_series(pd.Series(["236048879010661.5"]), column="PLT_CN")


def test_numpy_scalars_in_an_object_column_are_handled_by_type():
    # An object-dtype column reaches the per-value loop, where numpy scalars arrive. They
    # do not subclass Python int/float, so the dispatch must match them explicitly rather
    # than rely on `.is_integer()` existing (CPython 3.12 / NumPy 1.25 and up).
    mixed = pd.Series([np.int64(1), np.int32(2), 3, "4.0"], dtype=object)
    assert as_id_series(mixed, column="MU_ID").tolist() == ["1", "2", "3", "4"]
    wide = pd.Series([np.int64(int(CN_19))], dtype=object)
    assert as_id_series(wide, column="PLT_CN").tolist() == [CN_19]  # int64 is exact, keep it


def test_booleans_are_not_identifiers():
    # Guards the scalar path. (An object-dtype *column* is stringified by the text fast
    # path before dispatch, so a bool there becomes the literal "True" and passes through
    # as a non-numeric label — an edge case with no real analogue in this data.)
    for value in (True, np.bool_(True)):
        with pytest.raises(IdPrecisionError):
            normalize_id(value, column="MU_ID")


def test_missing_values_pass_through_as_na():
    s = as_id_series(pd.Series([CN_15, None, np.nan]), column="PLT_CN")
    assert s.iloc[0] == CN_15
    assert s.isna().sum() == 2


def test_error_names_the_column_and_the_remedy():
    with pytest.raises(IdPrecisionError) as exc:
        normalize_id(float(CN_19), column="STAND_CN")
    message = str(exc.value)
    assert "STAND_CN" in message
    assert "strings" in message


def test_read_id_csv_pins_id_columns_to_text(tmp_path):
    csv = tmp_path / "lookup.csv"
    csv.write_text(f"Value,PLT_CN,BALIVE\n2623,{CN_15},80.5\n10,17498047010478,42.0\n")
    df = read_id_csv(csv)
    assert df["PLT_CN"].tolist() == [CN_15, "17498047010478"]
    assert df["BALIVE"].tolist() == [80.5, 42.0]  # non-ID columns still numeric


def test_read_id_csv_repairs_a_lookup_written_from_a_double(tmp_path):
    # Exactly the shape r/02 produced before the as.numeric() there was removed.
    csv = tmp_path / "lookup.csv"
    csv.write_text("Value,PLT_CN\n2623,1.7498047010478e+13\n")
    assert read_id_csv(csv)["PLT_CN"].tolist() == ["17498047010478"]


def test_report_key_overlap_counts_shared_keys():
    n_left, n_right, shared = report_key_overlap(
        pd.Series(["1", "2"]), pd.Series(["2", "3"]), left_name="a", right_name="b"
    )
    assert (n_left, n_right, shared) == (2, 2, 1)


def test_report_key_overlap_warns_when_nothing_matches(caplog):
    with caplog.at_level("WARNING"):
        report_key_overlap(pd.Series(["1", "2"]), pd.Series(["1.0", "2.0"]),
                           left_name="units MU_ID", right_name="weights MU_ID")
    assert "no units MU_ID key matched" in caplog.text
