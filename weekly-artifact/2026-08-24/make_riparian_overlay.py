"""
Join the Florida BMP riparian layer to the pilot landscape and rebuild the trajectory
library with the riparian override live.

Every weekly artifact since 2026-08-10 has carried the same caveat:

    "No riparian exclusion yet. `SMZ_Pct` is 0 for every unit because no buffer layer is
     joined, so the absolute no-entry riparian rule in `regime_assignment` never fires."

`notes/methodology-directions.md` item 2 is that outstanding work, and
`config/management_regimes.yaml` declares the rule as executable policy —
`overrides.riparian` with `field: SMZ_Pct`, `min_value: 50.0`, `absolute: true`. The rule
has never been evaluated against real geometry. This driver evaluates it: it builds the
Florida BMP stream buffers from NHD flowlines, rasterises them onto the TreeMap grid,
measures every scheduling unit's share inside a stream-management zone, and re-enumerates
the decision space with that share in place of the zero.

Every modelling decision belongs to committed repository code —

  * `pipeline.s3_management.sketch_management_units` : `classify_stream_fcode` (NHD FCode
        -> BMP buffer class), `build_riparian_buffer_layer` (the disjoint, priority-ordered
        buffer layer), `feet_to_meters`, and the `config/bmp_rules.yaml` loader
  * `pipeline.s3_management.regime_assignment`       : `_is_riparian` (the override test),
        `eligible_prescriptions`, `forest_type_branch`
  * `pipeline.s3_management.owner_classes`           : Harris class -> ARTEMIS owner class
  * `pipeline.s4_fvs.paint_fvs_to_raster`            : the TM_ID -> PLT_CN crosswalk loader

...and buffer widths come from `config/bmp_rules.yaml` (Florida Forest Service BMP manual
2020: 35 ft ephemeral/intermittent, 50 ft perennial small, 75 ft perennial large), while
the threshold and the override prescription come from `config/management_regimes.yaml`.
The landscape attribution reproduces `weekly-artifact/2026-08-10/make_schedule.py`, and
the library enumeration reuses `weekly-artifact/2026-08-17/make_trajectory_library.py`.

Two mechanics are this driver's own, and both are reported rather than hidden:

1. **Two unit readings, because the answer differs.** The scheduling unit in use today is
   `TreeMap plot x county x ownership class` — an attribute class, not a contiguous
   polygon, so its SMZ share is a landscape-wide average and almost nothing crosses a 50%
   threshold. That is reported as scenario `unit_mean`. The geometric reading — the one
   the Phase 2.3 polygon units will produce — splits every unit at the buffer edge into
   its in-SMZ and out-of-SMZ parts, which makes the in-SMZ part 100% riparian by
   construction. That is scenario `smz_split`. The first says whether today's units trip
   the rule; the second says what the rule actually costs the decision space.

2. **Riparian units get a one-item menu.** `eligible_prescriptions` does not take
   geometry, so the driver applies the override the way `assign_prescription` does: a unit
   that trips `_is_riparian` is offered exactly the prescription the override names
   (`no_management`), which is what the function's own docstring says riparian units get.
   The test and the prescription both come from the config, not from this file.

Usage (from the repo root, with the R2 inputs staged under ./data — see the artifact
README for the exact keys):

    uv run python weekly-artifact/2026-08-24/make_riparian_overlay.py
"""

from __future__ import annotations

import importlib.util
import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from pipeline.s3_management.owner_classes import MASKED, classify_owner  # noqa: E402
from pipeline.s3_management.regime_assignment import (  # noqa: E402
    _is_riparian,
    _riparian_override,
    eligible_prescriptions,
    forest_type_branch,
    load_regimes_config,
)
from pipeline.s3_management.sketch_management_units import (  # noqa: E402
    RIPARIAN_BUFFER_PRIORITY,
    SQ_M_PER_ACRE,
    build_riparian_buffer_layer,
    classify_stream_fcode,
    feet_to_meters,
    load_config as load_bmp_rules,
)
from pipeline.s4_fvs.paint_fvs_to_raster import load_crosswalk  # noqa: E402
from pipeline.spatial_ref import project_crs  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
log = logging.getLogger("make_riparian_overlay")

