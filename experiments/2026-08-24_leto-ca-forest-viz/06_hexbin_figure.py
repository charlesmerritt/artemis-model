"""Hexbin overlay on the end-of-simulation (2072) basal-area map.

A single-panel figure: the t=50 stand-level BA map (same rendering as the
main figure's final panel, minus the harvest overlay) with a hexagonal
aggregation layer on top — each hex is the mean projected BA of the 30 m
cells it covers. The hex lattice is the kind of sub-unit regionalization the
simulated-annealing scheduler will optimize even-flow over; seeing the 2072
landscape averaged to that scale is the point of the overlay.

Usage:
    uv run python experiments/.../06_hexbin_figure.py [--policy heuristic]
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from common import INV_YEAR, OUTPUTS, STREAMS_SHP, WORK  # noqa: E402

# 05_figure.py starts with a digit, so pull its helpers in via importlib.
_spec = importlib.util.spec_from_file_location("figure_mod", HERE / "05_figure.py")
figure_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(figure_mod)

HEX_KM_ACROSS = 1.0   # target hex width; ~a stand neighbourhood / sub-unit


def main() -> None:
    import argparse

    import geopandas as gpd
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.colors as mcolors
    import matplotlib.pyplot as plt

    from affine import Affine

    parser = argparse.ArgumentParser()
    parser.add_argument("--policy", choices=("default", "random", "heuristic"),
                        default="heuristic")
    args = parser.parse_args()
    suffix = "" if args.policy == "default" else f"_{args.policy}"

    seg = np.load(WORK / "segmentation.npz")
    mu_labels = seg["mu_labels"]
    mu = pd.read_csv(WORK / "mu_summary.csv")
    summary = pd.read_csv(WORK / f"fvs_summary2{suffix}.csv", dtype={"MU_ID": str})
    summary["MU_ID"] = summary["MU_ID"].astype(int)
    meta = json.loads((WORK / "staged" / "aoi_meta.json").read_text())
    a, b, c, d, e, f = meta["transform"]
    h, w = mu_labels.shape
    extent = (c, c + a * w, f + e * h, f)
    transform = Affine(a, b, c, d, e, f)

    year = INV_YEAR + 50
    ba = figure_mod.ba_by_year(summary)
    fallback_ba = dict(zip(mu["MU_ID"], mu["BALIVE_MEAN"]))
    ba_of_mu = np.zeros(int(mu_labels.max()) + 1)
    for mu_id in mu["MU_ID"]:
        if mu_id in ba.index and year in ba.columns and np.isfinite(ba.at[mu_id, year]):
            ba_of_mu[int(mu_id)] = ba.at[mu_id, year]
        else:
            ba_of_mu[int(mu_id)] = fallback_ba.get(mu_id, 0.0)

    # Stand-level underlay, exactly as the main figure's final panel renders it
    default_rgb = np.array(figure_mod._hex_to_rgb(figure_mod.BACKGROUND))
    vals = {int(m): figure_mod.ba_to_color(np.array(ba_of_mu[int(m)]))
            for m in mu["MU_ID"]}
    img = figure_mod.lookup_image(mu_labels, vals, default_rgb)

    units = figure_mod.polygonize_units(mu_labels, transform)
    units = units.merge(mu[["MU_ID", "OWNER_CLASS", "MGMT_CLASS"]], on="MU_ID")
    upland = units[units["MGMT_CLASS"] == 0]
    rip_corridor = units[units["MGMT_CLASS"] == 1].dissolve()
    stream = gpd.read_file(STREAMS_SHP).to_crs("EPSG:5070")
    stream = stream[stream["fcode"] == 55800]
    stream = stream.clip((extent[0], extent[2], extent[1], extent[3]))

    fig, ax = plt.subplots(figsize=(11, 12.5), dpi=170)
    fig.patch.set_facecolor("white")
    ax.imshow(img, extent=extent, interpolation="nearest")
    upland.boundary.plot(ax=ax, linewidth=0.25, color=figure_mod.STAND_EDGE,
                         alpha=0.8, zorder=3)
    rip_corridor.boundary.plot(ax=ax, linewidth=0.35, color="#2e5f7a",
                               alpha=0.7, zorder=4)
    if len(stream):
        stream.plot(ax=ax, color=figure_mod.STREAM_COLOR, linewidth=1.0, zorder=5)

    # Hexbin: mean projected BA per hex over the forested cells. Cell centres
    # in map coordinates; nonforest cells are excluded so a hex's mean is a
    # mean over forest, not diluted by roads and fields.
    rows, cols = np.nonzero(mu_labels > 0)
    xs = c + (cols + 0.5) * a
    ys = f + (rows + 0.5) * e
    cvals = ba_of_mu[mu_labels[rows, cols]]
    gridsize = max(4, int(round((extent[1] - extent[0]) / (HEX_KM_ACROSS * 1000))))
    cmap = mcolors.LinearSegmentedColormap.from_list("ba", figure_mod.BA_RAMP)
    hb = ax.hexbin(xs, ys, C=cvals, reduce_C_function=np.mean,
                   gridsize=gridsize, extent=(extent[0], extent[1],
                                              extent[2], extent[3]),
                   cmap=cmap, vmin=0, vmax=figure_mod.BA_MAX, alpha=0.72,
                   edgecolors="white", linewidths=1.2, zorder=6, mincnt=1)

    figure_mod.draw_north_arrow_and_scale(ax, extent)
    ax.set_xlim(extent[0], extent[1])
    ax.set_ylim(extent[2], extent[3])
    ax.set_xticks([])
    ax.set_yticks([])
    for s in ax.spines.values():
        s.set_visible(False)
    ax.set_title(f"End of simulation ({year}) — mean projected BA per "
                 f"~{HEX_KM_ACROSS:g} km hex", fontsize=14, fontweight="bold",
                 pad=26)
    ax.text(0.5, 1.006,
            f"{args.policy} harvest policy · hexes average the FVSsn "
            "stand projections — the sub-unit scale the annealing scheduler "
            "will balance even-flow over",
            transform=ax.transAxes, ha="center", va="bottom",
            fontsize=10.5, color="#444444")

    cb = fig.colorbar(hb, ax=ax, orientation="horizontal", fraction=0.04,
                      pad=0.03, aspect=38)
    cb.set_label("Live basal area (sq ft / acre) — hex mean and stand fill "
                 "share this scale", fontsize=10.5, fontweight="bold")
    cb.ax.tick_params(labelsize=9)

    OUTPUTS.mkdir(exist_ok=True)
    out = OUTPUTS / f"leto_artemis_hexbin_2072{suffix}.png"
    fig.savefig(out, dpi=170, facecolor="white", bbox_inches="tight")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
