"""
Render the riparian (SMZ) map for the five-county pilot.

Two panels, both drawn from the same geometry `make_riparian_overlay.py` measured:

  A. The pilot AOI — forested TreeMap pixels in grey, the pixels inside a Florida BMP
     stream-management zone picked out, county boundaries labelled.
  B. The densest 3 km window in the AOI, at buffer resolution — the disjoint BMP buffer
     polygons by class, over the flowlines that generated them, over the forest mask.
     The window is chosen deterministically: the 3 km box with the most SMZ pixels.

Inputs are the staged TreeMap raster, the county shapefile, and the two GeoPackages the
overlay driver cached under `data/interim/` (buffers and clipped flowlines). No R2 access
and no ownership raster are needed — this is the geometry panel, not the attribution.

Usage:
    uv run python weekly-artifact/2026-08-24/make_map.py
"""

from __future__ import annotations

import importlib.util
import logging
import sys
from pathlib import Path

import geopandas as gpd
import matplotlib
import numpy as np
import pandas as pd

matplotlib.use("Agg")
import matplotlib.lines as mlines  # noqa: E402
import matplotlib.patches as mpatches  # noqa: E402
import matplotlib.patheffects as pe  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import rasterio  # noqa: E402
from rasterio.features import rasterize  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
log = logging.getLogger("make_map")

OUT_DIR = Path(__file__).resolve().parent


def _load(relpath: str, name: str):
    spec = importlib.util.spec_from_file_location(name, REPO / relpath)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


OVERLAY = _load("weekly-artifact/2026-08-24/make_riparian_overlay.py", "overlay_20260824")

# Categorical hues in fixed order, validated CVD-safe (see the artifact README).
C_EPHEMERAL = "#E69F00"
C_PERENNIAL = "#0072B2"
C_PERENNIAL_LG = "#009E73"
C_FOREST = "#c8ccc4"
C_STREAM = "#4a6fa5"
INK = "#22252a"
MUTED = "#6b7280"
SURFACE = "#fcfcfb"
WINDOW_M = 3000.0


def grids():
    """Forest mask and SMZ class grid on the TreeMap raster grid."""
    with rasterio.open(OVERLAY.PRIOR_10.TREEMAP_TIF) as src:
        tm = src.read(1)
        profile = src.profile
        nodata = src.nodata
        bounds = src.bounds

    counties = OVERLAY.aoi_counties().to_crs(profile["crs"])
    county_grid = rasterize(
        ((geom, 1) for geom in counties.geometry),
        out_shape=tm.shape, transform=profile["transform"], fill=0, dtype="uint8",
    )
    forest = (tm != nodata) & (county_grid > 0)

    buffers = gpd.read_file(OVERLAY.SMZ_LAYER_CACHE).to_crs(profile["crs"])
    codes = {"ephemeral_intermittent": 1, "perennial_small": 2, "perennial_large": 3}
    smz = rasterize(
        ((geom, codes[cls]) for geom, cls in zip(buffers.geometry, buffers["buffer_class"])),
        out_shape=tm.shape, transform=profile["transform"], fill=0, dtype="uint8",
    )
    return forest, smz, profile, bounds, counties, buffers


def densest_window(smz: np.ndarray, profile) -> tuple[float, float]:
    """Centre of the WINDOW_M box holding the most SMZ pixels — deterministic, no seed."""
    res = profile["transform"].a
    step = int(round(WINDOW_M / res))
    block = (smz > 0).astype("int32")
    # Box-sum by integral image, then take the first argmax (ties resolve to the
    # north-westmost window, so the choice is reproducible).
    integral = block.cumsum(0).cumsum(1)
    integral = np.pad(integral, ((1, 0), (1, 0)))
    sums = (integral[step:, step:] - integral[:-step, step:]
            - integral[step:, :-step] + integral[:-step, :-step])
    row, col = np.unravel_index(int(np.argmax(sums)), sums.shape)
    log.info("Densest %.0f m window: %d SMZ pixels at row %d col %d",
             WINDOW_M, int(sums[row, col]), row, col)
    x, y = profile["transform"] * (col + step / 2, row + step / 2)
    return x, y