OUT_DIR = Path(__file__).resolve().parent
DATA = REPO / "data"

NHD_GDB = DATA / "interim/nhd/nhdplus_epasnapshot2022_fl.gdb"
NHD_FLOWLINE_LAYER = "nhdflowline_fl"
SMZ_CACHE = DATA / "interim/smz_pixels_attributed.csv"
SMZ_LAYER_CACHE = DATA / "interim/smz_buffers_5070.gpkg"     # gitignored; map input
STREAM_LAYER_CACHE = DATA / "interim/smz_streams_5070.gpkg"  # gitignored; map input

FLORIDA_FIPS = "12"
PROJECT_CRS = project_crs()

# The published landscape totals from the 2026-08-10 and 2026-08-17 artifacts. The
# attribution below adds one dimension (in/out of SMZ) to the same pixel pass, so
# collapsing that dimension has to reproduce these exactly or something has moved.
PRIOR_UNITS = 5240
PRIOR_STANDS = 676
PRIOR_ACRES = 925_098


def load_driver(relpath: str, name: str):
    """Import a prior artifact driver by path — dated directory names are not identifiers."""
    path = REPO / relpath
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PRIOR_10 = load_driver("weekly-artifact/2026-08-10/make_schedule.py", "make_schedule_20260810")
PRIOR_17 = load_driver("weekly-artifact/2026-08-17/make_trajectory_library.py", "make_lib_20260817")


# --------------------------------------------------------------------------------------
# Stage A — the BMP stream-management zone, from NHD flowlines and config/bmp_rules.yaml
# --------------------------------------------------------------------------------------

def aoi_counties():
    """The five pilot counties as a GeoDataFrame in the project CRS."""
    import geopandas as gpd

    counties = gpd.read_file(PRIOR_10.COUNTIES_SHP)
    counties = counties[
        (counties["STATE"] == "FL") & (counties["NAME"].isin(PRIOR_10.PILOT_COUNTIES))
    ]
    if len(counties) != len(PRIOR_10.PILOT_COUNTIES):
        raise RuntimeError(f"expected {len(PRIOR_10.PILOT_COUNTIES)} pilot counties, got {len(counties)}")
    return counties.to_crs(PROJECT_CRS)


def build_smz_layer():
    """Florida BMP stream buffers over the pilot AOI, as the repo's own builder returns them.

    Returns ``(buffers, streams)`` — the disjoint per-class buffer layer, and the clipped
    flowlines that produced it (kept for the map and for the per-class stream mileage).
    """
    import geopandas as gpd

    counties = aoi_counties()
    aoi = counties.dissolve()[["geometry"]]

    rules = load_bmp_rules(REPO / "config/bmp_rules.yaml")
    fl = rules["states"][FLORIDA_FIPS]
    widths_ft = {cls: fl["buffers"][cls]["width_ft"] for cls in RIPARIAN_BUFFER_PRIORITY}
    buffer_widths = {cls: feet_to_meters(ft) for cls, ft in widths_ft.items()}
    log.info("BMP buffer widths (%s): %s", fl["name"],
             ", ".join(f"{c}={widths_ft[c]} ft / {buffer_widths[c]:.1f} m" for c in widths_ft))

    log.info("Reading NHD flowlines over the AOI from %s", NHD_GDB)
    streams = gpd.read_file(NHD_GDB, layer=NHD_FLOWLINE_LAYER, mask=aoi.to_crs("EPSG:4269"))
    streams = streams.to_crs(PROJECT_CRS)
    streams = gpd.clip(streams, aoi)
    streams = streams[~streams.geometry.is_empty & streams.geometry.notna()].copy()
    streams["buffer_class"] = streams["fcode"].apply(classify_stream_fcode)
    streams["length_km"] = streams.geometry.length / 1000.0
    log.info("Flowlines in AOI: %d (%.0f km); by BMP class:\n%s", len(streams),
             streams["length_km"].sum(),
             streams.groupby(streams["buffer_class"].fillna("unbuffered (other FCode)"))
                    .agg(features=("fcode", "size"), km=("length_km", "sum")).to_string())

    buffers = build_riparian_buffer_layer(streams, buffer_widths, crs=PROJECT_CRS)
    buffers["buffer_ac"] = buffers.geometry.area / SQ_M_PER_ACRE
    log.info("Buffer layer: %s", buffers[["buffer_class", "buffer_ac"]].to_string(index=False))

    SMZ_LAYER_CACHE.parent.mkdir(parents=True, exist_ok=True)
    buffers.to_file(SMZ_LAYER_CACHE, driver="GPKG")
    streams[["fcode", "buffer_class", "length_km", "geometry"]].to_file(
        STREAM_LAYER_CACHE, driver="GPKG")
    return buffers, streams


