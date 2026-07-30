"""Weekly progress figures (week of 2026-07-20) from the ARTEMIS R2 bucket.

Eight figures covering the harvest-scheduling work and the model slices around
it. Every panel is computed from data pulled out of `r2:artemis-r2` at run time
(see ``R2_INPUTS``) except the last, which charts the measured results of the
management-injection gate recorded in
``research/restart_fidelity/outputs/gate_cut_injection.txt``.

Run:

    uv run python weekly-artifact/2026-07-26/make_figures.py

Credentials come from the ``RCLONE_CONFIG_R2_*`` environment variables. Inputs
are cached under ``--cache`` (default ``data/interim/r2_cache``) so re-runs are
free.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import rasterio
from matplotlib.patches import Patch
from rasterio.enums import Resampling
from rasterio.vrt import WarpedVRT

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
BUCKET = "artemis-r2"

# --- R2 inputs -------------------------------------------------------------
R2_INPUTS = {
    "tpo": "data/Harvest_level_guidance_from_TPO_reports_1999-2024.xlsx",
    "trajectory": "data/Artemis_project_fvs_copy_no_management/fvs_trajectory.csv",
    "stand_change": "data/Artemis_project_fvs_copy_no_management/fvs_stand_change.csv",
    "crosswalk": "data/TreeMap_Chaz/output/FL_5county_TreeMap_TMIDs.csv",
    "treemap": "data/TreeMap_Chaz/FiveFloridaCounties/TreeMap2022_CONUS_5FlCntys.tif",
}
# Read as a window/warp rather than downloaded whole: 3.9 GB CONUS raster.
R2_OWNERSHIP = "data/RDS-2025-0045/Data/US_forest_ownership.tif"

# --- constants -------------------------------------------------------------
ACRES_PER_PIXEL = 900 / 4046.8564224  # 30 m pixel, EPSG:5070

# Harris 2025 pixel classes, per config/projection.yaml.
OWNER_LABEL = {
    0: "Unknown forest",
    1: "Non-forest",
    2: "Water",
    3: "Family forest",
    4: "Corporate forest",
    5: "Tribal forest",
    6: "Federal forest",
    7: "State forest",
    8: "Local forest",
}
OWNER_CLASSES = [3, 4, 5, 6, 7, 8]  # classes that can own a harvest bundle

# Harris ownership class -> the three owner groups the TPO reports use.
TPO_GROUP = {3: "Private", 4: "Private", 5: "Private",
             6: "Federal (NF)", 7: "Other public", 8: "Other public"}
TPO_ORDER = ["Private", "Other public", "Federal (NF)"]

# Correct Florida spelling. The TPO workbook's own header says "Suwanee"; the
# columns are read positionally, so this only fixes the label on the figures.
COUNTIES = ["Baker", "Columbia", "Hamilton", "Suwannee", "Union"]

# --- palette (dataviz skill reference palette, light mode) ------------------
# Validated: `validate_palette.js "#2a78d6,#eb6834,#1baf7a" --mode light --pairs all`
# -> all checks pass; aqua carries a contrast WARN, so every series is direct-labelled.
BLUE, ORANGE, AQUA = "#2a78d6", "#eb6834", "#1baf7a"
SERIES = {"Private": BLUE, "Other public": ORANGE, "Federal (NF)": AQUA}
SEQ = ["#cde2fb", "#9ec5f4", "#5598e7", "#2a78d6", "#1c5cab", "#0d366b"]
INK, INK2, INK3 = "#0b0b0b", "#52514e", "#8a8880"
SURFACE, GRID = "#fcfcfb", "#e7e6e2"
GOOD, CRIT = "#1baf7a", "#e34948"

# Management-injection gate, measured 2026-07-17.
# Source: research/restart_fidelity/outputs/gate_cut_injection.txt
GATE = {
    "stand": "43393151010478",
    "cut_year": 2004,
    "target_prop": 0.30,
    "pre": {"Tpa": 1179.261, "BA": 106.456},
    "post": {"Tpa": 825.483, "BA": 74.519},
    "arm_deltas": [("G1 keyword\nvs G2 fvsCutNow", 0.0),
                   ("G2 in-process\nvs G3 after restart", 0.0)],
    "bundle": {"target_id": "121705900034", "other_id": "121705900036",
               "target_pre": 1656.3, "target_post": 1159.4, "other_max_delta": 0.0},
}


# --- plumbing --------------------------------------------------------------
def r2_client():
    import boto3

    missing = [k for k in ("RCLONE_CONFIG_R2_ENDPOINT", "RCLONE_CONFIG_R2_ACCESS_KEY_ID",
                           "RCLONE_CONFIG_R2_SECRET_ACCESS_KEY") if not os.environ.get(k)]
    if missing:
        sys.exit(f"missing R2 credentials in the environment: {', '.join(missing)}")
    return boto3.client(
        "s3",
        endpoint_url=os.environ["RCLONE_CONFIG_R2_ENDPOINT"],
        aws_access_key_id=os.environ["RCLONE_CONFIG_R2_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["RCLONE_CONFIG_R2_SECRET_ACCESS_KEY"],
    )


def fetch(cache: Path, key: str) -> Path:
    """Download an R2 object into ``cache``, keeping whatever is already there."""
    dst = cache / key.split("/")[-1]
    if not dst.exists():
        cache.mkdir(parents=True, exist_ok=True)
        print(f"  fetching r2:{BUCKET}/{key}")
        r2_client().download_file(BUCKET, key, str(dst))
    return dst


def ownership_on_aoi_grid(cache: Path, treemap_path: Path) -> np.ndarray:
    """Harris 2025 ownership, warped onto the 5-county TreeMap grid.

    The source is a 3.9 GB EPSG:4269 raster in R2; GDAL's /vsis3 driver reads
    only the tiles the AOI window touches (~35 s), so it is never downloaded.
    """
    cached = cache / "ownership_aoi_5070.npy"
    if cached.exists():
        return np.load(cached)

    endpoint = os.environ["RCLONE_CONFIG_R2_ENDPOINT"]
    gdal_env = {
        "AWS_ACCESS_KEY_ID": os.environ["RCLONE_CONFIG_R2_ACCESS_KEY_ID"],
        "AWS_SECRET_ACCESS_KEY": os.environ["RCLONE_CONFIG_R2_SECRET_ACCESS_KEY"],
        "AWS_S3_ENDPOINT": endpoint.replace("https://", ""),
        "AWS_VIRTUAL_HOSTING": "FALSE",
        "AWS_REGION": "auto",
        "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
        "VSI_CACHE": "TRUE",
    }
    print(f"  warping r2:{BUCKET}/{R2_OWNERSHIP} onto the AOI grid")
    with rasterio.Env(**gdal_env), rasterio.open(treemap_path) as tm:
        with rasterio.open(f"/vsis3/{BUCKET}/{R2_OWNERSHIP}") as src:
            with WarpedVRT(src, crs=tm.crs, transform=tm.transform, width=tm.width,
                           height=tm.height, resampling=Resampling.nearest) as vrt:
                own = vrt.read(1)
    cached.parent.mkdir(parents=True, exist_ok=True)
    np.save(cached, own)
    return own


def style(ax, title=None, xlabel=None, ylabel=None):
    ax.set_facecolor(SURFACE)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(GRID)
    ax.tick_params(colors=INK2, labelsize=8, length=3, color=GRID)
    ax.grid(True, color=GRID, lw=0.6, zorder=0)
    ax.set_axisbelow(True)
    if title:
        ax.set_title(title, color=INK, fontsize=10, loc="left", pad=8)
    if xlabel:
        ax.set_xlabel(xlabel, color=INK2, fontsize=8.5)
    if ylabel:
        ax.set_ylabel(ylabel, color=INK2, fontsize=8.5)
    return ax


def figure(nrows=1, ncols=1, figsize=(10, 5), **kw):
    fig, axes = plt.subplots(nrows, ncols, figsize=figsize, facecolor=SURFACE, **kw)
    return fig, axes


def caption(fig, text):
    fig.text(0.01, 0.005, text, color=INK3, fontsize=7.2, va="bottom", ha="left")


def save(fig, name: str, outdir: Path):
    outdir.mkdir(parents=True, exist_ok=True)
    path = outdir / name
    fig.savefig(path, dpi=170, facecolor=SURFACE, bbox_inches="tight")
    plt.close(fig)
    # --outdir may point anywhere; only shorten the path when it is inside the repo.
    try:
        shown = path.relative_to(REPO)
    except ValueError:
        shown = path
    print(f"  wrote {shown}")


# --- data loading ----------------------------------------------------------
def load_tpo(path: Path) -> dict[str, pd.DataFrame]:
    """The TPO workbook is a hand-laid sheet; pull the two blocks by position.

    ByOwnerGroup rows 16-31 hold the total (softwood+hardwood) block, columns
    10-13 = year, Federal (NF), Other public, Private, in thousands of cubic
    feet. ByCounty rows 14-29 hold year + the five counties, same units.
    """
    owner = pd.read_excel(path, sheet_name="ByOwnerGroup", header=None)
    blk = owner.iloc[16:32, [10, 11, 12, 13]].astype(float)
    blk.columns = ["year", "Federal (NF)", "Other public", "Private"]
    blk["year"] = blk["year"].astype(int)
    blk["Total"] = blk[TPO_ORDER].sum(axis=1)

    county = pd.read_excel(path, sheet_name="ByCounty", header=None)
    cblk = county.iloc[14:30, [0, 1, 2, 3, 4, 5]].astype(float)
    cblk.columns = ["year"] + COUNTIES
    cblk["year"] = cblk["year"].astype(int)

    for name, df in (("owner", blk), ("county", cblk)):
        if len(df) != 16 or df.isna().any().any():
            raise ValueError(f"TPO {name} block did not parse to 16 clean rows")
    return {"owner": blk.reset_index(drop=True), "county": cblk.reset_index(drop=True)}


def load_stand_acres(cache: Path) -> pd.DataFrame:
    """Per-stand pixel counts by ownership class, on the painted AOI footprint.

    Rows are FVS ``stand_cn`` (== crosswalk PLT_CN); columns are Harris classes
    holding acres. This is the TM_ID -> PLT_CN -> stand join the painter uses
    (``pipeline/s4_fvs/paint_fvs_to_raster.py``), with ownership tabulated over
    each stand's pixels instead of a projected value painted onto them.
    """
    treemap = fetch(cache, R2_INPUTS["treemap"])
    own = ownership_on_aoi_grid(cache, treemap)
    with rasterio.open(treemap) as src:
        tm = src.read(1)
        nodata = src.nodata

    xwalk = (pd.read_csv(fetch(cache, R2_INPUTS["crosswalk"]),
                         usecols=["Value", "PLT_CN"], dtype={"PLT_CN": "string"})
             .dropna(subset=["PLT_CN"]).drop_duplicates("Value"))
    traj = load_trajectory(cache)
    xwalk = xwalk[xwalk["PLT_CN"].isin(set(traj["stand_cn"]))]

    valid = np.ones(tm.shape, dtype=bool) if nodata is None else tm != nodata
    keys = np.sort(xwalk["Value"].to_numpy().astype("int64"))
    flat = tm[valid].astype("int64")
    idx = np.clip(np.searchsorted(keys, flat), 0, keys.size - 1)
    hit = keys[idx] == flat
    if hit.sum() / hit.size < 0.99:
        raise ValueError(f"only {hit.sum()/hit.size:.1%} of AOI pixels joined the crosswalk")

    pairs = pd.DataFrame({"tm_id": flat[hit], "own": own[valid][hit]})
    counts = pairs.pivot_table(index="tm_id", columns="own", aggfunc="size", fill_value=0)
    counts = counts.join(xwalk.set_index("Value")["PLT_CN"]).groupby("PLT_CN").sum()
    return counts * ACRES_PER_PIXEL


def load_trajectory(cache: Path) -> pd.DataFrame:
    return pd.read_csv(fetch(cache, R2_INPUTS["trajectory"]),
                       dtype={"stand_cn": "string", "stand_id": "string"})


# --- figures ---------------------------------------------------------------
def fig_even_flow_targets(tpo, outdir):
    """The demand signal the even-flow objective has to reproduce."""
    d = tpo["owner"]
    fig = plt.figure(figsize=(11, 6.2), facecolor=SURFACE)
    gs = fig.add_gridspec(2, 3, height_ratios=[1.25, 1], hspace=0.55, wspace=0.28)

    ax = style(fig.add_subplot(gs[0, :]), ylabel="million ft³ per year")
    ax.plot(d["year"], d["Total"] / 1e3, color=BLUE, lw=2, marker="o", ms=5,
            mfc=SURFACE, mew=1.6, zorder=3)
    all_mean, recent_mean = d["Total"].mean() / 1e3, d.loc[d.year >= 2013, "Total"].mean() / 1e3
    for val, lab, col, ls in ((all_mean, f"all years  {all_mean:.1f}", INK3, ":"),
                              (recent_mean, f"2013–2024  {recent_mean:.1f}", ORANGE, "--")):
        ax.hlines(val, 1998, 2024.5, color=col, lw=1.4, ls=ls, zorder=2)
        ax.text(2025.2, val, lab, color=col, fontsize=8, va="center", ha="left")
    ax.set_xlim(1997, 2031)
    ax.set_title("Observed harvest, five-county pilot — the even-flow target the scheduler must hold",
                 color=INK, fontsize=11, loc="left", pad=8)
    ax.text(2025.2, all_mean - 3.0, "candidate\nflow targets", color=INK3,
            fontsize=8, va="top")

    for i, grp in enumerate(TPO_ORDER):
        a = style(fig.add_subplot(gs[1, i]), title=grp,
                  ylabel="million ft³ / yr" if i == 0 else None)
        a.plot(d["year"], d[grp] / 1e3, color=SERIES[grp], lw=2)
        m = d.loc[d.year >= 2013, grp].mean() / 1e3
        a.axhline(m, color=INK3, lw=1.2, ls="--")
        a.text(0.98, 0.06, f"target {m:,.1f}", transform=a.transAxes, ha="right",
               color=INK2, fontsize=8)
        cv = d[grp].std() / d[grp].mean()
        a.text(0.98, 0.92, f"CV {cv:.0%}", transform=a.transAxes, ha="right",
               va="top", color=INK2, fontsize=8)
        a.set_ylim(0, d[grp].max() / 1e3 * 1.25)

    caption(fig, "Source: r2:artemis-r2/data/Harvest_level_guidance_from_TPO_reports_1999-2024.xlsx "
                 "(USFS TPO, all products, softwood + hardwood). Bottom panels use independent y scales.")
    save(fig, "fig1_even_flow_targets.png", outdir)
    return {"tpo_total_recent": recent_mean, "tpo_total_all": all_mean}


def fig_harvest_by_county(tpo, outdir):
    """Per-county flow, and how far each county swings year to year."""
    d = tpo["county"]
    fig, axes = figure(1, 6, figsize=(13, 3.4), gridspec_kw={"wspace": 0.32})
    for i, c in enumerate(COUNTIES):
        a = style(axes[i], title=c, ylabel="million ft³ / yr" if i == 0 else None)
        a.plot(d["year"], d[c] / 1e3, color=BLUE, lw=1.8)
        m = d.loc[d.year >= 2013, c].mean() / 1e3
        a.axhline(m, color=ORANGE, lw=1.3, ls="--")
        a.text(0.96, 0.06, f"{m:,.1f}", transform=a.transAxes, ha="right",
               color=ORANGE, fontsize=8)
        a.set_ylim(0, d[COUNTIES].max().max() / 1e3 * 1.1)
        a.set_xticks([2000, 2012, 2024])
        if i:
            a.set_yticklabels([])

    cv = (d[COUNTIES].std() / d[COUNTIES].mean()).sort_values()
    a = style(axes[5], title="Year-to-year swing", xlabel="coefficient of variation")
    a.barh(cv.index, cv.values, color=SEQ[3], height=0.62)
    for name, v in cv.items():
        a.text(v + 0.008, name, f"{v:.0%}", va="center", color=INK2, fontsize=8)
    a.set_xlim(0, cv.max() * 1.35)
    a.grid(axis="y", visible=False)

    fig.suptitle("Harvest by county, 1999–2024 — the dispersion an even-flow schedule removes",
                 color=INK, fontsize=11, x=0.005, ha="left", y=1.06)
    caption(fig, "Source: TPO workbook, sheet ByCounty. Dashed line = 2013–2024 mean (the candidate per-county target).")
    save(fig, "fig2_harvest_by_county.png", outdir)


def fig_supply_vs_demand(cache, tpo, acres, outdir):
    """Standing inventory and net growth against the observed harvest level."""
    traj = load_trajectory(cache)
    years = sorted(traj.loc[traj.calendar_year >= 2026, "calendar_year"].unique())
    merch = traj[traj.calendar_year.isin(years)].pivot_table(
        index="stand_cn", columns="calendar_year", values="merch_cuft").loc[acres.index]
    acc = traj[traj.calendar_year.isin(years)].pivot_table(
        index="stand_cn", columns="calendar_year", values="accretion").loc[acres.index]
    mort = traj[traj.calendar_year.isin(years)].pivot_table(
        index="stand_cn", columns="calendar_year", values="mortality").loc[acres.index]

    grp_acres = acres[[c for c in acres.columns if c in TPO_GROUP]].T.groupby(TPO_GROUP).sum().T
    total_acres = acres.sum(axis=1)
    # Pixels inside a painted stand that Harris calls non-forest, water or
    # unknown-forest have no owner group; keep them so the stack and the growth
    # panel describe the same footprint.
    unclassed = total_acres - grp_acres.sum(axis=1)
    bands = TPO_ORDER + ["No owner class"]
    band_acres = grp_acres.assign(**{"No owner class": unclassed})
    stock = pd.DataFrame({g: merch.mul(band_acres[g], axis=0).sum() / 1e6 for g in bands})
    net_growth = (acc - mort).mul(total_acres, axis=0).sum() / 1e6
    net_growth = net_growth.iloc[:-1]  # terminal cycle reports no accretion

    demand = tpo["owner"].loc[tpo["owner"].year >= 2013, "Total"].mean() / 1e3

    fig, (ax1, ax2) = figure(1, 2, figsize=(13.5, 4.8), gridspec_kw={"wspace": 0.42})

    style(ax1, title="Standing merchantable volume, no management",
          ylabel="billion ft³", xlabel="calendar year")
    band_color = dict(SERIES, **{"No owner class": "#cfcecb"})
    ax1.stackplot(stock.index, *[stock[g] / 1e3 for g in bands],
                  colors=[band_color[g] for g in bands], edgecolor=SURFACE, lw=2, zorder=3)
    # Direct-label each band to the right of the stack, nudged apart so the thin
    # public bands stay legible.
    ends = [stock[g].iloc[-1] / 1e3 for g in bands]
    mids = np.cumsum(ends) - np.array(ends) / 2
    for i in range(1, len(mids)):  # minimum label spacing, in data units
        mids[i] = max(mids[i], mids[i - 1] + 0.42)
    for g, end, y in zip(bands, ends, mids):
        ax1.text(2078, y, f"{g}  {end:.2f}", color=band_color[g] if g != "No owner class" else INK2,
                 fontsize=8.5, va="center", ha="left", zorder=5)
    ax1.set_xlim(2026, 2076)
    total = stock.sum(axis=1)
    ax1.text(2027, total.iloc[-1] / 1e3 * 1.02,
             f"{total.iloc[0]/1e3:.1f} bn ft³ in 2026 → {total.iloc[-1]/1e3:.1f} bn ft³ in 2076",
             color=INK, fontsize=9, va="bottom")

    style(ax2, title="Annual net growth vs. the observed harvest level",
          ylabel="million ft³ per year", xlabel="calendar year")
    ax2.plot(net_growth.index, net_growth.values, color=BLUE, lw=2.2, marker="o",
             ms=6, mfc=SURFACE, mew=1.6, zorder=4, label="net growth (accretion − mortality)")
    ax2.axhline(demand, color=ORANGE, lw=2, ls="--", zorder=3, label="TPO harvest, 2013–2024 mean")
    ax2.text(2072, demand + 3, f"harvest {demand:,.0f}", color=ORANGE, fontsize=8.5, ha="right")
    ax2.text(2029, net_growth.iloc[0] - 12, "net growth", color=BLUE, fontsize=8.5)
    cross = net_growth.index[np.argmax(net_growth.values < demand)] if (net_growth.values < demand).any() else None
    if cross is not None:
        ax2.axvspan(cross, net_growth.index[-1] + 5, color=CRIT, alpha=0.06, zorder=1)
        ax2.text(cross + 1.5, net_growth.max() * 0.82,
                 f"growth falls below the\nharvest level by {cross}", color=CRIT, fontsize=8.5)
    ax2.set_ylim(0, max(net_growth.max(), demand) * 1.25)
    ax2.set_xlim(2026, 2076)

    fig.suptitle("Supply vs. demand for the five-county pilot", color=INK, fontsize=11.5,
                 x=0.005, ha="left", y=1.02)
    caption(fig, "FVS no-management projection (r2:.../fvs_trajectory.csv, 693 stands) expanded to "
                 f"{total_acres.sum():,.0f} painted acres via the TreeMap 2022 crosswalk; ownership split from "
                 f"Harris 2025 (RDS-2025-0045), which classes {unclassed.sum()/total_acres.sum():.0%} of those "
                 "acres as non-forest, water or unknown-owner. Growth shown is the unharvested trajectory — "
                 "harvesting resets stands to faster-growing states, which is what the scheduler is for.")
    save(fig, "fig3_supply_vs_demand.png", outdir)
    return {"stock_2026": stock.sum(axis=1).iloc[0], "stock_2076": stock.sum(axis=1).iloc[-1],
            "growth_2026": net_growth.iloc[0], "growth_last": net_growth.iloc[-1],
            "crossing": cross, "demand": demand, "acres": total_acres.sum()}


def fig_barrier_alignment(cache, outdir):
    """Why the orchestrator needs a stop/restart barrier: stands are not aligned."""
    traj = load_trajectory(cache)
    per_year = traj.groupby("calendar_year")["stand_id"].nunique()
    starts = traj.groupby("stand_cn")["calendar_year"].min().value_counts().sort_index()

    fig, (ax1, ax2) = figure(1, 2, figsize=(12, 4.2),
                             gridspec_kw={"wspace": 0.2, "width_ratios": [1.6, 1]})
    style(ax1, title="Stands reporting in each calendar year",
          ylabel="stands with a projected value", xlabel="calendar year")
    colors = [BLUE if n == 693 else ORANGE for n in per_year.values]
    ax1.bar(per_year.index, per_year.values, color=colors, width=1.6, zorder=3)
    ax1.axhline(693, color=INK3, lw=1.2, ls=":")
    ax1.text(2080, 700, "all 693 stands", color=INK3, fontsize=8, va="bottom", ha="right")
    ax1.annotate("inventory years 1997–2021: stands enter at\ndifferent dates, so a calendar year is not a\ncommon snapshot",
                 xy=(2012, 130), xytext=(1997, 900), color=ORANGE, fontsize=8.5,
                 arrowprops=dict(arrowstyle="->", color=ORANGE, lw=1.2))
    ax1.annotate("from 2026 the run is on a\nshared 5-year cycle", xy=(2046, 700),
                 xytext=(2040, 880), color=BLUE, fontsize=8.5,
                 arrowprops=dict(arrowstyle="->", color=BLUE, lw=1.2))
    ax1.set_ylim(0, 1040)

    style(ax2, title="Inventory year each stand starts from",
          ylabel="stands", xlabel="first projected year")
    ax2.bar(starts.index, starts.values, color=SEQ[3], width=0.85, zorder=3)
    ax2.text(0.03, 0.93, f"{starts.index.min()}–{starts.index.max()}\n{len(starts)} distinct start years",
             transform=ax2.transAxes, color=INK2, fontsize=8.5, va="top")

    fig.suptitle("The synchronisation problem the restart barrier solves",
                 color=INK, fontsize=11.5, x=0.005, ha="left", y=1.02)
    caption(fig, "Source: r2:.../fvs_trajectory.csv. FVS processes one stand fully before the next, so within a "
                 "process stands are never aligned in time; the stop/restart barrier is what stores every stand at "
                 "a common year t so a bundle-wide harvest allocation can see them at once.")
    save(fig, "fig4_barrier_alignment.png", outdir)
    return {"start_years": (int(starts.index.min()), int(starts.index.max()))}


def fig_baseline_ensemble(cache, outdir):
    """The no-management counterfactual every scheduled run gets measured against."""
    traj = load_trajectory(cache)
    traj = traj[traj.calendar_year >= 2026]
    fig, axes = figure(1, 3, figsize=(12.5, 4.2), gridspec_kw={"wspace": 0.26})
    panels = [("basal_area", "basal area (ft²/ac)", "Basal area"),
              ("merch_cuft", "merchantable volume (ft³/ac)", "Merchantable volume"),
              ("trees_per_acre", "trees per acre", "Stem density")]
    for ax, (col, ylab, title) in zip(axes, panels):
        g = traj.groupby("calendar_year")[col]
        q = g.quantile([0.1, 0.25, 0.5, 0.75, 0.9]).unstack()
        style(ax, title=title, ylabel=ylab, xlabel="calendar year")
        ax.fill_between(q.index, q[0.1], q[0.9], color=SEQ[1], alpha=0.75, lw=0, zorder=2)
        ax.fill_between(q.index, q[0.25], q[0.75], color=SEQ[2], alpha=0.85, lw=0, zorder=3)
        ax.plot(q.index, q[0.5], color=SEQ[5], lw=2.2, zorder=4)
        ax.set_xlim(2026, 2076)
        if col == "basal_area":
            ax.text(2028, q[0.5].iloc[0], " median", color=SEQ[5], fontsize=8.5, va="bottom")
            ax.text(2028, q[0.9].iloc[0], " P10–P90", color=SEQ[3], fontsize=8.5, va="bottom")
            ax.text(2044, q[0.75].iloc[3], "P25–P75", color="white", fontsize=8.5, va="center")
    fig.suptitle("No-management baseline across all 693 stands, 2026–2076",
                 color=INK, fontsize=11.5, x=0.005, ha="left", y=1.03)
    caption(fig, "Source: r2:.../fvs_trajectory.csv, FVS Southern variant FS2026.1, run NoManagement_5countyFL_1. "
                 "Unweighted across stands (each stand one sample), so this is the per-acre stand picture, not an AOI total.")
    save(fig, "fig5_baseline_ensemble.png", outdir)


def fig_ownership_map(cache, acres, outdir):
    """Where each owner's forest actually sits inside the AOI."""
    treemap = fetch(cache, R2_INPUTS["treemap"])
    own = ownership_on_aoi_grid(cache, treemap)
    with rasterio.open(treemap) as src:
        bounds = src.bounds
    step = 3  # ~90 m for display
    small = own[::step, ::step]
    extent = [bounds.left / 1e3, bounds.right / 1e3, bounds.bottom / 1e3, bounds.top / 1e3]

    panels = [("Family forest", [3]), ("Corporate / other private", [4]),
              ("Federal", [6]), ("State + local", [7, 8])]
    fig, axes = figure(1, 4, figsize=(13.5, 3.4), gridspec_kw={"wspace": 0.08})
    forest = np.isin(small, OWNER_CLASSES)
    for ax, (title, classes) in zip(axes, panels):
        ax.set_facecolor(SURFACE)
        ax.imshow(np.where(forest, 1.0, np.nan), extent=extent, cmap="Greys",
                  vmin=0, vmax=6, interpolation="nearest")
        sel = np.isin(small, classes)
        ax.imshow(np.where(sel, 1.0, np.nan), extent=extent, cmap="Blues",
                  vmin=0, vmax=1.35, interpolation="nearest")
        ac = sum(acres[c].sum() for c in classes if c in acres.columns)
        ax.set_title(f"{title}\n{ac:,.0f} ac on painted stands", color=INK, fontsize=9.5,
                     loc="left", pad=6)
        ax.set_xticks([])
        ax.set_yticks([])
        for s in ax.spines.values():
            s.set_color(GRID)
    axes[0].legend(handles=[Patch(facecolor=SEQ[4], label="this owner"),
                            Patch(facecolor="#cfcecb", label="other forest ownership")],
                   loc="upper left", bbox_to_anchor=(0.0, -0.03), ncol=2, frameon=False,
                   fontsize=8.5, labelcolor=INK2, handlelength=1.4)
    fig.suptitle("Forest ownership across the five-county AOI — one panel per candidate bundle",
                 color=INK, fontsize=11.5, x=0.005, ha="left", y=1.06)
    caption(fig, "Source: Harris 2025 forest ownership (r2:.../RDS-2025-0045) warped from EPSG:4269 ~11 m onto the "
                 "TreeMap 2022 EPSG:5070 30 m AOI grid, displayed at 90 m. Bundle-per-ownership is the parallel "
                 "decomposition in docs/superpowers/specs/2026-07-17-orchestrator-sketch.md.")
    save(fig, "fig6_ownership_map.png", outdir)


