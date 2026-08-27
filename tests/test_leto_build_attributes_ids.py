"""Regression test for the PLT_CN precision defect in the LETO experiment's VAT reader
(experiments/2026-08-24_leto-ca-forest-viz/02_build_attributes.py).

The risk: read_vat() decoded every DBF "N" field — PLT_CN included — through
pd.to_numeric(text, errors="coerce"). PLT_CN is an FIA control number (an identifier), not a
numeric attribute; the moment any row in the *nationwide* VAT has a blank or malformed
PLT_CN (routine for non-forest/water sentinel rows), pandas cannot hold that row as int64 (no
NaN representation) and silently upcasts the whole column to float64 — before the AOI subset
is even taken. A float64 carries only 15-17 significant digits, so any genuine 16+ digit
control number in that column loses digits right there, matching the exact defect class
documented in notes/identifier-precision.md and already fixed elsewhere in this repo
(pipeline/ids.py's as_id_series). PLT_CN must never round-trip through a float.
"""

import importlib.util
import struct
import sys
from pathlib import Path

import numpy as np
import pandas as pd

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

_EXPERIMENT_DIR = _ROOT / "experiments/2026-08-24_leto-ca-forest-viz"
sys.path.insert(0, str(_EXPERIMENT_DIR))
_spec = importlib.util.spec_from_file_location(
    "build_attributes", _EXPERIMENT_DIR / "02_build_attributes.py")
build_attributes = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module := build_attributes)


def _write_dbf(path: Path, fields: list[tuple[str, str, int]], rows: list[dict]) -> None:
    """Write a minimal dBase III .dbf satisfying read_vat()'s own (non-spec-complete) parser:
    a 32-byte header carrying (n_records, header_len, record_len) at bytes 4:12, one 32-byte
    field descriptor per field (name, type, ..., length at byte 16) terminated by 0x0D, then
    fixed-width ASCII records (a leading deletion-flag byte + each field left as given text,
    right-padded to its field width — DBF numeric fields are conventionally right-justified,
    but read_vat() only strips whitespace, so padding side doesn't matter for these tests)."""
    record_len = 1 + sum(flen for _, _, flen in fields)
    header_len = 32 + 32 * len(fields) + 1
    header = bytearray(32)
    struct.pack_into("<IHH", header, 4, len(rows), header_len, record_len)

    field_descs = bytearray()
    for name, ftype, flen in fields:
        fd = bytearray(32)
        name_bytes = name.encode("ascii")[:11]
        fd[0:len(name_bytes)] = name_bytes
        fd[11:12] = ftype.encode("ascii")
        fd[16] = flen
        field_descs += fd
    field_descs += b"\r"

    body = bytearray()
    for row in rows:
        body += b" "  # not deleted
        for name, _ftype, flen in fields:
            text = str(row.get(name, "")).encode("ascii")
            body += text.rjust(flen)[:flen] if len(text) <= flen else text[:flen]

    with open(path, "wb") as f:
        f.write(bytes(header))
        f.write(bytes(field_descs))
        f.write(bytes(body))


FIELDS = [("Value", "N", 6), ("PLT_CN", "N", 19), ("BALIVE", "N", 8)]


def test_read_vat_keeps_plt_cn_as_exact_text_even_with_a_blank_row(tmp_path):
    big_cn = "9876543210987654321"[:19]  # 19 digits, far above float64's 2**53 exact range
    rows = [
        {"Value": "1", "PLT_CN": big_cn, "BALIVE": "80.5"},
        {"Value": "2", "PLT_CN": "", "BALIVE": "0"},  # water/nonforest sentinel: blank PLT_CN
        {"Value": "3", "PLT_CN": "134323601010676", "BALIVE": "112.0"},
    ]
    dbf = tmp_path / "vat.dbf"
    _write_dbf(dbf, FIELDS, rows)

    vat = build_attributes.read_vat(dbf)

    # The historical bug: pd.to_numeric(..., errors="coerce") on the blank row forces the
    # whole PLT_CN column to float64, silently truncating the 19-digit control number.
    assert not pd.api.types.is_numeric_dtype(vat["PLT_CN"].dtype), (
        "PLT_CN must never be coerced to a numeric dtype — it is an identifier")
    assert vat.loc[0, "PLT_CN"] == big_cn, "19-digit control number lost digits"
    assert vat.loc[1, "PLT_CN"] == "", "blank sentinel row should stay blank text, not NaN"
    assert vat.loc[2, "PLT_CN"] == "134323601010676"

    # Sibling numeric attributes are unaffected by excluding PLT_CN from coercion.
    assert pd.api.types.is_numeric_dtype(vat["BALIVE"].dtype)
    assert pd.api.types.is_numeric_dtype(vat["Value"].dtype)


def test_as_id_series_preserves_plt_cn_through_the_aoi_filter(tmp_path):
    """End-to-end: read_vat() -> AOI filter -> the as_id_series call site in main() must
    round-trip a 19-digit PLT_CN exactly, unlike the old `.astype("int64").astype(str)`."""
    big_cn = "9876543210987654321"
    rows = [
        {"Value": "1", "PLT_CN": big_cn, "BALIVE": "80.5"},
        {"Value": "2", "PLT_CN": "", "BALIVE": "0"},
    ]
    dbf = tmp_path / "vat.dbf"
    _write_dbf(dbf, FIELDS, rows)

    vat = build_attributes.read_vat(dbf)
    aoi_values = np.array([1])
    vat_aoi = vat[vat["Value"].isin(aoi_values)].copy()

    from pipeline.ids import as_id_series
    vat_aoi["PLT_CN"] = as_id_series(vat_aoi["PLT_CN"], column="PLT_CN")
    assert str(vat_aoi.loc[0, "PLT_CN"]) == big_cn

    # Demonstrate the regression this guards against: the old call-site pattern silently
    # mangles the same value once the column has already been forced to float64 elsewhere.
    corrupted = pd.Series([float(big_cn)]).astype("int64").astype(str)
    assert corrupted.iloc[0] != big_cn, "sanity check: the old pattern is lossy by construction"