# --------------------------------------------------------------------------------------
# Stage B — attribute the landscape, now with the SMZ dimension
# --------------------------------------------------------------------------------------

def attribute_pixels_with_smz() -> pd.DataFrame:
    """Pixel counts over (tm_id, county, owner_class, buffer_class) on the TreeMap grid.

    This is `weekly-artifact/2026-08-10/make_schedule.py::attribute_pixels` with one extra
    grid: the rasterised BMP buffer layer. `buffer_class` is `""` outside every buffer.
    A pixel is inside a buffer when its centre is (rasterio's default), which is the
    30 m grid's honest reading of a 10.7-23 m wide strip.
    """
    import rasterio
    from rasterio.enums import Resampling
    from rasterio.features import rasterize
    from rasterio.vrt import WarpedVRT

    with rasterio.open(PRIOR_10.TREEMAP_TIF) as src:
        tm = src.read(1)
        profile = src.profile
        nodata = src.nodata
    log.info("TreeMap grid %s, nodata=%s, crs=%s", tm.shape, nodata, profile["crs"])

    counties = aoi_counties().to_crs(profile["crs"])
    county_codes = {name: i + 1 for i, name in enumerate(sorted(counties["NAME"]))}
    county_grid = rasterize(
        ((geom, county_codes[name]) for geom, name in zip(counties.geometry, counties["NAME"])),
        out_shape=tm.shape, transform=profile["transform"], fill=0, dtype="uint8",
    )
    log.info("Rasterized counties: %s", county_codes)

    buffers, _ = build_smz_layer()
    buffers = buffers.to_crs(profile["crs"])
    smz_codes = {cls: i + 1 for i, cls in enumerate(RIPARIAN_BUFFER_PRIORITY)}
    smz_grid = rasterize(
        ((geom, smz_codes[cls]) for geom, cls in zip(buffers.geometry, buffers["buffer_class"])),
        out_shape=tm.shape, transform=profile["transform"], fill=0, dtype="uint8",
    )
    log.info("SMZ pixels on the TreeMap grid: %d of %d", int((smz_grid > 0).sum()), smz_grid.size)

    PRIOR_10._r2_gdal_env()
    log.info("Warping the Harris 2025 ownership raster onto the TreeMap grid (windowed read from R2)")
    with rasterio.open(PRIOR_10.OWNERSHIP_VSI) as osrc:
        with WarpedVRT(
            osrc, crs=profile["crs"], transform=profile["transform"],
            width=profile["width"], height=profile["height"],
            resampling=Resampling.nearest,
        ) as vrt:
            owner_grid = vrt.read(1)
    log.info("Ownership classes present: %s", np.unique(owner_grid).tolist())

    valid = (tm != nodata) & (county_grid > 0)
    df = pd.DataFrame({
        "tm_id": tm[valid].astype("int64"),
        "county_code": county_grid[valid],
        "owner_class": owner_grid[valid],
        "smz_code": smz_grid[valid],
    })
    counts = df.value_counts().rename("pixel_count").reset_index()
    counts["county"] = counts["county_code"].map({v: k for k, v in county_codes.items()})
    counts["buffer_class"] = counts["smz_code"].map({v: k for k, v in smz_codes.items()}).fillna("")
    return counts.drop(columns=["county_code", "smz_code"])