def fig_bundle_threshold(acres, outdir):
    """Does dominant-owner-with-threshold actually produce usable bundles?

    Two denominators are in play and they disagree by a lot, so both are shown.
    The spec says "> 70% of the stand's pixels", which means the stand's **whole
    painted footprint** — including the 15% of AOI acres Harris classes as
    non-forest, water or unknown-owner. Dividing instead by only the
    owner-classified pixels answers a different question ("of the pixels whose
    owner we know, how many agree?") and reports a materially higher purity.
    """
    owner_acres = acres[[c for c in acres.columns if c in OWNER_CLASSES]]
    stand_acres = acres.sum(axis=1)
    classified = owner_acres.sum(axis=1)
    dominant = owner_acres.max(axis=1)

    share = dominant / stand_acres                       # the spec's definition
    share_cls = dominant / classified.replace(0, np.nan)  # classified pixels only

    thresholds = np.arange(0.5, 1.001, 0.05)
    kept_stands = [int((share >= t).sum()) for t in thresholds]
    kept_acres = [stand_acres[share >= t].sum() for t in thresholds]
    kept_stands_cls = [int((share_cls >= t).sum()) for t in thresholds]

    fig, (ax1, ax2, ax3) = figure(1, 3, figsize=(13.5, 4.3),
                                  gridspec_kw={"wspace": 0.32, "width_ratios": [1.1, 1.15, 1]})

    style(ax1, title="Purity of each stand's ownership",
          ylabel="stands", xlabel="dominant owner's share")
    bins = np.arange(0.0, 1.05, 0.05)
    ax1.hist(share.dropna(), bins=bins, color=SEQ[2], zorder=2)
    ax1.hist(share_cls.dropna(), bins=bins, histtype="step", lw=1.8, color="#7a8b99",
             zorder=3)
    ax1.axvline(0.70, color=CRIT, lw=1.6, ls="--", zorder=4)
    top = ax1.get_ylim()[1]
    ax1.set_ylim(0, top * 1.42)
    ax1.text(0.72, top * 1.40, " 70% threshold\n in the spec", color=CRIT, fontsize=8.5,
             va="top")
    ax1.text(0.02, top * 1.40, "filled: share of the whole stand\nfootprint (the spec's rule)",
             color=SEQ[4], fontsize=8.5, va="top")
    ax1.text(0.02, top * 1.14, "outline: share of owner-classified\npixels only",
             color="#7a8b99", fontsize=8.5, va="top")

    style(ax2, title="What survives the threshold", xlabel="dominant-owner threshold",
          ylabel="% of the AOI retained")
    ax2.plot(thresholds * 100, np.array(kept_stands) / len(share) * 100, color=BLUE,
             lw=2.2, marker="o", ms=5, mfc=SURFACE, mew=1.5, zorder=4)
    ax2.plot(thresholds * 100, np.array(kept_acres) / stand_acres.sum() * 100, color=ORANGE,
             lw=2.2, marker="s", ms=5, mfc=SURFACE, mew=1.5, zorder=4)
    ax2.text(56, kept_stands[0] / len(share) * 100 - 15, "stands", color=BLUE, fontsize=8.5)
    ax2.text(50.5, kept_acres[0] / stand_acres.sum() * 100 + 4, "acres", color=ORANGE, fontsize=8.5)
    i70 = int(np.argmin(np.abs(thresholds - 0.70)))
    ax2.axvline(70, color=CRIT, lw=1.4, ls="--", zorder=3)
    ax2.annotate(f"at 70%: {kept_stands[i70]}/{len(share)} stands,\n"
                 f"{kept_acres[i70]/stand_acres.sum():.0%} of acres",
                 xy=(70, kept_acres[i70] / stand_acres.sum() * 100), xytext=(76, 46),
                 color=CRIT, fontsize=8.5,
                 arrowprops=dict(arrowstyle="->", color=CRIT, lw=1.2))
    ax2.text(50.5, 100, f"counting only owner-classified pixels would say "
             f"{kept_stands_cls[i70]}/{len(share)}\nstands at 70% — a different, easier question",
             color=INK3, fontsize=8, va="top")
    ax2.set_ylim(0, 105)

    style(ax3, title="Acres per owner group, allocated by pixel share",
          xlabel="thousand acres")
    grp = owner_acres.T.groupby(TPO_GROUP).sum().T.sum().reindex(TPO_ORDER)
    ax3.barh(list(grp.index), grp.values / 1e3, color=[SERIES[g] for g in grp.index], height=0.6)
    for g, v in grp.items():
        ax3.text(v / 1e3 + grp.max() / 1e3 * 0.02, g, f"{v/1e3:,.0f}k", va="center",
                 color=INK2, fontsize=8.5)
    ax3.set_xlim(0, grp.max() / 1e3 * 1.25)
    ax3.grid(axis="y", visible=False)

    fig.suptitle("Testing the bundling rule: dominant-owner-with-threshold on TreeMap-imputed stands",
                 color=INK, fontsize=11.5, x=0.005, ha="left", y=1.03)
    caption(fig, "Threshold is the dominant owner's share of the stand's whole painted footprint, per "
                 "docs/superpowers/specs/2026-07-17-orchestrator-sketch.md. A TreeMap stand is an imputed FIA plot "
                 "painted onto many scattered pixels, so its footprint straddles owners by construction. Pixel-share "
                 "allocation (right) keeps the whole AOI and is the workable alternative to a hard per-stand "
                 "assignment; see notes/ownership-bundling-pixel-share.md.")
    save(fig, "fig7_bundle_threshold.png", outdir)
    return {"kept_70_stands": int(kept_stands[i70]), "n_stands": int(len(share)),
            "kept_70_acres_frac": float(kept_acres[i70] / stand_acres.sum()),
            "kept_70_stands_classified_only": int(kept_stands_cls[i70]),
            "unclassified_acre_frac": float(1 - classified.sum() / stand_acres.sum())}


