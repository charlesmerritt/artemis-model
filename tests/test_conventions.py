"""The convention checks catch what they claim to, and nothing they should not.

The first file of the rebuilt suite. Tests come back incrementally, one behaviour at a time;
pytest also runs every doctest under ``pipeline/`` (pyproject.toml), and coverage from both
feeds the CRAP and mutation reports in ``scripts/quality.py``.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from check_conventions import hardcoded_crs, lossy_id_casts  # noqa: E402


@pytest.mark.parametrize("source", [
    'df["PLT_CN"] = df["PLT_CN"].astype(str)',
    'df["stand_cn"].astype("str")',
    'key = str(int(rec.PLT_CN))',
    'units["MU_ID"].astype(str)',
    'source[stand_key].astype(str)',
])
def test_lossy_id_casts_are_caught(source):
    assert lossy_id_casts(source) == [1]


@pytest.mark.parametrize("source", [
    'df["PLT_CN"] = as_id_series(df["PLT_CN"], column="PLT_CN")',
    'df["BALIVE"].astype(str)',          # not an identifier
    'str(int(row.year))',
    '"never call .astype(str) on PLT_CN"',  # prose in a string is not a call
])
def test_legitimate_code_is_not_flagged(source):
    assert lossy_id_casts(source) == []


def test_crs_literal_is_caught_but_docstring_mention_is_not():
    source = '"""Everything is in EPSG:5070."""\nCRS = "EPSG:5070"\n'
    assert hardcoded_crs(source) == [2]