def pixel_table() -> pd.DataFrame:
    """The SMZ-aware pixel attribution, cached under gitignored data/interim."""
    if SMZ_CACHE.exists():
        log.info("Reusing cached SMZ attribution %s", SMZ_CACHE)
        return pd.read_csv(SMZ_CACHE, keep_default_na=False, na_values=[])
    counts = attribute_pixels_with_smz()
    SMZ_CACHE.parent.mkdir(parents=True, exist_ok=True)
    counts.to_csv(SMZ_CACHE, index=False)
    log.info("Wrote SMZ attribution cache %s", SMZ_CACHE)
    return counts


def build_units_with_smz() -> tuple[pd.DataFrame, pd.DataFrame]:
    """Scheduling units with a measured SMZ share.

    Returns ``(units, pieces)``:
      * ``units``  — one row per `TM_ID x county x owner class`, as the 2026-08-10 driver
        defines it, plus `smz_acres` and `SMZ_Pct`.
      * ``pieces`` — the same landscape split at the buffer edge: one row per
        `unit x buffer_class` (buffer_class `""` = outside every buffer). This is the
        `smz_split` reading, and its in-SMZ rows carry `SMZ_Pct = 100`.
    """
    counts = pixel_table()

    xwalk = load_crosswalk(PRIOR_10.CROSSWALK)
    fortyp = (
        pd.read_csv(PRIOR_10.CROSSWALK, usecols=["Value", "FORTYPCD", "ForTypName"])
        .rename(columns={"Value": "tm_id"})
        .drop_duplicates("tm_id")
    )
    pieces = counts.merge(xwalk, on="tm_id", how="inner").merge(fortyp, on="tm_id", how="left")

    pieces["acres"] = pieces["pixel_count"] * PRIOR_10.ACRES_PER_PIXEL
    pieces["owner_name"] = pieces["owner_class"].map(
        lambda c: PRIOR_10.OWNER_CLASSES.get(c, (None, None))[0])
    pieces["owner_group"] = pieces["owner_class"].map(
        lambda c: PRIOR_10.OWNER_CLASSES.get(c, (None, None))[1])
    pieces["OWN_CODE"] = pieces["owner_class"]
    dropped = pieces["owner_group"].isna()
    log.info("Dropping %d pieces (%.0f ac) on unknown/non-forest/water ownership classes",
             int(dropped.sum()), pieces.loc[dropped, "acres"].sum())
    pieces = pieces[~dropped].copy()
    pieces["unit_id"] = ("TM" + pieces["tm_id"].astype(str) + "_" + pieces["county"]
                         + "_O" + pieces["owner_class"].astype(str))
    pieces["in_smz"] = pieces["buffer_class"] != ""

    keys = ["unit_id", "tm_id", "county", "owner_class", "owner_name", "owner_group",
            "OWN_CODE", "PLT_CN", "FORTYPCD", "ForTypName"]
    units = (
        pieces.groupby(keys, as_index=False, dropna=False)
        .agg(pixel_count=("pixel_count", "sum"), acres=("acres", "sum"),
             smz_pixels=("pixel_count", lambda s: 0), smz_acres=("acres", lambda s: 0.0))
    )
    smz = (pieces[pieces["in_smz"]].groupby("unit_id", as_index=False)
           .agg(smz_pixels=("pixel_count", "sum"), smz_acres=("acres", "sum")))
    units = units.drop(columns=["smz_pixels", "smz_acres"]).merge(smz, on="unit_id", how="left")
    units[["smz_pixels", "smz_acres"]] = units[["smz_pixels", "smz_acres"]].fillna(0)
    units["SMZ_Pct"] = 100.0 * units["smz_pixels"] / units["pixel_count"]

    log.info("Units: %d over %d stands, %.0f ac (prior artifacts: %d / %d / %d ac)",
             len(units), units["PLT_CN"].nunique(), units["acres"].sum(),
             PRIOR_UNITS, PRIOR_STANDS, PRIOR_ACRES)
    if len(units) != PRIOR_UNITS or units["PLT_CN"].nunique() != PRIOR_STANDS:
        raise RuntimeError(
            f"attribution drifted from the published landscape: {len(units)} units / "
            f"{units['PLT_CN'].nunique()} stands vs {PRIOR_UNITS} / {PRIOR_STANDS}"
        )
    if abs(units["acres"].sum() - PRIOR_ACRES) > 1.0:
        raise RuntimeError(f"acreage drifted: {units['acres'].sum():.0f} vs {PRIOR_ACRES}")
    return units, pieces