def scalebar(ax, length_m: float, label: str, pad=0.04):
    x0, x1 = ax.get_xlim()
    y0, y1 = ax.get_ylim()
    x = x0 + (x1 - x0) * pad
    y = y0 + (y1 - y0) * pad
    ax.plot([x, x + length_m], [y, y], color=INK, lw=2.5, solid_capstyle="butt", zorder=6)
    ax.text(x + length_m / 2, y + (y1 - y0) * 0.012, label, ha="center", va="bottom",
            fontsize=8, color=INK, zorder=6)


def headline_numbers() -> dict:
    """Acreages for the titles and legend, read from the committed summary CSVs."""
    by_class = pd.read_csv(OUT_DIR / "smz_by_buffer_class.csv").set_index("buffer_class")
    delta = pd.read_csv(OUT_DIR / "library_riparian_delta.csv").set_index("scenario")
    smz_ac = float(delta.loc["riparian_stands", "riparian_acres"])
    total_ac = float(delta.loc["riparian_stands", "acres"])
    # perennial_large is declared in bmp_rules.yaml but unreachable from the current
    # classifier, so it may be absent from the summary CSV entirely — default to 0.
    class_ac = by_class["smz_acres"]
    return {
        "smz_ac": smz_ac,
        "total_ac": total_ac,
        "smz_pct": 100 * smz_ac / total_ac,
        "perennial_ac": float(class_ac.get("perennial_small", 0.0)),
        "perennial_lg_ac": float(class_ac.get("perennial_large", 0.0)),
        "ephemeral_ac": float(class_ac.get("ephemeral_intermittent", 0.0)),
    }


