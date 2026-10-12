"""Deck figures: FL5 LETO stands at 2022, a zoom where boundaries read, and one stand's tree list.

Slide 1  outputs/fig_fl5_stands.png      parent stands by owner class, zoom box marked
         outputs/fig_zoom_stands.png     the zoom window: management-unit outlines, riparian
Slide 2  outputs/fig_zoom_selected.png   close-up, every unit dimmed but the selected one
         outputs/fig_selected_dbh.png    the stand's FVS tree list as a diameter distribution
         outputs/selected_stand.json     its attributes, stand table and tree rows for the slide

The tree list is LETO v1 stage 4 (``stage4_inventory.build_tree_list``): live FIA TREE rows
(``STATUSCD == 1``, no seedlings) of the retained donors, with ``TREE_COUNT = TPA_UNADJ x
WEIGHT``. LETO writes it under a 1-acre design (``BASAL_AREA_FACTOR -1``, ``INV_PLOT_SIZE 1``,
``BRK_DBH 0``, ``NUM_PLOTS 1``), so ``TREE_COUNT`` is trees per acre as it stands.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import geopandas as gpd
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import rasterio  # noqa: E402
from matplotlib.patches import Rectangle  # noqa: E402
from pyproj import Transformer  # noqa: E402
from shapely.geometry import box  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
import deck_paths as P  # noqa: E402
from pipeline import leto_ca  # noqa: E402
from pipeline.ids import as_id_series  # noqa: E402

BACKGROUND = "#f6f4ee"     # deck surface, outside the five counties
NON_FOREST = "#e4e0d5"
RIPARIAN_FILL = "#9fcbe3"
RIPARIAN_EDGE = "#1a6fae"
OWNER_COLORS = {  # 2026-08-24 palette, corporate darkened to pass the lightness band
    "private_family": "#8f1d33", "private_industrial": "#c99a12", "federal": "#3d55c9",
    "state": "#c273b3", "local": "#0f8a6d", "tribal": "#6a3d9a", "unknown": "#a3a3a3",
}
OWNER_LABELS = {"private_family": "Family", "private_industrial": "Corporate",
                "federal": "Federal", "state": "State", "local": "Local",
                "tribal": "Tribal", "unknown": "Unknown"}
SPECIES_COLORS = ["#2a78d6", "#eb6834", "#1baf7a", "#e87ba4"]
OTHER_COLOR = "#a3a3a3"
MAX_CLASS = 16  # DBH classes above this fold into the last bar
DPI = 200
ZOOM_SCALE = 0.5  # slide-1 zoom: this fraction of the AOI window's width and height
LOBLOLLY_HARDWOOD = 406  # FIA forest type: loblolly pine / hardwood


def hex_rgb(h: str) -> np.ndarray:
    return np.array([int(h[i:i + 2], 16) for i in (1, 3, 5)], dtype=np.float32) / 255


def zoom_box():
    to_5070 = Transformer.from_crs("EPSG:4269", "EPSG:5070", always_xy=True)
    west, south, east, north = P.viz.AOI_BOUNDS_4269
    xs, ys = to_5070.transform([west, east, west, east], [south, south, north, north])
    return box(min(xs), min(ys), max(xs), max(ys))


def unit_fill(units: gpd.GeoDataFrame) -> list[str]:
    return [RIPARIAN_FILL if r else OWNER_COLORS[o]
            for r, o in zip(units["RIPARIAN"], units["OWNER_CLASS"])]


def blend(colors: list[str], amount: float) -> list:
    return [tuple(hex_rgb(c) * (1 - amount) + hex_rgb(BACKGROUND) * amount) for c in colors]


def closeup_box(window, center, scale: float = ZOOM_SCALE):
    """The AOI window shrunk by ``scale`` about ``center``, same aspect, kept inside the window."""
    x0, y0, x1, y1 = window.bounds
    w, h = (x1 - x0) * scale, (y1 - y0) * scale
    cx = min(max(center.x, x0 + w / 2), x1 - w / 2)
    cy = min(max(center.y, y0 + h / 2), y1 - h / 2)
    return box(cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2)


def render_units(units: gpd.GeoDataFrame, frame, path: Path, width_in: float,
                 selected: int | None = None) -> None:
    xmin, ymin, xmax, ymax = frame.bounds
    fig, ax = plt.subplots(figsize=(width_in, width_in * (ymax - ymin) / (xmax - xmin)), dpi=DPI)
    fig.patch.set_facecolor(NON_FOREST)
    ax.set_facecolor(NON_FOREST)
    fill = unit_fill(units)
    riparian = units["RIPARIAN"].to_numpy() == 1
    if selected is None:
        units.plot(ax=ax, color=fill, edgecolor=blend(fill, 0.45), linewidth=0.8)
        units[riparian].boundary.plot(ax=ax, color=RIPARIAN_EDGE, linewidth=0.3)
    else:
        units.plot(ax=ax, color=blend(fill, 0.55), edgecolor=blend(fill, 0.8), linewidth=0.4)
        target = units[units["MU_ID"] == selected]
        target.plot(ax=ax, color=fill[int(np.flatnonzero(units["MU_ID"] == selected)[0])],
                    edgecolor="#111111", linewidth=2.2)
    ax.set_xlim(xmin, xmax)
    ax.set_ylim(ymin, ymax)
    ax.set_axis_off()
    fig.subplots_adjust(0, 0, 1, 1)
    fig.savefig(path, facecolor=NON_FOREST)
    plt.close(fig)


def tree_list(mu_id: int, weights: pd.DataFrame) -> pd.DataFrame:
    w = weights[weights["MU_ID"] == mu_id][["MU_ID", "PLT_CN", "WEIGHT"]]
    con = sqlite3.connect(P.viz.FIA_DB)
    donors = ",".join(f"'{p}'" for p in w["PLT_CN"])
    trees = pd.read_sql(f"""SELECT CAST(CN AS TEXT) AS CN, CAST(PLT_CN AS TEXT) AS PLT_CN,
                                   STATUSCD, SPCD, DIA, HT, ACTUALHT, CR, TPA_UNADJ FROM TREE
                            WHERE CAST(PLT_CN AS TEXT) IN ({donors})""", con)
    species = pd.read_sql("SELECT SPCD, COMMON_NAME FROM REF_SPECIES", con)
    con.close()
    trees["PLT_CN"] = as_id_series(trees["PLT_CN"], column="PLT_CN")
    out = w.merge(trees[trees["STATUSCD"] == 1], on="PLT_CN", how="inner")
    out["TREE_COUNT"] = out["TPA_UNADJ"] * out["WEIGHT"]
    out["HT"] = out["HT"].fillna(out["ACTUALHT"])
    out = out.dropna(subset=["DIA", "TREE_COUNT"])
    out = out[out["TREE_COUNT"] > 0].merge(species, on="SPCD", how="left")
    out["COMMON_NAME"] = out["COMMON_NAME"].fillna(out["SPCD"].astype(str)).str.capitalize()
    out["BA"] = out["TREE_COUNT"] * 0.005454 * out["DIA"] ** 2
    return out


def pick_stand(units: pd.DataFrame, weights: pd.DataFrame, frame) -> int:
    """A family-forest pine-hardwood stand: the dominant owner class, and a mixed stand
    that reads differently from a plantation in the tree list."""
    donors = weights.groupby("MU_ID").size()
    s = units[units.geometry.within(frame)].copy()
    s["DONORS"] = s["MU_ID"].map(donors).fillna(0)
    cand = s[(s["OWNER_CLASS"] == "private_family") & (s["RIPARIAN"] == 0)
             & (s["FORTYPCD_DOM"] == LOBLOLLY_HARDWOOD) & s["Acres"].between(40, 200)
             & (s["YOUNG_SHARE"] == 0) & (s["DONORS"] >= 3)]
    cand = cand.assign(_d=(cand["Acres"] - 80).abs()).sort_values(["_d", "MU_ID"])
    return int(cand.iloc[0]["MU_ID"])


def dbh_chart(trees: pd.DataFrame, path: Path) -> list[dict]:
    top = trees.groupby("COMMON_NAME")["BA"].sum().sort_values(ascending=False)
    keep = list(top.index[:len(SPECIES_COLORS)])
    trees = trees.assign(GROUP=np.where(trees["COMMON_NAME"].isin(keep), trees["COMMON_NAME"], "Other"),
                         CLASS=(np.floor(trees["DIA"] / 2) * 2).clip(upper=MAX_CLASS))
    classes = np.arange(0, MAX_CLASS + 2, 2)
    table = trees.pivot_table(index="CLASS", columns="GROUP", values="TREE_COUNT",
                              aggfunc="sum", fill_value=0).reindex(classes, fill_value=0)
    order = keep + (["Other"] if "Other" in table.columns else [])
    colors = SPECIES_COLORS[:len(keep)] + [OTHER_COLOR]
    fig, ax = plt.subplots(figsize=(9, 5.2), dpi=DPI)
    fig.patch.set_facecolor(BACKGROUND)
    ax.set_facecolor(BACKGROUND)
    bottom = np.zeros(len(classes))
    for name, color in zip(order, colors):
        ax.bar(classes + 1, table[name], width=1.7, bottom=bottom, color=color,
               edgecolor=BACKGROUND, linewidth=1.5, label=name)
        bottom += table[name].to_numpy()
    ax.set_xlabel("DBH class (in)", fontsize=13, color="#4a4a48")
    ax.set_ylabel("Trees per acre", fontsize=13, color="#4a4a48")
    ax.set_xticks(classes + 1, [f"{c:g}–{c + 2:g}" for c in classes[:-1]] + [f"{MAX_CLASS:g}+"])
    ax.tick_params(colors="#4a4a48", labelsize=12, length=0)
    ax.grid(axis="y", color="#dcd8cc", linewidth=0.8)
    ax.set_axisbelow(True)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color("#8a8780")
    ax.legend(frameon=False, fontsize=12, loc="upper right", labelcolor="#2b2b2b")
    fig.tight_layout()
    fig.savefig(path, facecolor=BACKGROUND)
    plt.close(fig)
    return [{"name": n, "color": c} for n, c in zip(order, colors)]


def main() -> None:
    seg = np.load(P.SEGMENTATION_NPZ)["seg_labels"]
    units = gpd.read_file(P.WORK / "management_units.gpkg")
    weights = pd.read_csv(P.MU_DONOR_WEIGHTS_CSV, dtype={"PLT_CN": str})
    weights["PLT_CN"] = as_id_series(weights["PLT_CN"], column="PLT_CN")
    with rasterio.open(P.NWOS_TIF) as src:
        outside = src.read(1) == src.nodata
        bounds = src.bounds

    # Slide 1, full extent: parent stands by owner class (riparian is sub-pixel at this scale).
    owner_lut = np.tile(hex_rgb(NON_FOREST), (int(seg.max()) + 1, 1))
    parents = units.drop_duplicates("SEG_ID")
    for owner, color in OWNER_COLORS.items():
        owner_lut[parents.loc[parents["OWNER_CLASS"] == owner, "SEG_ID"].to_numpy()] = hex_rgb(color)
    rgb = owner_lut[seg]
    rgb[outside] = hex_rgb(BACKGROUND)
    counties = gpd.read_file(P.viz.COUNTIES_SHP)
    counties = (counties[counties["ADMIN_FIPS"].isin(P.viz.PILOT_COUNTY_FIPS)]
                .dissolve(by="NAME", as_index=False).to_crs("EPSG:5070"))
    window = zoom_box()
    chosen = pick_stand(units, weights, window)
    target = units[units["MU_ID"] == chosen].iloc[0]
    frame = closeup_box(window, target.geometry.centroid)
    fig, ax = plt.subplots(figsize=(10, 10 * seg.shape[0] / seg.shape[1]), dpi=DPI)
    fig.patch.set_facecolor(BACKGROUND)
    ax.imshow(rgb, extent=(bounds.left, bounds.right, bounds.bottom, bounds.top), interpolation="nearest")
    counties.boundary.plot(ax=ax, color="#3a3a38", linewidth=0.8)
    for _, c in counties.iterrows():
        p = c.geometry.representative_point()
        ax.text(p.x, p.y, c["NAME"], fontsize=11, ha="center", color="#1f1f1d", weight="bold",
                bbox={"facecolor": BACKGROUND, "edgecolor": "none", "alpha": 0.8, "pad": 2})
    x0, y0, x1, y1 = frame.bounds
    ax.add_patch(Rectangle((x0, y0), x1 - x0, y1 - y0, fill=False, edgecolor="#111111", linewidth=2))
    ax.set_xlim(bounds.left, bounds.right)
    ax.set_ylim(bounds.bottom, bounds.top)
    ax.set_axis_off()
    fig.subplots_adjust(0, 0, 1, 1)
    fig.savefig(P.OUTPUTS / "fig_fl5_stands.png", facecolor=BACKGROUND)
    plt.close(fig)

    in_zoom = units[units.intersects(frame)]
    render_units(in_zoom, frame, P.OUTPUTS / "fig_zoom_stands.png", width_in=8.8)

    tx0, ty0, tx1, ty1 = target.geometry.bounds
    half = max(tx1 - tx0, ty1 - ty0) * 2 + 600
    cx, cy = (tx0 + tx1) / 2, (ty0 + ty1) / 2
    close = box(cx - half, cy - half, cx + half, cy + half)
    render_units(units[units.intersects(close)], close, P.OUTPUTS / "fig_zoom_selected.png",
                 width_in=6, selected=chosen)

    trees = tree_list(chosen, weights)
    legend = dbh_chart(trees, P.OUTPUTS / "fig_selected_dbh.png")
    stand_table = (trees.assign(D2=trees["TREE_COUNT"] * trees["DIA"] ** 2)
                        .groupby("COMMON_NAME")
                        .agg(TPA=("TREE_COUNT", "sum"), BA=("BA", "sum"), D2=("D2", "sum"))
                        .sort_values("BA", ascending=False))
    stand_table["QMD"] = np.sqrt(stand_table.pop("D2") / stand_table["TPA"])
    w = weights[weights["MU_ID"] == chosen].sort_values("WEIGHT", ascending=False)
    info = {
        "mu_id": chosen, "parent_seg": int(target["SEG_ID"]), "acres": round(float(target["Acres"]), 1),
        "owner": OWNER_LABELS[target["OWNER_CLASS"]], "fortypcd": int(target["FORTYPCD_DOM"]),
        "age": int(target["AGE"]), "stdage_mean": round(float(target["STDAGE_MEAN"]), 1),
        "balive_mean": round(float(target["BALIVE_MEAN"]), 1),
        "tpa_mean": round(float(target["TPA_MEAN"]), 1),
        "donors": [{"plt_cn": p, "weight": round(float(x), 3)} for p, x in zip(w["PLT_CN"], w["WEIGHT"])],
        "tree_rows": int(len(trees)),
        "tpa": round(float(trees["TREE_COUNT"].sum()), 1), "ba": round(float(trees["BA"].sum()), 1),
        "qmd": round(float(np.sqrt((trees["TREE_COUNT"] * trees["DIA"] ** 2).sum()
                                   / trees["TREE_COUNT"].sum())), 2),
        "stand_table": stand_table.round(1).reset_index().to_dict(orient="records"),
        "legend": legend,
        "zoom_acres": round(float(in_zoom.clip(frame)["Acres"].sum()), 0),
        "zoom_units": int(units.geometry.within(frame).sum()),
        "parent_stands": int(leto_ca.renumber_segments(seg).max()),
    }
    (P.OUTPUTS / "selected_stand.json").write_text(json.dumps(info, indent=2))
    print(json.dumps({k: v for k, v in info.items() if k != "stand_table"}, indent=2))


if __name__ == "__main__":
    main()