# --------------------------------------------------------------------------------------
# Stage C — re-enumerate the decision space, with and without the override live
# --------------------------------------------------------------------------------------

def enumerate_library(units: pd.DataFrame, riparian_active: bool, scenario: str) -> pd.DataFrame:
    """The 2026-08-17 enumeration, with `SMZ_Pct` carried into the unit mapping.

    With ``riparian_active=False`` every unit is offered its owner/branch menu, which
    reproduces last week's library exactly. With it True, a unit that trips the repo's own
    `_is_riparian` test is offered only the prescription the override names.
    """
    base_cfg = load_regimes_config()
    override = _riparian_override(base_cfg)
    cache: dict = {}
    rows = []

    for unit in units.itertuples(index=False):
        smz_pct = float(unit.SMZ_Pct)
        mapping = {"OWN_CODE": unit.OWN_CODE, "FORTYPCD": unit.FORTYPCD, "SMZ_Pct": smz_pct}
        assignment = classify_owner(mapping)
        if assignment.owner_class == MASKED:
            continue
        owner_class = assignment.owner_class
        branch = forest_type_branch(mapping)
        riparian = riparian_active and _is_riparian(mapping, override)
        menu = [override["prescription"]] if riparian else eligible_prescriptions(owner_class, branch)
        age = None if pd.isna(unit.stand_age) else float(unit.stand_age)

        for prescription in menu:
            resolved = PRIOR_17.resolve_pair(owner_class, branch, prescription, age, base_cfg, cache)
            rows.append({
                "scenario": scenario,
                "unit_id": unit.unit_id,
                "tm_id": unit.tm_id,
                "PLT_CN": unit.PLT_CN,
                "county": unit.county,
                "owner_class": owner_class,
                "forest_branch": branch,
                "acres": unit.acres,
                "SMZ_Pct": smz_pct,
                "riparian": riparian,
                "stand_age": age,
                "prescription": prescription,
                "template": resolved["template"],
                "n_entries": resolved["n_entries"],
                "cuts": resolved["cuts"],
            })

    lib = pd.DataFrame(rows)
    log.info("[%s] decision space: %d rows over %d units, %d stands; %d riparian units",
             scenario, len(lib), lib["unit_id"].nunique(), lib["PLT_CN"].nunique(),
             lib.loc[lib["riparian"], "unit_id"].nunique())
    return lib


def smz_split_units(units: pd.DataFrame, pieces: pd.DataFrame) -> pd.DataFrame:
    """The geometric reading: every unit split at the buffer edge.

    In-SMZ pieces are 100% inside a stream-management zone by construction, so they trip
    the override at any threshold; out-of-SMZ pieces are 0%. This is what a polygon unit
    delineation (Phase 2.3) produces, expressed on today's attribution.
    """
    keys = ["unit_id", "tm_id", "county", "owner_class", "owner_name", "owner_group",
            "OWN_CODE", "PLT_CN", "FORTYPCD", "ForTypName", "in_smz"]
    split = (pieces.groupby(keys, as_index=False, dropna=False)
             .agg(pixel_count=("pixel_count", "sum"), acres=("acres", "sum")))
    split["SMZ_Pct"] = np.where(split["in_smz"], 100.0, 0.0)
    split["unit_id"] = split["unit_id"] + np.where(split["in_smz"], "_SMZ", "_UPL")
    split = split.merge(units[["PLT_CN", "stand_age"]].drop_duplicates("PLT_CN"),
                        on="PLT_CN", how="left")
    log.info("SMZ-split pieces: %d (%d in-SMZ, %.0f ac in-SMZ of %.0f ac)",
             len(split), int(split["in_smz"].sum()),
             split.loc[split["in_smz"], "acres"].sum(), split["acres"].sum())
    return split