def main() -> None:
    n = headline_numbers()
    forest, smz, profile, bounds, counties, buffers = grids()
    extent = (bounds.left, bounds.right, bounds.bottom, bounds.top)

    smz_forest = np.where(forest & (smz > 0), smz, 0)
    log.info("Forest pixels %d; SMZ forest pixels %d (%.2f%%)",
             int(forest.sum()), int((smz_forest > 0).sum()),
             100 * (smz_forest > 0).sum() / forest.sum())

    fig = plt.figure(figsize=(15.5, 8.6), facecolor=SURFACE)
    gs = fig.add_gridspec(1, 2, width_ratios=[1.45, 1.0], wspace=0.06,
                          left=0.03, right=0.985, top=0.855, bottom=0.06)
    axa = fig.add_subplot(gs[0, 0])
    axb = fig.add_subplot(gs[0, 1])

    # ---- Panel A: the AOI -------------------------------------------------------------
    axa.imshow(np.ma.masked_where(~forest, forest), extent=extent, origin="upper",
               cmap=matplotlib.colors.ListedColormap([C_FOREST]), interpolation="nearest")
    for code, color in ((1, C_EPHEMERAL), (2, C_PERENNIAL), (3, C_PERENNIAL_LG)):
        layer = np.ma.masked_where(smz_forest != code, smz_forest)
        axa.imshow(layer, extent=extent, origin="upper",
                   cmap=matplotlib.colors.ListedColormap([color]), interpolation="nearest")
    counties.boundary.plot(ax=axa, color=INK, lw=0.9, zorder=4)
    for geom, name in zip(counties.geometry, counties["NAME"]):
        pt = geom.representative_point()
        axa.annotate(name.upper(), (pt.x, pt.y), ha="center", va="center", fontsize=9.5,
                     color=INK, zorder=5,
                     path_effects=[pe.withStroke(linewidth=3, foreground="white")])
    # The mask highlights every forested SMZ pixel; the attributed total additionally
    # drops non-forest/water/unknown ownership classes, so title both numbers.
    drawn_ac = float((smz_forest > 0).sum()) * OVERLAY.PRIOR_10.ACRES_PER_PIXEL
    axa.set_title("A · Forested land inside a Florida BMP stream-management zone\n"
                  f"{drawn_ac:,.0f} ac highlighted, of which {n['smz_ac']:,.0f} of "
                  f"{n['total_ac']:,.0f} attributed acres ({n['smz_pct']:.2f}%) "
                  "survive the ownership screen",
                  fontsize=11.5, color=INK, loc="left", pad=8)
    scalebar(axa, 20000, "20 km")

    # ---- Panel B: the densest 3 km window ---------------------------------------------
    cx, cy = densest_window(smz, profile)
    half = WINDOW_M / 2
    win = (cx - half, cx + half, cy - half, cy + half)
    axa.add_patch(mpatches.Rectangle((win[0], win[2]), WINDOW_M, WINDOW_M, fill=False,
                                     ec=INK, lw=1.6, zorder=6))

    axb.imshow(np.ma.masked_where(~forest, forest), extent=extent, origin="upper",
               cmap=matplotlib.colors.ListedColormap([C_FOREST]), interpolation="nearest")
    streams = gpd.read_file(OVERLAY.STREAM_LAYER_CACHE).to_crs(profile["crs"])
    streams.plot(ax=axb, color=C_STREAM, lw=0.9, zorder=3)
    for cls, color in (("ephemeral_intermittent", C_EPHEMERAL), ("perennial_small", C_PERENNIAL),
                       ("perennial_large", C_PERENNIAL_LG)):
        sub = buffers[buffers["buffer_class"] == cls]
        if len(sub):
            sub.plot(ax=axb, facecolor=color, edgecolor="none", alpha=0.85, zorder=2)
    axb.set_xlim(win[0], win[1])
    axb.set_ylim(win[2], win[3])
    axb.set_title("B · The densest 3 km window, at buffer resolution\n"
                  "1,617 of 3,650 flowline km carry an FCode the classifier does not buffer",
                  fontsize=11.5, color=INK, loc="left", pad=8)
    scalebar(axb, 500, "500 m")

    for ax in (axa, axb):
        ax.set_xticks([])
        ax.set_yticks([])
        ax.set_facecolor(SURFACE)
        for spine in ax.spines.values():
            spine.set_color("#d5d8d2")

    handles = [
        mpatches.Patch(facecolor=C_PERENNIAL,
                       label=f"Perennial stream buffer — 50 ft ({n['perennial_ac']:,.0f} ac)"),
        mpatches.Patch(facecolor=C_EPHEMERAL,
                       label=f"Ephemeral / intermittent buffer — 35 ft ({n['ephemeral_ac']:,.0f} ac)"),
        mpatches.Patch(facecolor=C_FOREST, label="Forested, outside every BMP buffer"),
        mlines.Line2D([], [], color=C_STREAM, lw=1.4,
                      label="NHD flowline — bare where its FCode gets no BMP class"),
    ]
    # Today classify_stream_fcode never emits perennial_large (the documented classifier
    # gap), so the class shows up only once a buffer layer or summary actually carries it.
    if n["perennial_lg_ac"] > 0 or (buffers["buffer_class"] == "perennial_large").any():
        handles.insert(0, mpatches.Patch(
            facecolor=C_PERENNIAL_LG,
            label=f"Perennial large buffer — 75 ft ({n['perennial_lg_ac']:,.0f} ac)"))
    fig.legend(handles=handles, loc="upper left", bbox_to_anchor=(0.03, 0.945), ncol=4,
               frameon=False, fontsize=9.5, labelcolor=INK, handlelength=1.6)
    fig.suptitle("ARTEMIS pilot — the riparian layer, joined for the first time",
                 x=0.03, y=0.975, ha="left", fontsize=14, color=INK, weight="semibold")
    fig.text(0.985, 0.012,
             "NHD flowlines (EPA NHDPlus snapshot 2022, FL) × config/bmp_rules.yaml buffer widths, "
             "rasterised onto the TreeMap 2022 30 m grid · pipeline.s3_management.sketch_management_units",
             ha="right", va="bottom", fontsize=8, color=MUTED)

    out = OUT_DIR / "riparian_map.png"
    fig.savefig(out, dpi=190, facecolor=SURFACE)
    log.info("Wrote %s", out)


if __name__ == "__main__":
    main()