def fig_gate_scorecard(outdir):
    """The week's mechanism result: harvest injection is exact, and restart-safe."""
    fig, (ax1, ax2, ax3) = figure(1, 3, figsize=(13.5, 4.4),
                                  gridspec_kw={"wspace": 0.38, "width_ratios": [1, 1.15, 1.15]})

    style(ax1, title=f"A 30% thin at {GATE['cut_year']}: what was removed",
          ylabel="retained fraction after the cut")
    metrics = ["Tpa", "BA"]
    retained = [GATE["post"][m] / GATE["pre"][m] for m in metrics]
    ax1.bar(metrics, retained, color=[BLUE, AQUA], width=0.5, zorder=3)
    ax1.hlines(1 - GATE["target_prop"], -0.6, 1.45, color=INK3, lw=1.4, ls="--", zorder=4)
    ax1.text(1.5, 0.70, "target\n0.700", color=INK3, fontsize=8.5, va="center")
    for i, (m, v) in enumerate(zip(metrics, retained)):
        ax1.text(i, v + 0.02, f"{v:.3f}", ha="center", color=INK2, fontsize=9)
    ax1.set_ylim(0, 1.05)
    ax1.set_xlim(-0.6, 2.3)

    # Three mechanisms agreeing to the last digit is a statement, not a magnitude:
    # a bar chart of zeros would be an empty panel, so draw the chain instead.
    ax2.set_title("The three cut mechanisms agree exactly", color=INK, fontsize=10,
                  loc="left", pad=8)
    ax2.set_facecolor(SURFACE)
    ax2.axis("off")
    T = ax2.transAxes
    ax2.set_xlim(0, 1)
    ax2.set_ylim(0, 1)
    arms = [("G1", "native ThinDBH keyword"), ("G2", "fvsCutNow in-process"),
            ("G3", "fvsAddActivity after a restart")]
    ys = [0.86, 0.52, 0.18]
    for (tag, desc), y in zip(arms, ys):
        ax2.plot([0.04], [y], marker="o", ms=10, color=BLUE, mfc=SURFACE, mew=2,
                 transform=T, clip_on=False, zorder=4)
        ax2.text(0.11, y, tag, color=INK, fontsize=12, va="center", weight="bold", transform=T)
        ax2.text(0.23, y, desc, color=INK2, fontsize=9.5, va="center", transform=T)
    for (_, v), y in zip(GATE["arm_deltas"], [0.69, 0.35]):
        ax2.plot([0.04, 0.04], [y - 0.14, y + 0.14], color=GOOD, lw=3, transform=T,
                 solid_capstyle="butt", zorder=3)
        ax2.text(0.11, y, f"max |Δ| = {v:.1f}  on every summary metric",
                 color=GOOD, fontsize=9.5, va="center", transform=T)
    ax2.text(0.0, -0.06, f"Fixture stand {GATE['stand']}, SN variant; arms joined on "
             "(StandID, Year, RmvCode)", color=INK3, fontsize=8, transform=T, va="top")

    style(ax3, title="Per-stand targeting inside a 2-stand bundle", ylabel="trees per acre")
    b = GATE["bundle"]
    x = [0, 1]
    ax3.plot(x, [b["target_pre"], b["target_post"]], color=BLUE, lw=2.4, marker="o",
             ms=8, mfc=SURFACE, mew=1.8, zorder=4)
    ax3.plot(x, [b["target_pre"], b["target_pre"]], color=ORANGE, lw=2.4, ls="--",
             marker="s", ms=7, mfc=SURFACE, mew=1.8, zorder=3)
    ax3.text(1.03, b["target_post"], f" cut stand …{b['target_id'][-5:]}\n "
             f"{b['target_post']/b['target_pre']:.3f} retained", color=BLUE, fontsize=8.5, va="center")
    ax3.text(1.03, b["target_pre"], f" untouched stand …{b['other_id'][-5:]}\n "
             f"max |Δ| vs no-cut baseline = {b['other_max_delta']:.1f}", color=ORANGE,
             fontsize=8.5, va="center")
    ax3.set_xticks(x)
    ax3.set_xticklabels(["before barrier", "after barrier"])
    ax3.set_xlim(-0.15, 2.05)
    ax3.set_ylim(1000, 1800)

    fig.suptitle("Management-injection gate — PASSED 2026-07-17", color=INK, fontsize=11.5,
                 x=0.005, ha="left", y=1.03)
    caption(fig, "Measured values from research/restart_fidelity/outputs/gate_cut_injection.txt "
                 "(not R2 data — this is the spike's own output). G1 native ThinDBH keyword ≡ G2 fvsCutNow "
                 "in-process ≡ G3 fvsAddActivity after a stop/restart barrier, exact on stand values.")
    save(fig, "fig8_gate_scorecard.png", outdir)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cache", type=Path, default=REPO / "data/interim/r2_cache",
                    help="where R2 inputs are cached")
    ap.add_argument("--outdir", type=Path, default=HERE, help="where the PNGs go")
    args = ap.parse_args()

    print("Fetching R2 inputs")
    tpo = load_tpo(fetch(args.cache, R2_INPUTS["tpo"]))
    acres = load_stand_acres(args.cache)

    print("Building figures")
    facts = {}
    facts |= fig_even_flow_targets(tpo, args.outdir)
    fig_harvest_by_county(tpo, args.outdir)
    facts |= fig_supply_vs_demand(args.cache, tpo, acres, args.outdir)
    facts |= fig_barrier_alignment(args.cache, args.outdir)
    fig_baseline_ensemble(args.cache, args.outdir)
    fig_ownership_map(args.cache, acres, args.outdir)
    facts |= fig_bundle_threshold(acres, args.outdir)
    fig_gate_scorecard(args.outdir)

    print("\nHeadline numbers")
    for k, v in facts.items():
        print(f"  {k:22s} {v}")


if __name__ == "__main__":
    main()