# --------------------------------------------------------------------------------------
# Stage D — summaries
# --------------------------------------------------------------------------------------

def summarize_smz(units: pd.DataFrame, pieces: pd.DataFrame, streams) -> dict[str, pd.DataFrame]:
    forested = pieces.copy()
    forested["forest_branch"] = forested.apply(
        lambda r: forest_type_branch({"FORTYPCD": r["FORTYPCD"]}), axis=1)

    by_county = (
        forested.groupby("county", as_index=False)
        .agg(acres=("acres", "sum"),
             smz_acres=("acres", lambda s: 0.0))
        .drop(columns="smz_acres")
    )
    smz_c = (forested[forested["in_smz"]].groupby("county", as_index=False)
             .agg(smz_acres=("acres", "sum")))
    by_county = by_county.merge(smz_c, on="county", how="left").fillna({"smz_acres": 0.0})
    by_county["smz_pct"] = 100 * by_county["smz_acres"] / by_county["acres"]

    def _share(df: pd.DataFrame, key: str) -> pd.DataFrame:
        tot = df.groupby(key, as_index=False).agg(acres=("acres", "sum"))
        smz = (df[df["in_smz"]].groupby(key, as_index=False).agg(smz_acres=("acres", "sum")))
        out = tot.merge(smz, on=key, how="left").fillna({"smz_acres": 0.0})
        out["smz_pct"] = 100 * out["smz_acres"] / out["acres"]
        return out.sort_values("acres", ascending=False)

    by_owner = _share(forested, "owner_name")
    by_branch = _share(forested, "forest_branch")

    by_class = (
        forested[forested["in_smz"]].groupby("buffer_class", as_index=False)
        .agg(smz_acres=("acres", "sum"), pixels=("pixel_count", "sum"))
        .sort_values("smz_acres", ascending=False)
    )
    stream_km = (streams.groupby(streams["buffer_class"].fillna("unbuffered"), as_index=False)
                 .agg(features=("fcode", "size"), stream_km=("length_km", "sum"))
                 .rename(columns={"buffer_class": "buffer_class"}))
    by_class = by_class.merge(stream_km, on="buffer_class", how="outer").fillna(0.0)

    unit_dist = pd.DataFrame({
        "smz_pct_band": ["0 (no SMZ pixel)", "0-5", "5-10", "10-25", "25-50", ">=50 (riparian)"],
        "units": [
            int((units["SMZ_Pct"] == 0).sum()),
            int(((units["SMZ_Pct"] > 0) & (units["SMZ_Pct"] < 5)).sum()),
            int(((units["SMZ_Pct"] >= 5) & (units["SMZ_Pct"] < 10)).sum()),
            int(((units["SMZ_Pct"] >= 10) & (units["SMZ_Pct"] < 25)).sum()),
            int(((units["SMZ_Pct"] >= 25) & (units["SMZ_Pct"] < 50)).sum()),
            int((units["SMZ_Pct"] >= 50).sum()),
        ],
    })
    unit_dist["acres"] = [
        units.loc[units["SMZ_Pct"] == 0, "acres"].sum(),
        units.loc[(units["SMZ_Pct"] > 0) & (units["SMZ_Pct"] < 5), "acres"].sum(),
        units.loc[(units["SMZ_Pct"] >= 5) & (units["SMZ_Pct"] < 10), "acres"].sum(),
        units.loc[(units["SMZ_Pct"] >= 10) & (units["SMZ_Pct"] < 25), "acres"].sum(),
        units.loc[(units["SMZ_Pct"] >= 25) & (units["SMZ_Pct"] < 50), "acres"].sum(),
        units.loc[units["SMZ_Pct"] >= 50, "acres"].sum(),
    ]
    return {"by_county": by_county, "by_owner": by_owner, "by_forest_branch": by_branch,
            "by_buffer_class": by_class, "unit_smz_distribution": unit_dist}


