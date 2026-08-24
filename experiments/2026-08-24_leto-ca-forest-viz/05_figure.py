"""Render the four-panel LETO stands / ARTEMIS-FVS projection figure.

Panels, mirroring the mockup:
    1. LETO stands and their owner class (Harris et al. 2025 ownership,
       majority per CA stand), with the riparian buffer corridor.
    2. BA per acre at t=0  (2022) — initial TreeMap/FIA state.
    3. BA per acre at t=25 (2047) — with stands harvested in the last
       5 years overlaid (clearcut / thin classes).
    4. BA per acre at t=50 (2072) — end of simulation, same overlay.

BA values are FVSsn FVS_Summary2 trajectories (post-removal state in harvest
years). Management units that never received a runnable tree list (nonstocked
donors) hold their TreeMap BALIVE.

Rendering: stand boundaries are drawn as vectorized polygon outlines
(rasterio.features.shapes), not per-pixel masks — hairline anti-aliased
borders instead of raster salt-and-pepper. Riparian management units are
their own stands: on the BA panels they carry their projected basal area
like any other unit (they grow untouched — the no-entry override), and a
blue outline marks them; the ownership panel fills them pale blue to show
the buffer itself. Owner-class boundaries get a heavier line on panel 1.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (
    CELL_ACRES,
    FLOWLINE_BUFFER_RULES_FT,
    INV_YEAR,
    OUTPUTS,
    STREAMS_SHP,
    WORK,
)

STAND_EDGE = "#2b2b2b"     # upland management-unit outline
OWNER_EDGE = "#000000"     # owner-class boundary (panel 1, heavier)
RIPARIAN_EDGE = "#1a6fae"  # riparian-unit outline — their own stands

OWNER_COLORS = {  # mockup-anchored; CVD-separation validated (see README)
    "private_family": "#8f1d33",
    "private_industrial": "#dfae19",
    "federal": "#3d55c9",
    "state": "#c273b3",
    "local": "#0f8a6d",
    "unknown": "#a3a3a3",
}
OWNER_LABELS = {
    "private_family": "Private family (small)",
    "private_industrial": "Private industrial / corporate",
    "federal": "Federal (Osceola NF)",
    "state": "State (Big Shoals SF)",
    "local": "Local government",
    "unknown": "Unknown forest",
}
HARVEST_COLORS = {
    "clearcut": "#6d1021",
    "thin_light": "#e07b28",
    "thin_heavy": "#d43a2f",
}
BACKGROUND = "#f4f2ec"   # non-forest / outside TreeMap
RIPARIAN_TINT = "#ddeef5"
STREAM_COLOR = "#3fa8e0"
BA_MAX = 180.0           # sq ft/acre at the dark end of the ramp (p90 at 2072 ≈ 210)
RIVER_FCODES = (55800, 46006)  # channels drawn as lines; all rules still buffer

BA_RAMP = ["#eef7e2", "#cfe8b5", "#a9d67f", "#7fc258", "#54a83c",
           "#357f2b", "#1f5c20", "#123f16"]


def _hex_to_rgb(h):
    return np.array([int(h[i:i + 2], 16) for i in (1, 3, 5)], dtype=np.float64) / 255.0


def ba_to_color(ba: np.ndarray) -> np.ndarray:
    """Map BA (sq ft/ac) onto the single-hue green ramp."""
    stops = np.array([_hex_to_rgb(h) for h in BA_RAMP])
    t = np.clip(ba / BA_MAX, 0, 1) * (len(stops) - 1)
    lo = np.floor(t).astype(int)
    hi = np.minimum(lo + 1, len(stops) - 1)
    frac = (t - lo)[..., None]
    return stops[lo] * (1 - frac) + stops[hi] * frac


def lookup_image(mu_labels: np.ndarray, values: dict[int, np.ndarray],
                 default: np.ndarray) -> np.ndarray:
    """Paint per-MU RGB values onto the label raster."""
    max_id = int(mu_labels.max())
    lut = np.tile(default, (max_id + 1, 1))
    for mu_id, rgb in values.items():
        if 0 < mu_id <= max_id:
            lut[mu_id] = rgb
    return lut[mu_labels]


def polygonize_units(mu_labels: np.ndarray, transform) -> "gpd.GeoDataFrame":
    """Vectorize the MU raster: one (multi)polygon per management unit."""
    import geopandas as gpd
    from rasterio import features
    from shapely.geometry import shape as to_shape

    shapes = features.shapes(mu_labels.astype(np.int32),
                             mask=mu_labels > 0, transform=transform)
    records = [{"MU_ID": int(v), "geometry": to_shape(g)} for g, v in shapes]
    gdf = gpd.GeoDataFrame(records, crs="EPSG:5070")
    return gdf.dissolve(by="MU_ID", as_index=False)


def ba_by_year(summary: pd.DataFrame) -> pd.DataFrame:
    """Post-removal BA per (MU_ID, Year): the max-RmvCode row of each year."""
    s = summary.sort_values("RmvCode").drop_duplicates(["StandID", "Year"], keep="last")
    return s.pivot(index="MU_ID", columns="Year", values="BA")


def harvest_class(summary: pd.DataFrame, year: int, window: int = 5) -> pd.Series:
    """Mockup legend classes for removals within the last `window` years.

    Cycle years fall every 5 years, so the inclusive window [year-5, year]
    captures the snapshot cycle and the one before it — a cut at year-5 is
    "5 years ago" at the snapshot.
    """
    s = summary[(summary["RmvCode"] == 1)
                & (summary["Year"] >= year - window) & (summary["Year"] <= year)]
    post = summary[summary["RmvCode"] == 2].set_index(["StandID", "Year"])["BA"]
    out = {}
    for r in s.itertuples(index=False):
        ba_before = r.BA
        ba_after = post.get((r.StandID, r.Year), np.nan)
        if not np.isfinite(ba_before) or ba_before <= 0 or not np.isfinite(ba_after):
            continue
        frac = 1.0 - ba_after / ba_before
        if frac >= 0.90:
            out[r.MU_ID] = "clearcut"
        elif frac > 0.30:
            out[r.MU_ID] = "thin_heavy"
        elif frac >= 0.10:
            out[r.MU_ID] = "thin_light"
    return pd.Series(out, name="harvest")


def main() -> None:
    import geopandas as gpd
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D
    from matplotlib.patches import Patch
    import matplotlib.colors as mcolors
    import matplotlib.patheffects as patheffects

    from affine import Affine

    seg = np.load(WORK / "segmentation.npz")
    mu_labels, riparian = seg["mu_labels"], seg["riparian"]
    mu = pd.read_csv(WORK / "mu_summary.csv")
    summary = pd.read_csv(WORK / "fvs_summary2.csv", dtype={"MU_ID": str})
    summary["MU_ID"] = summary["MU_ID"].astype(int)
    meta = json.loads((WORK / "staged" / "aoi_meta.json").read_text())
    a, b, c, d, e, f = meta["transform"]
    h, w = mu_labels.shape
    extent = (c, c + a * w, f + e * h, f)

    units = polygonize_units(mu_labels, Affine(a, b, c, d, e, f))
    units = units.merge(mu[["MU_ID", "OWNER_CLASS", "MGMT_CLASS"]], on="MU_ID")
    upland = units[units["MGMT_CLASS"] == 0]
    rip_units = units[units["MGMT_CLASS"] == 1]
    # One corridor outline, not 1,523 fragment outlines: the dissolved
    # riparian footprint reads as its own stand without webbing the map.
    rip_corridor = rip_units.dissolve()
    owner_bounds = units.dissolve(by="OWNER_CLASS")
    print(f"{len(units):,} unit polygons ({len(rip_units):,} riparian)")

    ba = ba_by_year(summary)
    snap_years = (INV_YEAR, INV_YEAR + 25, INV_YEAR + 50)
    ran = set(ba.index)
    fallback_ba = dict(zip(mu["MU_ID"], mu["BALIVE_MEAN"]))
    print(f"{len(ran):,} FVS-projected units; "
          f"{mu['MU_ID'].nunique() - len(ran):,} hold TreeMap BALIVE (nonstocked donors)")

    default_rgb = np.array(_hex_to_rgb(BACKGROUND))
    stream = gpd.read_file(STREAMS_SHP).to_crs("EPSG:5070")
    stream = stream[stream["fcode"].isin(RIVER_FCODES)]
    stream = stream.clip((extent[0], extent[2], extent[1], extent[3]))

    owner_rgb = {int(r.MU_ID): _hex_to_rgb(OWNER_COLORS[r.OWNER_CLASS])
                 for r in mu.itertuples(index=False)}

    fig, axes = plt.subplots(1, 4, figsize=(24, 7.8), dpi=170)
    fig.patch.set_facecolor("white")

    def finish(ax, img, title, subtitle, owner_edges=False, minor_streams=True):
        ax.imshow(img, extent=extent, interpolation="nearest")
        # Vector stand borders: hairline anti-aliased outlines instead of a
        # per-pixel boundary mask (which reads as salt-and-pepper at this
        # stand density).
        upland.boundary.plot(ax=ax, linewidth=0.25, color=STAND_EDGE,
                             alpha=0.8, zorder=3)
        # The riparian corridor is its own stand: a muted blue-gray hairline
        # marks it without webbing the map (the buffer follows the entire
        # dendritic network, so a bright/thick outline overwhelms the fill).
        rip_lw, rip_color, rip_alpha = ((0.7, RIPARIAN_EDGE, 1.0) if owner_edges
                                        else (0.35, "#2e5f7a", 0.7))
        rip_corridor.boundary.plot(ax=ax, linewidth=rip_lw, color=rip_color,
                                   alpha=rip_alpha, zorder=4)
        if owner_edges:
            owner_bounds.boundary.plot(ax=ax, linewidth=0.9, color=OWNER_EDGE,
                                       zorder=5)
        river = stream[stream["fcode"] == 55800]
        peren = stream[stream["fcode"] != 55800]
        if len(river):
            river.plot(ax=ax, color=STREAM_COLOR,
                       linewidth=1.6 if minor_streams else 1.0, zorder=6)
        # The minor perennial network is context on the ownership/start
        # panels; on the projection panels it would cover the very riparian
        # growth the corridor outline exists to show.
        if minor_streams and len(peren):
            peren.plot(ax=ax, color=STREAM_COLOR, linewidth=0.6, zorder=6)
        ax.set_xlim(extent[0], extent[1])
        ax.set_ylim(extent[2], extent[3])
        ax.set_title(title, fontsize=15, fontweight="bold", pad=10)
        ax.text(0.5, -0.045, subtitle, transform=ax.transAxes,
                ha="center", fontsize=12)
        ax.set_xticks([])
        ax.set_yticks([])
        for s in ax.spines.values():
            s.set_visible(False)

    # -- Panel 1: ownership ------------------------------------------------
    img = lookup_image(mu_labels, owner_rgb, default_rgb)
    img[riparian] = _hex_to_rgb(RIPARIAN_TINT)
    finish(axes[0], img, "LETO stands and their owner class",
           "cellular-automata segmentation · riparian buffer in pale blue",
           owner_edges=True)

    # scale bar: 2 km
    x0, y0 = extent[0] + 800, extent[2] + 900
    axes[0].plot([x0, x0 + 2000], [y0, y0], color="black", lw=3,
                 path_effects=[patheffects.withStroke(linewidth=5, foreground="white")])
    axes[0].text(x0 + 1000, y0 + 260, "2 km", ha="center", fontsize=9,
                 bbox=dict(facecolor="white", edgecolor="none", pad=1, alpha=0.8))

    # -- Panels 2-4: BA ----------------------------------------------------
    ba_titles = ["Start", "“1st harvest” era", "End of simulation"]
    for i, year in enumerate(snap_years):
        vals = {}
        for mu_id in mu["MU_ID"]:
            if mu_id in ran and year in ba.columns and np.isfinite(ba.at[mu_id, year]):
                v = ba.at[mu_id, year]
            else:
                v = fallback_ba.get(mu_id, 0.0)
            vals[int(mu_id)] = ba_to_color(np.array(v))
        if year > INV_YEAR:
            hc = harvest_class(summary, year)
            for mu_id, cls in hc.items():
                vals[int(mu_id)] = _hex_to_rgb(HARVEST_COLORS[cls])
        # Riparian units keep their projected BA on every panel — they are
        # their own stands, growing untouched; the blue outline identifies them.
        img = lookup_image(mu_labels, vals, default_rgb)
        finish(axes[i + 1], img, ba_titles[i],
               f"t = {year - INV_YEAR} · {year}",
               minor_streams=(year == INV_YEAR))
        if year > INV_YEAR:
            n_cc = int((hc == "clearcut").sum())
            n_th = int(hc.isin(["thin_light", "thin_heavy"]).sum())
            axes[i + 1].text(0.5, -0.085,
                             f"{n_cc} stands clearcut, {n_th} thinned in the last 5 yr",
                             transform=axes[i + 1].transAxes, ha="center",
                             fontsize=9.5, color="#555555")

    # -- Legends -----------------------------------------------------------
    fig.subplots_adjust(left=0.01, right=0.99, top=0.84, bottom=0.20, wspace=0.03)

    owner_handles = [Patch(facecolor=OWNER_COLORS[k], edgecolor="black",
                           linewidth=0.4, label=OWNER_LABELS[k])
                     for k in OWNER_COLORS]
    owner_handles.append(Patch(facecolor=RIPARIAN_TINT, edgecolor=RIPARIAN_EDGE,
                               linewidth=1.2,
                               label="Riparian stand (no entry, grows untouched)"))
    owner_handles.append(Line2D([0], [0], color=STREAM_COLOR, lw=2,
                                label="NHD flowline"))
    leg1 = fig.legend(handles=owner_handles, loc="lower left",
                      bbox_to_anchor=(0.015, 0.008), ncol=2, fontsize=9.5,
                      title="Owner class (Harris et al. 2025)", title_fontsize=10,
                      frameon=False)
    leg1.get_title().set_fontweight("bold")

    cmap = mcolors.LinearSegmentedColormap.from_list("ba", BA_RAMP)
    cax = fig.add_axes([0.42, 0.075, 0.16, 0.028])
    cb = fig.colorbar(plt.cm.ScalarMappable(
        norm=mcolors.Normalize(0, BA_MAX), cmap=cmap), cax=cax,
        orientation="horizontal")
    cb.set_label("Live basal area (sq ft / acre) — FVSsn projection",
                 fontsize=10, fontweight="bold")
    cb.ax.tick_params(labelsize=9)

    harvest_handles = [
        Patch(facecolor=HARVEST_COLORS["clearcut"],
              label="Clearcut harvested in the last 5 yr"),
        Patch(facecolor=HARVEST_COLORS["thin_light"],
              label="Thinned in the last 5 yr (10–30% BA)"),
        Patch(facecolor=HARVEST_COLORS["thin_heavy"],
              label="Thinned in the last 5 yr (31–90% BA)"),
    ]
    leg2 = fig.legend(handles=harvest_handles, loc="lower right",
                      bbox_to_anchor=(0.99, 0.008), fontsize=9.5,
                      title="Harvest activity (owner-class default regimes)",
                      title_fontsize=10, frameon=False)
    leg2.get_title().set_fontweight("bold")

    fig.suptitle(
        "LETO cellular-automata stands → ARTEMIS owner-class regimes → "
        "FVS Southern variant, 2022–2072\n"
        "White Springs AOI, Columbia County, FL (five-county pilot) · "
        "TreeMap 2022 + FIA tree lists · Suwannee River riparian buffer",
        fontsize=13.5, y=0.99)

    OUTPUTS.mkdir(exist_ok=True)
    out = OUTPUTS / "leto_artemis_forest_viz.png"
    fig.savefig(out, dpi=170, facecolor="white", bbox_inches="tight")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
