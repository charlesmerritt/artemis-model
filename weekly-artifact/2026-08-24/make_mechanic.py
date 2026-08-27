"""
The riparian carve, drawn on real geometry.

Three panels, one window, no schematic — every line is data:

  A. Stands as they are: real parcels intersected with the forest mask, the way Phase 2.3
     delineates management units, with the stream running through them.
  B. The BMP buffer drawn across them. It respects nothing: it cuts parcel boundaries
     wherever it meets them, because it is set by the stream, not by the ownership fabric.
  C. The carve. Inside the buffer the old boundaries are gone and the corridor is one
     stand — grow-only, never entered. The stands it crossed are truncated at its edge.

There is no threshold in any of this. A stand is riparian or it is not; the geometry says
which, and `assign_prescription`'s absolute override then gives the riparian stand exactly
one option. `SMZ_Pct` is a derived label (100 inside, 0 outside), not a dial.

The window is chosen deterministically: centred on the corridor that cuts the most
scheduling units (ties → the largest by area), from `riparian_corridors.csv`.

Usage (from the repo root, with the R2 inputs staged under ./data):

    uv run python weekly-artifact/2026-08-24/make_mechanic.py
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
import matplotlib.pyplot as plt  # noqa: E402
import rasterio  # noqa: E402
from rasterio.features import shapes  # noqa: E402
from shapely.geometry import box, shape  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
log = logging.getLogger("make_mechanic")

OUT_DIR = Path(__file__).resolve().parent
DATA = REPO / "data"
PARCELS = DATA / "interim/parcels/FL_5_Co_Parcels.gdb"
PARCEL_LAYER = "FL_5_Co_Parcels"
CORRIDOR_LABELS = DATA / "interim/riparian_corridor_labels.npy"

WINDOW_M = 900.0

C_CORRIDOR = "#E69F00"     # no-entry riparian, same hue the summary figure uses
C_STREAM = "#0072B2"
C_BUFFER_EDGE = "#5b4b9a"
C_FOREST = "#dfe3da"
INK = "#22252a"
MUTED = "#6b7280"
SURFACE = "#fcfcfb"


def _load(relpath: str, name: str):
    spec = importlib.util.spec_from_file_location(name, REPO / relpath)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


OVERLAY = _load("weekly-artifact/2026-08-24/make_riparian_overlay.py", "overlay_20260824")
PRIOR_10 = OVERLAY.PRIOR_10


def target_window() -> tuple[float, float, int]:
    """Centre of the window, and the id of the corridor it is chosen for."""
    corridors = pd.read_csv(OUT_DIR / "riparian_corridors.csv")
    pick = corridors.sort_values(["units_cut", "acres"], ascending=False).iloc[0]
    corridor_id = int(pick["corridor_id"])
    log.info("Window corridor %d: %.1f ac, cuts %d scheduling units across %d plots",
             corridor_id, pick["acres"], int(pick["units_cut"]), int(pick["plots"]))

    labels = np.load(CORRIDOR_LABELS)
    rows, cols = np.nonzero(labels == corridor_id)
    with rasterio.open(PRIOR_10.TREEMAP_TIF) as src:
        transform = src.transform
    x, y = transform * (cols.mean() + 0.5, rows.mean() + 0.5)
    return float(x), float(y), corridor_id


def forest_polygons(window: box, crs) -> gpd.GeoDataFrame:
    """The forest mask inside the window, vectorised off the TreeMap grid."""
    with rasterio.open(PRIOR_10.TREEMAP_TIF) as src:
        win = rasterio.windows.from_bounds(*window.bounds, transform=src.transform)
        data = src.read(1, window=win)
        transform = src.window_transform(win)
        nodata = src.nodata
    mask = (data != nodata).astype("uint8")
    geoms = [shape(geom) for geom, val in shapes(mask, mask=mask.astype(bool), transform=transform)
             if val == 1]
    return gpd.GeoDataFrame(geometry=geoms, crs=crs).dissolve()


def build_layers():
    cx, cy, corridor_id = target_window()
    half = WINDOW_M / 2
    window = box(cx - half, cy - half, cx + half, cy + half)

    with rasterio.open(PRIOR_10.TREEMAP_TIF) as src:
        crs = src.crs

    forest = forest_polygons(window, crs)

    log.info("Reading parcels over the window")
    window_gdf = gpd.GeoDataFrame(geometry=[window], crs=crs)
    parcels = gpd.read_file(PARCELS, layer=PARCEL_LAYER,
                            mask=window_gdf.to_crs("EPSG:26917")).to_crs(crs)
    parcels = gpd.clip(parcels, window)
    log.info("Parcels in window: %d", len(parcels))

    # A management unit, as Phase 2.3 builds it: parcel ∩ forest.
    units = gpd.overlay(parcels[["PARCELID", "geometry"]], forest, how="intersection")
    units = units[~units.geometry.is_empty].explode(index_parts=False).reset_index(drop=True)

    buffers = gpd.read_file(OVERLAY.SMZ_LAYER_CACHE).to_crs(crs)
    buffers = gpd.clip(buffers, window)
    streams = gpd.read_file(OVERLAY.STREAM_LAYER_CACHE).to_crs(crs)
    streams = gpd.clip(streams, window)

    # The carve: the corridor is forest inside the buffer, dissolved into one stand;
    # every other unit is truncated at its edge.
    corridor = gpd.overlay(forest, buffers.dissolve()[["geometry"]], how="intersection").dissolve()
    managed = gpd.overlay(units, buffers.dissolve()[["geometry"]], how="difference")
    managed = managed[~managed.geometry.is_empty]

    stats = {
        "units_before": len(units),
        "units_after": len(managed),
        "corridor_ac": float(corridor.geometry.area.sum() / 4046.8564224),
        "cut": int((gpd.overlay(units, buffers.dissolve()[["geometry"]],
                                how="intersection").geometry.area > 0).sum()),
    }
    log.info("Window: %d parcel-forest units, %d cut by the buffer, corridor %.1f ac",
             stats["units_before"], stats["cut"], stats["corridor_ac"])
    return window, units, managed, corridor, buffers, streams, stats


def frame(ax, window, title, subtitle):
    ax.set_xlim(window.bounds[0], window.bounds[2])
    ax.set_ylim(window.bounds[1], window.bounds[3])
    ax.set_xticks([])
    ax.set_yticks([])
    ax.set_facecolor(SURFACE)
    for spine in ax.spines.values():
        spine.set_color(INK)
        spine.set_linewidth(1.4)
    ax.set_title(f"{title}\n{subtitle}", loc="left", fontsize=11, color=INK, pad=8)


def main() -> None:
    window, units, managed, corridor, buffers, streams, stats = build_layers()

    fig, axes = plt.subplots(1, 3, figsize=(15.6, 6.8), facecolor=SURFACE)
    fig.subplots_adjust(left=0.02, right=0.98, top=0.74, bottom=0.06, wspace=0.06)

    # ---- A. the stands as they are ----------------------------------------------------
    ax = axes[0]
    units.plot(ax=ax, facecolor=C_FOREST, edgecolor=INK, lw=0.9, zorder=2)
    streams.plot(ax=ax, color=C_STREAM, lw=2.2, zorder=4)
    frame(ax, window, "A · Stands as they are",
          f"{stats['units_before']} parcel × forest units, and the stream")

    # ---- B. the buffer drawn across them ----------------------------------------------
    ax = axes[1]
    units.plot(ax=ax, facecolor=C_FOREST, edgecolor=INK, lw=0.9, zorder=2)
    buffers.dissolve().boundary.plot(ax=ax, color=C_BUFFER_EDGE, lw=1.8, linestyle=(0, (5, 3)),
                                     zorder=5)
    streams.plot(ax=ax, color=C_STREAM, lw=2.2, zorder=4)
    frame(ax, window, "B · The BMP buffer, drawn across them",
          f"set by the stream, not the ownership fabric — it cuts {stats['cut']} of them")

    # ---- C. the carve ------------------------------------------------------------------
    ax = axes[2]
    managed.plot(ax=ax, facecolor=C_FOREST, edgecolor=INK, lw=0.9, zorder=2)
    streams.plot(ax=ax, color=C_STREAM, lw=2.2, zorder=3)
    corridor.plot(ax=ax, facecolor=C_CORRIDOR, edgecolor=INK, lw=1.2, hatch="///",
                  alpha=0.92, zorder=4)
    frame(ax, window, "C · The corridor is its own stand",
          f"{stats['corridor_ac']:.0f} ac, grow-only, boundaries erased inside it")

    handles = [
        mpatches.Patch(facecolor=C_FOREST, edgecolor=INK, label="management unit (parcel × forest)"),
        mpatches.Patch(facecolor=C_CORRIDOR, edgecolor=INK, hatch="////",
                       label="riparian stand — no entry, ever"),
        mlines.Line2D([], [], color=C_BUFFER_EDGE, lw=1.8, linestyle=(0, (5, 3)),
                      label="BMP buffer edge (config/bmp_rules.yaml)"),
        mlines.Line2D([], [], color=C_STREAM, lw=2.2, label="NHD flowline"),
    ]
    fig.legend(handles=handles, loc="upper left", bbox_to_anchor=(0.02, 0.885), ncol=4,
               frameon=False, fontsize=9.5, labelcolor=INK, handlelength=1.8)
    fig.suptitle("Riparian management — the buffer cuts the stand, it is not an attribute of it",
                 x=0.02, y=0.965, ha="left", fontsize=14.5, color=INK, weight="semibold")
    fig.text(0.02, 0.915,
             "Real geometry, 900 m window centred on the corridor that cuts the most "
             "scheduling units. Parcels: FL_5_Co_Parcels · forest mask: TreeMap 2022 · "
             "buffers: NHD flowlines × Florida BMP widths.",
             ha="left", fontsize=9.5, color=MUTED)
    fig.text(0.98, 0.02,
             "No threshold is involved: a stand is 100% riparian or 0%, and the geometry decides.",
             ha="right", fontsize=9, color=MUTED)

    out = OUT_DIR / "riparian_stand_mechanic.png"
    fig.savefig(out, dpi=185, facecolor=SURFACE)
    log.info("Wrote %s", out)


if __name__ == "__main__":
    main()