def library_delta(libs: dict[str, pd.DataFrame], cfg: dict) -> pd.DataFrame:
    rows = []
    for scenario, lib in libs.items():
        runs = lib.groupby(["PLT_CN", "prescription"]).size().reset_index(name="n")
        harvestable = lib[lib["cuts"]]
        rip_units = lib.loc[lib["riparian"], "unit_id"].nunique()
        rip_acres = lib.loc[lib["riparian"]].drop_duplicates("unit_id")["acres"].sum()
        rows.append({
            "scenario": scenario,
            "units": lib["unit_id"].nunique(),
            "acres": lib.drop_duplicates("unit_id")["acres"].sum(),
            "library_rows": len(lib),
            "fvs_runs": len(runs),
            "fvs_runs_si_expanded": len(runs) * cfg["si_bins"],
            "riparian_units": rip_units,
            "riparian_acres": rip_acres,
            "units_with_a_cutting_option": harvestable["unit_id"].nunique(),
            "acres_with_a_cutting_option": harvestable.drop_duplicates("unit_id")["acres"].sum(),
        })
    delta = pd.DataFrame(rows)
    base = delta[delta["scenario"] == "no_riparian"].iloc[0]
    delta["library_rows_vs_baseline"] = delta["library_rows"] - base["library_rows"]
    delta["fvs_runs_vs_baseline"] = delta["fvs_runs"] - base["fvs_runs"]
    delta["harvestable_acres_vs_baseline"] = (
        delta["acres_with_a_cutting_option"] - base["acres_with_a_cutting_option"])
    return delta


def main() -> None:
    cfg = load_regimes_config()
    override = _riparian_override(cfg)
    log.info("Riparian override in force: field=%s min_value=%s prescription=%s absolute=%s",
             override["field"], override["min_value"], override["prescription"],
             override["absolute"])

    units, pieces = build_units_with_smz()
    ages = PRIOR_17.stand_ages()
    units = units.merge(ages, on="PLT_CN", how="left")

    libs = {
        "no_riparian": enumerate_library(units.assign(SMZ_Pct=0.0), False, "no_riparian"),
        "unit_mean": enumerate_library(units, True, "unit_mean"),
    }
    split = smz_split_units(units, pieces)
    libs["smz_split"] = enumerate_library(split, True, "smz_split")

    # The map layers were written by build_smz_layer(); re-read for the summaries.
    import geopandas as gpd
    streams = gpd.read_file(STREAM_LAYER_CACHE)
    summaries = summarize_smz(units, pieces, streams)
    delta = library_delta(libs, cfg)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    unit_cols = ["unit_id", "tm_id", "PLT_CN", "county", "owner_name", "owner_class",
                 "FORTYPCD", "ForTypName", "pixel_count", "acres", "smz_pixels",
                 "smz_acres", "SMZ_Pct", "stand_age"]
    units_out = units[unit_cols].sort_values("SMZ_Pct", ascending=False)
    units_out.to_csv(OUT_DIR / "smz_by_unit.csv", index=False)
    for name, df in summaries.items():
        df.to_csv(OUT_DIR / f"smz_{name}.csv", index=False)
    delta.to_csv(OUT_DIR / "library_riparian_delta.csv", index=False)

    # The riparian decision space under the geometric reading, at unit-piece resolution.
    rip = libs["smz_split"]
    rip[rip["riparian"]].to_csv(OUT_DIR / "riparian_pieces.csv", index=False)

    log.info("=" * 86)
    for name, df in summaries.items():
        log.info("%s\n%s", name, df.to_string(index=False))
    log.info("library delta\n%s", delta.to_string(index=False))
    log.info("=" * 86)


if __name__ == "__main__":
    main()
