"""Paint FVS output values onto TreeMap pixels.

Reclassifies the 5-county TreeMap raster: every TM_ID pixel is replaced by an
FVS-projected value for its stand, matched through

    raster TM_ID (== crosswalk ``Value``) -> PLT_CN -> fvs_trajectory.stand_cn

For a chosen metric (e.g. ``basal_area``) and calendar year, this writes one
GeoTIFF where each forested pixel carries that stand's projected value and
everything else is nodata.

Data version trap: the FVS run has 693 stands. The ``output/`` crosswalk also
has 693 rows (TreeMap 2022); the ``output2020/`` crosswalk has 688. The raster
and crosswalk must come from the same TreeMap vintage or pixels are mis-mapped,
so ``main()`` reports coverage for each candidate pairing and paints with the
one that actually matches the FVS stands.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import rasterio

REPO = Path(__file__).resolve().parents[2]
TREEMAP_CHAZ = Path("/mnt/d/TreeMap_Chaz")

FVS_TRAJECTORY = REPO / "data/interim/no_management_fl5co_fvs_output/fvs_trajectory.csv"
OUT_DIR = REPO / "data/processed/no_management_fl5co_rasters"

# Candidate (crosswalk, raster) pairings, by TreeMap vintage. Each crosswalk maps
# the raster pixel value (``Value`` == TM_ID) to a FIA plot control number (PLT_CN).
PAIRINGS = {
    "treemap2022": {
        "crosswalk": TREEMAP_CHAZ / "output/FL_5county_TreeMap_TMIDs.csv",
        "raster": TREEMAP_CHAZ / "FiveFloridaCounties/TreeMap2022_CONUS_5FlCntys.tif",
    },
    "treemap2020": {
        "crosswalk": TREEMAP_CHAZ / "output2020/FL_5county_TreeMap_TMIDs.csv",
        "raster": TREEMAP_CHAZ / "output2020/clipped_TreeMap_2020.tif",
    },
}

METRIC = "basal_area"
NODATA = -9999.0


def load_crosswalk(path: Path) -> pd.DataFrame:
    """TM_ID -> PLT_CN map. PLT_CN kept as string to preserve 15-digit precision."""
    df = pd.read_csv(path, usecols=["Value", "PLT_CN"], dtype={"PLT_CN": "string"})
    df = df.rename(columns={"Value": "tm_id"})
    df["tm_id"] = df["tm_id"].astype("int64")
    return df.dropna(subset=["PLT_CN"]).drop_duplicates("tm_id")


def load_trajectory() -> pd.DataFrame:
    """One row per stand x cycle. stand_cn kept as string (== PLT_CN)."""
    df = pd.read_csv(
        FVS_TRAJECTORY,
        dtype={"stand_cn": "string", "stand_id": "string"},
    )
    return df


def pixel_match_fraction(raster_path: Path, keys: np.ndarray) -> tuple[float, int, int]:
    """Fraction of valid raster pixels whose TM_ID is present in ``keys``."""
    with rasterio.open(raster_path) as src:
        band = src.read(1)
        nodata = src.nodata
    valid = np.ones(band.shape, dtype=bool) if nodata is None else band != nodata
    flat = band[valid].astype("int64")
    matched = np.isin(flat, keys)
    total = int(flat.size)
    n_match = int(matched.sum())
    return (n_match / total if total else 0.0), n_match, total


def report_pairing(name: str, crosswalk: pd.DataFrame, raster: Path,
                   fvs_stands: set[str]) -> float:
    """Print coverage for one pairing; return a combined coverage score."""
    xwalk_stands = set(crosswalk["PLT_CN"].dropna())
    stand_cov = len(fvs_stands & xwalk_stands) / len(fvs_stands)
    frac, n_match, total = pixel_match_fraction(raster, crosswalk["tm_id"].to_numpy())
    print(f"  [{name}]")
    print(f"    crosswalk rows={len(crosswalk)}  unique PLT_CN={len(xwalk_stands)}")
    print(f"    FVS stand_cn covered by crosswalk: {len(fvs_stands & xwalk_stands)}/{len(fvs_stands)} ({stand_cov:.1%})")
    print(f"    raster pixels matched to crosswalk TM_IDs: {n_match:,}/{total:,} ({frac:.1%})")
    return stand_cov * frac


def reclassify_by_key(band: np.ndarray, keys: np.ndarray, vals: np.ndarray,
                      nodata: float) -> np.ndarray:
    """Map each pixel's integer key to ``vals``; unmatched pixels become ``nodata``.

    ``keys`` must be sorted ascending and aligned 1:1 with ``vals``. Vectorized
    via ``searchsorted`` so it scales to tens of millions of pixels.
    """
    out = np.full(band.shape, nodata, dtype="float32")
    if keys.size == 0:
        return out
    flat = band.ravel().astype("int64")
    idx = np.clip(np.searchsorted(keys, flat), 0, keys.size - 1)
    hit = keys[idx] == flat
    flat_out = out.ravel()
    flat_out[hit] = vals[idx[hit]]
    return flat_out.reshape(band.shape)


def paint(metric: str, sel_col: str, sel_val: int, label: str,
          crosswalk: pd.DataFrame, traj: pd.DataFrame, raster_path: Path) -> Path:
    """Write one GeoTIFF: TM_ID pixels replaced by ``metric`` at a snapshot.

    The snapshot is the trajectory rows where ``sel_col == sel_val``. Two anchors
    are common to all 693 stands despite differing inventory start years:
    ``years_since_start == 0`` (initial condition) and ``calendar_year == 2076``
    (the shared projection end). Plain ``calendar_year`` mid-run is not, because
    stands start in different years.
    """
    snap = (
        traj.loc[traj[sel_col] == sel_val, ["stand_cn", metric]]
        .groupby("stand_cn", as_index=False)[metric].first()
    )
    # TM_ID -> PLT_CN -> metric
    merged = crosswalk.merge(
        snap.rename(columns={"stand_cn": "PLT_CN"}), on="PLT_CN", how="inner"
    )
    keys = merged["tm_id"].to_numpy()
    order = np.argsort(keys)
    keys = keys[order]
    vals = merged[metric].to_numpy(dtype="float32")[order]

    with rasterio.open(raster_path) as src:
        band = src.read(1)
        profile = src.profile.copy()
        rio_nodata = src.nodata

    out = reclassify_by_key(band, keys, vals, NODATA)

    n_painted = int((out != NODATA).sum())
    valid_pixels = int(band.size if rio_nodata is None else (band != rio_nodata).sum())

    profile.update(dtype="float32", count=1, nodata=NODATA, compress="lzw")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUT_DIR / f"{metric}_{label}.tif"
    with rasterio.open(out_path, "w", **profile) as dst:
        dst.write(out, 1)
        dst.set_band_description(1, f"FVS {metric} {label} (no management)")

    print(f"  wrote {out_path.name}: painted {n_painted:,} pixels "
          f"({n_painted / valid_pixels:.1%} of valid); "
          f"value range [{vals.min():.2f}, {vals.max():.2f}]")
    return out_path


def main() -> None:
    traj = load_trajectory()
    fvs_stands = set(traj["stand_cn"].dropna())
    print(f"FVS trajectory: {len(traj):,} rows, {len(fvs_stands)} stands, "
          f"calendar years {int(traj['calendar_year'].min())}-{int(traj['calendar_year'].max())}\n")

    print("Coverage by candidate pairing:")
    scored = {}
    crosswalks = {}
    for name, paths in PAIRINGS.items():
        if not paths["crosswalk"].exists() or not paths["raster"].exists():
            print(f"  [{name}] missing files, skipped")
            continue
        xwalk = load_crosswalk(paths["crosswalk"])
        crosswalks[name] = xwalk
        scored[name] = report_pairing(name, xwalk, paths["raster"], fvs_stands)

    best = max(scored, key=scored.get)
    print(f"\nSelected pairing: {best}\n")

    crosswalk = crosswalks[best]
    raster_path = PAIRINGS[best]["raster"]
    end_year = int(traj["calendar_year"].max())
    snapshots = [
        ("years_since_start", 0, "yr0_initial"),
        ("calendar_year", end_year, f"{end_year}_final"),
    ]
    print(f"Painting '{METRIC}' onto {raster_path.name}:")
    for sel_col, sel_val, label in snapshots:
        paint(METRIC, sel_col, sel_val, label, crosswalk, traj, raster_path)


if __name__ == "__main__":
    main()
