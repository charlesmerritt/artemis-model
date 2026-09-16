"""Build the LETO segmentation attribute rasters for the AOI (LETO stage 1).

LETO's `01_build_segmentation_rasters.py` derives five 30 m attribute rasters
from the TreeMap raster's PLT_CN link into FIA: STDAGE, FORTYPCD, BALIVE, QMD
and TPA. The TreeMap 2022 release ships FORTYPCD, BALIVE, QMD and TPA_LIVE
directly in its raster attribute table (already the plot-level values LETO
computes), so those four come from the VAT; STDAGE is not in the VAT and is
read the way LETO reads it — from the FIA COND record of the plot's dominant
condition, here via the consolidated five-county FIA database.

Outputs (work/):
    attributes.npz   float32 arrays STDAGE/FORTYPCD/BALIVE/QMD/TPA aligned to
                     the AOI grid, plus the uint32 TM value raster
    vat_aoi.csv      the VAT rows for TM values present in the AOI
                     (TM value -> PLT_CN and attributes), the donor table for
                     stage 4's weighted FVS inputs
"""

from __future__ import annotations

import argparse
import sqlite3
import struct
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import FIA_DB, TREEMAP_NODATA, TREEMAP_VAT_DBF, region_paths

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from pipeline.ids import as_id_series  # noqa: E402

# PLT_CN is an FIA control number (an identifier, not a numeric attribute), so it is kept as
# exact text even though the DBF stores it as an "N" field. Coercing it through
# pd.to_numeric like the other numeric fields is the exact defect notes/identifier-precision.md
# documents: pd.to_numeric(..., errors="coerce") silently upcasts the *whole* column to
# float64 the moment any single row's PLT_CN is blank or malformed (routine for a nationwide
# VAT's non-forest/water sentinel rows), and float64 loses digits above 2**53 — a 16+ digit
# control number is truncated before this script ever sees a string.
_TEXT_FIELDS = {"PLT_CN"}


def read_vat(path: Path) -> pd.DataFrame:
    """Read the TreeMap VAT .dbf (numeric + character fields, dBase III layout)."""
    with open(path, "rb") as f:
        header = f.read(32)
        n_records, header_len, record_len = struct.unpack("<IHH", header[4:12])
        fields = []
        while True:
            fd = f.read(32)
            if fd[:1] == b"\r":
                break
            name = fd[:11].split(b"\x00")[0].decode()
            fields.append((name, fd[11:12].decode(), fd[16]))
        f.seek(header_len)
        raw = f.read(n_records * record_len)

    arr = np.frombuffer(raw, dtype="S1").reshape(n_records, record_len)
    out, pos = {}, 1  # first byte per record is the deletion flag
    for name, ftype, flen in fields:
        col = arr[:, pos:pos + flen].view(f"S{flen}").ravel()
        text = pd.Series(col).str.decode("ascii").str.strip()
        if ftype == "N" and name not in _TEXT_FIELDS:
            out[name] = pd.to_numeric(text, errors="coerce")
        else:
            out[name] = text
        pos += flen
    return pd.DataFrame(out)


def main() -> None:
    import rasterio

    parser = argparse.ArgumentParser()
    parser.add_argument("--region", choices=("aoi", "full"), default="aoi")
    args = parser.parse_args()
    paths = region_paths(args.region)

    with rasterio.open(paths.treemap_tif) as src:
        tm = src.read(1)
    valid = tm != TREEMAP_NODATA

    vat = read_vat(TREEMAP_VAT_DBF)
    print(f"VAT: {len(vat)} rows; {args.region} values: {np.unique(tm[valid]).size}")

    aoi_values = np.unique(tm[valid])
    vat_aoi = vat[vat["Value"].isin(aoi_values)].copy()
    vat_aoi["PLT_CN"] = as_id_series(vat_aoi["PLT_CN"], column="PLT_CN")

    # STDAGE per PLT_CN from FIA COND, dominant condition per LETO
    # (largest CONDPROP_UNADJ, live forest condition).
    con = sqlite3.connect(FIA_DB)
    cond = pd.read_sql(
        """
        SELECT CAST(PLT_CN AS TEXT) AS PLT_CN, CONDID, STDAGE, CONDPROP_UNADJ
        FROM COND WHERE COND_STATUS_CD = 1 AND STDAGE IS NOT NULL
        """,
        con,
    )
    con.close()
    cond = (cond.sort_values(["PLT_CN", "CONDPROP_UNADJ"], ascending=[True, False])
                .drop_duplicates("PLT_CN"))
    vat_aoi = vat_aoi.merge(cond[["PLT_CN", "STDAGE"]], on="PLT_CN", how="left")
    n_no_age = int(vat_aoi["STDAGE"].isna().sum())
    if n_no_age:
        # LETO reports rather than silently drops; a missing STDAGE segment
        # attribute becomes the landscape median so the CA cost stays defined.
        print(f"WARNING: {n_no_age} AOI plots without COND STDAGE; using median")
        vat_aoi["STDAGE"] = vat_aoi["STDAGE"].fillna(vat_aoi["STDAGE"].median())

    lookup_max = int(aoi_values.max())
    grids = {}
    for name, col in [("STDAGE", "STDAGE"), ("FORTYPCD", "FORTYPCD"),
                      ("BALIVE", "BALIVE"), ("QMD", "QMD"), ("TPA", "TPA_LIVE")]:
        lut = np.full(lookup_max + 1, np.nan, dtype=np.float64)
        lut[vat_aoi["Value"].astype(int).to_numpy()] = vat_aoi[col].to_numpy(dtype=np.float64)
        grid = np.full(tm.shape, np.nan, dtype=np.float32)
        grid[valid] = lut[tm[valid]].astype(np.float32)
        grids[name] = grid
        v = grid[valid]
        print(f"{name:9s} min {np.nanmin(v):8.1f} med {np.nanmedian(v):8.1f} max {np.nanmax(v):8.1f}")

    paths.attributes_npz.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(paths.attributes_npz, tm=tm, valid=valid, **grids)
    vat_aoi.to_csv(paths.vat_csv, index=False)
    print(f"wrote {paths.attributes_npz.name} and {paths.vat_csv.name} "
          f"({len(vat_aoi)} donor plots)")


if __name__ == "__main__":
    main()
