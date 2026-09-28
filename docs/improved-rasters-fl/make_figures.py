"""Figures and numbers for docs/improved-rasters-fl/presentation.html.

Reads the county and AOI outputs of ``pipeline.s1_initial_state.county_improvement``
from /mnt/d/improved-rasters. Writes small committed files next to the deck:
``figures/*.png|jpg`` (previews sized to live in git without LFS) and ``data/*.json|csv``.

    uv run python docs/improved-rasters-fl/make_figures.py            # everything
    uv run python docs/improved-rasters-fl/make_figures.py --only fia maps

Network: FIA EVALIDator (forest-area estimates) and Earth Engine (NAIP chips). Both are
cached, in ``data/`` and ``/mnt/d/improved-rasters/figure_cache/`` respectively.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # run as a script from anywhere

import geopandas as gpd  # noqa: E402
import matplotlib  # noqa: E402

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import rasterio  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402
from PIL import Image  # noqa: E402
from rasterio import features  # noqa: E402
from scipy import ndimage  # noqa: E402

from pipeline.s1_initial_state.add_back_methods import METHOD_PRIORITY, AddBackMethod  # noqa: E402
from pipeline.s1_initial_state.county_improvement import (  # noqa: E402
    AOI_COUNTIES,
    AOI_NAME,
    OUT_ROOT,
    Inputs,
    TreeMapProvenance as TP,
    county_dir,
)
from pipeline.s1_initial_state.finalize_add_back import ACRES_PER_PIXEL  # noqa: E402
from pipeline.s1_initial_state.statewide_repair import FLGrid  # noqa: E402
from pipeline.s1_initial_state.verify_fia_evalidator import (  # noqa: E402
    ATTRIBUTE_AREA_FOREST,
    EVAL_GRP,
    query,
    total_cell,
)

HERE = Path(__file__).resolve().parent
FIG = HERE / "figures"
DATA = HERE / "data"
AOI = OUT_ROOT / AOI_NAME
CACHE = OUT_ROOT / "figure_cache"
STATES = Path("/mnt/d/tl_2022_us_state/tl_2022_us_state.shp")

# Colours. Maps: provenance on a paper surface (validated: green/violet pass all checks).
PAPER, INK, INK_SOFT, LINE = "#f6f5ef", "#1b221c", "#4b564c", "#d0d0c3"
PUBLISHED, ADDED, UNMAPPED, WATER, OUTSIDE_C = "#008300", "#4a3aa7", "#e6e0cf", "#c6d3de", PAPER
# Charts: methods take categorical slots 1-3 in priority order (validated; aqua needs labels).
METHOD_COLOR = {AddBackMethod.BOOKENDS: "#2a78d6", AddBackMethod.OBATA_DISTURBANCE: "#eb6834",
                AddBackMethod.HANSEN_LOSS: "#1baf7a"}
METHOD_LABEL = {AddBackMethod.BOOKENDS: "LANDFIRE bookends",
                AddBackMethod.OBATA_DISTURBANCE: "Obata Landsat disturbance",
                AddBackMethod.HANSEN_LOSS: "Hansen forest loss"}
BASE_GREY = "#b9b8ae"
# Harris/NWOS 0-8, the palette docs/county-correction already uses.
OWNERS = ["#404040", "#d9cfbe", "#63d3f2", "#b5533c", "#f5d000", "#642a89", "#788c00", "#f27600", "#753b16"]
OWNER_LABELS = ["Unknown forest", "Non-forest", "Water", "Family", "Corporate", "Tribal", "Federal",
                "State", "Local"]

plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 11, "axes.edgecolor": LINE,
                     "axes.labelcolor": INK_SOFT, "xtick.color": INK_SOFT, "ytick.color": INK_SOFT,
                     "figure.facecolor": PAPER, "axes.facecolor": PAPER, "savefig.facecolor": PAPER})


def hexrgb(h: str) -> tuple[int, int, int]:
    return tuple(int(h[i:i + 2], 16) for i in (1, 3, 5))


def save_png(fig, name: str, colors: int = 96) -> Path:
    """Save, then re-encode as an adaptive-palette PNG: flat-colour maps shrink ~5x."""
    path = FIG / name
    fig.savefig(path, dpi=160, bbox_inches="tight")
    plt.close(fig)
    Image.open(path).convert("RGB").quantize(colors=colors, method=Image.Quantize.MEDIANCUT) \
        .save(path, optimize=True)
    return path


def save_jpg(fig, name: str) -> Path:
    path = FIG / name
    fig.savefig(path, dpi=150, bbox_inches="tight", pil_kwargs={"quality": 84, "optimize": True})
    plt.close(fig)
    return path


def summaries() -> tuple[dict, list[dict]]:
    aoi = json.loads((AOI / "summary.json").read_text())
    counties = [json.loads((county_dir(OUT_ROOT, f) / "summary.json").read_text()) for f in AOI_COUNTIES]
    return aoi, counties


# ── FIA and the statewide TreeMap count ─────────────────────────────────────────────────


def fia_estimates() -> dict:
    """EVALIDator forest-land area: Florida, the AOI as one domain, and each county."""
    path = DATA / "fia_forest_area.json"
    if path.exists():
        return json.loads(path.read_text())
    base = {"snum": ATTRIBUTE_AREA_FOREST, "wc": EVAL_GRP, "rselected": "All live stocking",
            "cselected": "All live stocking", "outputFormat": "JSON"}

    def one(where: str | None) -> dict:
        est, se_pct, plots = total_cell(query({**base, **({"wf": where} if where else {})}))
        return {"acres": round(est), "se_pct": round(se_pct, 3), "plots": plots}

    codes = {fips: int(fips[2:]) for fips in AOI_COUNTIES}
    out = {"source": "USFS FIADB-API EVALIDator fullreport, attribute 2 (forest land area), "
                     f"evaluation group {EVAL_GRP} (EVALID 122201, EXPCURR)",
           "retrieved": datetime.now(timezone.utc).date().isoformat(),
           "florida": one(None),
           "aoi_5county": one(f"PLOT.COUNTYCD IN ({','.join(str(c) for c in codes.values())})"),
           "counties": {f: one(f"PLOT.COUNTYCD IN ({c})") for f, c in codes.items()}}
    path.write_text(json.dumps(out, indent=2))
    return out


def treemap_florida() -> dict:
    """TreeMap 2022 mapped (forest) acres inside the Florida boundary, counted row-block-wise."""
    path = DATA / "treemap_florida.json"
    if path.exists():
        return json.loads(path.read_text())
    fl = gpd.read_file(STATES, where="STUSPS = 'FL'").to_crs(5070).geometry.union_all()
    tif = Inputs().treemap
    mapped = 0
    with rasterio.open(tif) as src:
        window, transform = FLGrid(src.transform.c, src.transform.f).window(fl.bounds)
        for r0 in range(0, int(window.height), 1024):
            # The TreeMap footprint stops short of the Keys; rows past it hold no forest.
            h = min(1024, int(window.height) - r0, src.height - int(window.row_off) - r0)
            if h <= 0:
                break
            sub = rasterio.windows.Window(window.col_off, window.row_off + r0, window.width, h)
            ids = src.read(1, window=sub, boundless=False)
            t = src.window_transform(sub)
            inside = features.rasterize([(fl, 1)], out_shape=ids.shape, transform=t, dtype="uint8")
            mapped += int(((ids != src.nodata) & (inside == 1)).sum())
    out = {"treemap_mapped_acres": round(mapped * ACRES_PER_PIXEL),
           "boundary": "TIGER 2022 state polygon (STUSPS FL)", "pixels": mapped}
    path.write_text(json.dumps(out, indent=2))
    return out


def fig_fia_gap(fia: dict, tm_fl: dict, counties: list[dict], aoi: dict) -> None:
    """TreeMap as published, as a share of the FIA estimate, with FIA's 95% interval."""
    rows = [("Florida", tm_fl["treemap_mapped_acres"], fia["florida"]),
            ("5-county AOI", aoi["published_forest_acres"], fia["aoi_5county"])]
    rows += [(s["county"], s["published_forest_acres"], fia["counties"][s["county_fips"]])
             for s in counties]
    table = []
    fig, ax = plt.subplots(figsize=(10.5, 4.6))
    for i, (name, tm, f) in enumerate(rows):
        y = len(rows) - 1 - i - (0.5 if i >= 2 else 0) - (0.3 if i >= 1 else 0)
        half = 1.96 * f["se_pct"]
        ax.plot([100 - half, 100 + half], [y, y], color=LINE, lw=7, solid_capstyle="round", zorder=1)
        share = 100 * tm / f["acres"]
        ax.plot([share, 100], [y, y], color=INK_SOFT, lw=1.2, zorder=2)
        ax.scatter([100], [y], s=40, color=INK, zorder=3, marker="|")
        ax.scatter([share], [y], s=70, color=PUBLISHED, zorder=4, edgecolor=PAPER, linewidth=1.5)
        gap = f["acres"] - tm
        over = share > 100
        ax.text(share + (1.2 if over else -1.2), y + 0.22, f"{share:.0f}%", ha="left" if over else "right",
                va="center", fontsize=10, color=INK)
        size = f"{abs(gap) / 1e6:.2f}M" if abs(gap) >= 1e6 else f"{abs(gap) / 1e3:,.0f}k"
        ax.text(101 + half, y, f"{size} ac {'over' if gap < 0 else 'short'}",
                ha="left", va="center", fontsize=9.5, color=INK_SOFT)
        table.append({"geography": name, "fia_forest_acres": f["acres"], "fia_se_pct": f["se_pct"],
                      "fia_plots": f["plots"], "treemap_published_acres": tm,
                      "treemap_share_of_fia_pct": round(share, 1), "shortfall_acres": gap})
    ax.set_yticks([len(rows) - 1 - i - (0.5 if i >= 2 else 0) - (0.3 if i >= 1 else 0)
                   for i in range(len(rows))], [r[0] for r in rows])
    ax.set_xlim(55, 142)
    ax.set_xlabel("TreeMap 2022 forest as % of the FIA forest-land estimate (FIA = 100%)")
    ax.axvline(100, color=INK, lw=0.8, zorder=0)
    ax.spines[["top", "right", "left"]].set_visible(False)
    ax.tick_params(axis="y", length=0)
    ax.legend(handles=[Patch(color=PUBLISHED, label="TreeMap 2022 as published"),
                       Patch(color=LINE, label="FIA 95% confidence interval")],
              loc="upper center", bbox_to_anchor=(0.5, -0.16), ncol=2, frameon=False, fontsize=9.5)
    save_png(fig, "fig02_fia_gap.png", colors=48)
    pd.DataFrame(table).to_csv(DATA / "fia_gap.csv", index=False)


def fig_area_vs_fia(fia: dict, counties: list[dict], aoi: dict) -> None:
    """Stacked add-back by method (priority order, each pixel once) against the FIA interval."""
    rows = [("5-county AOI", aoi, fia["aoi_5county"])]
    rows += [(s["county"], s, fia["counties"][s["county_fips"]]) for s in counties]
    fig, ax = plt.subplots(figsize=(10.5, 4.4))
    table = []
    for i, (name, s, f) in enumerate(rows):
        y = len(rows) - 1 - i + (0.4 if i == 0 else 0)
        half = 1.96 * f["se_pct"]
        ax.errorbar(100, y - 0.36, xerr=half, fmt="none", ecolor=INK_SOFT, elinewidth=1.2,
                    capsize=4, zorder=3)
        left = 100 * s["published_forest_acres"] / f["acres"]
        ax.barh(y, left, color=BASE_GREY, height=0.5, zorder=2)
        rec = {"geography": name, "fia_acres": f["acres"], "fia_se_pct": f["se_pct"],
               "published_acres": s["published_forest_acres"]}
        for m in METHOD_PRIORITY:
            acres = s["added_back_credit_acres"][str(m)]
            rec[f"{m}_acres"] = acres
            if acres:
                w = 100 * acres / f["acres"]
                ax.barh(y, w, left=left + 0.25, color=METHOD_COLOR[m], height=0.5, zorder=2)
                if w > 3:
                    ax.text(left + 0.25 + w / 2, y, f"+{acres / 1e3:,.0f}k", ha="center", va="center",
                            fontsize=9, color="#ffffff", fontweight="bold", zorder=4)
                left += w + 0.25
        total = s["published_forest_acres"] + sum(v or 0 for v in s["added_back_credit_acres"].values())
        z = (total - f["acres"]) / (f["acres"] * f["se_pct"] / 100)
        ax.text(max(left, 100 + half) + 1, y, f"{z:+.2f} SE", va="center", fontsize=9.5, color=INK)
        rec["total_acres"], rec["distance_se"] = round(total), round(z, 2)
        table.append(rec)
    ax.axvline(100, color=INK, lw=0.8, zorder=3)
    ax.set_yticks([len(rows) - 1 - i + (0.4 if i == 0 else 0) for i in range(len(rows))],
                  [r[0] for r in rows])
    ax.set_xlim(50, 140)
    ax.set_xlabel("Forest acres as % of the FIA forest-land estimate (FIA = 100%, whisker = 95% CI)")
    ax.spines[["top", "right", "left"]].set_visible(False)
    ax.tick_params(axis="y", length=0)
    handles = [Patch(color=BASE_GREY, label="TreeMap 2022 as published")]
    for m in METHOD_PRIORITY:
        pending = aoi["methods"][str(m)] == "pending"
        handles.append(Patch(facecolor=METHOD_COLOR[m] if not pending else PAPER,
                             edgecolor=METHOD_COLOR[m], hatch="////" if pending else None,
                             label=f"+ {METHOD_LABEL[m]}" + (" (pending)" if pending else "")))
    ax.legend(handles=handles, loc="upper center", bbox_to_anchor=(0.5, -0.16), ncol=4,
              frameon=False, fontsize=9.5)
    save_png(fig, "fig07_area_vs_fia.png", colors=48)
    pd.DataFrame(table).to_csv(DATA / "area_vs_fia.csv", index=False)


# ── AOI maps ────────────────────────────────────────────────────────────────────────────


def read(name: str, root: Path = AOI):
    with rasterio.open(root / name) as src:
        return src.read(1), src.transform, src.nodata


def priority_reduce(values: np.ndarray, factor: int, priority: list[int]) -> np.ndarray:
    """Downsample classes by ``factor`` keeping the highest-priority class in each block,
    so a small added-back patch survives the preview."""
    rank = np.full(256, 0, dtype=np.uint8)
    for i, v in enumerate(priority):
        rank[v] = len(priority) - i
    h, w = (values.shape[0] // factor) * factor, (values.shape[1] // factor) * factor
    r = rank[values[:h, :w]].reshape(h // factor, factor, w // factor, factor).max(axis=(1, 3))
    inverse = np.zeros(256, dtype=np.uint8)
    for i, v in enumerate(priority):
        inverse[len(priority) - i] = v
    return inverse[r]


def county_shapes():
    rows = gpd.read_file(Inputs().counties,
                         where=f"ADMIN_FIPS IN ({','.join(repr(f) for f in AOI_COUNTIES)})")
    return rows.to_crs(5070).dissolve("ADMIN_FIPS").reset_index()


def draw_map(classes: np.ndarray, transform, palette: dict[int, str], legend: list[tuple[str, str]],
             name: str, title: str, factor: int = 2) -> None:
    rgb = np.zeros((*classes.shape, 3), dtype=np.uint8)
    for v, c in palette.items():
        rgb[classes == v] = hexrgb(c)
    h, w = classes.shape
    extent = (transform.c, transform.c + w * factor * 30, transform.f - h * factor * 30, transform.f)
    fig, ax = plt.subplots(figsize=(12, 8.6))
    ax.imshow(rgb, extent=extent, interpolation="nearest")
    shapes = county_shapes()
    shapes.boundary.plot(ax=ax, color=INK, linewidth=0.8)
    for _, row in shapes.iterrows():
        p = row.geometry.representative_point()
        ax.text(p.x, p.y, AOI_COUNTIES[row.ADMIN_FIPS], ha="center", va="center", fontsize=11,
                fontweight="bold", color=INK,
                bbox=dict(boxstyle="round,pad=0.25", fc=PAPER, ec="none", alpha=0.85))
    ax.set_axis_off()
    ax.set_title(title, loc="left", fontsize=14, color=INK, fontweight="bold")
    ax.legend(handles=[Patch(color=c, label=l) for l, c in legend], loc="lower left",
              frameon=True, facecolor=PAPER, edgecolor=LINE, fontsize=10)
    save_png(fig, name, colors=64)


def fig_aoi_maps(aoi: dict) -> None:
    prov, t, nodata = read("treemap2022_provenance.tif")
    nodata = int(nodata)
    order = [int(TP.ADDED_BACK), int(TP.PUBLISHED), int(TP.UNMAPPED_LAND), int(TP.WATER), nodata]
    before = prov.copy()
    before[prov == TP.ADDED_BACK] = TP.UNMAPPED_LAND
    pal = {TP.PUBLISHED: PUBLISHED, TP.ADDED_BACK: ADDED, TP.UNMAPPED_LAND: UNMAPPED,
           TP.WATER: WATER, int(nodata): OUTSIDE_C}
    pub, add = aoi["published_forest_acres"], aoi["added_back_acres"]
    draw_map(priority_reduce(before, 2, order), t, pal,
             [(f"TreeMap forest ({pub / 1e6:.3f}M ac)", PUBLISHED),
              (f"Land TreeMap leaves unmapped ({aoi['hole_acres'] / 1e3:,.0f}k ac)", UNMAPPED),
              ("Water", WATER)],
             "fig09_treemap_before.png", "TreeMap 2022 as published: five-county AOI")
    draw_map(priority_reduce(prov, 2, order), t, pal,
             [(f"TreeMap forest ({pub / 1e6:.3f}M ac)", PUBLISHED),
              (f"Added back, donor-filled (+{add / 1e3:,.1f}k ac)", ADDED),
              ("Land still unmapped", UNMAPPED), ("Water", WATER)],
             "fig10_treemap_after.png", "TreeMap 2022 improved: five-county AOI")

    for tag, fname, title in (("published", "fig11_nwos_before.png", "NWOS 2022 ownership as published"),
                              ("improved", "fig12_nwos_after.png", "NWOS 2022 ownership improved")):
        own, t, nd = read(f"nwos2022_{tag}.tif")
        small = own[::2, ::2]
        pal = {i: c for i, c in enumerate(OWNERS)} | {int(nd): OUTSIDE_C}
        present = [i for i in range(9) if (own == i).any()]
        draw_map(small, t, pal, [(OWNER_LABELS[i], OWNERS[i]) for i in present], fname,
                 f"{title}: five-county AOI")


# ── chips: TreeMap, NWOS and NAIP around one place ──────────────────────────────────────


def naip_chip(cx: float, cy: float, half_m: float, scale_m: float = 2.0) -> tuple[np.ndarray, str]:
    """NAIP RGB around (cx, cy) in EPSG:5070 from the flight closest to mid-2022."""
    CACHE.mkdir(parents=True, exist_ok=True)
    key = CACHE / f"naip_{cx:.0f}_{cy:.0f}_{half_m:.0f}_{scale_m:g}.npz"
    if key.exists():
        z = np.load(key)
        return z["rgb"], str(z["date"])
    import ee

    ee.Initialize(project="perseus-gee")
    region = ee.Geometry.Rectangle([cx - half_m, cy - half_m, cx + half_m, cy + half_m],
                                   ee.Projection("EPSG:5070"), False)
    col = ee.ImageCollection("USDA/NAIP/DOQQ").filterBounds(region).filterDate("2019-01-01", "2024-12-31")
    stamps = col.aggregate_array("system:time_start").getInfo()
    target = datetime(2022, 7, 1, tzinfo=timezone.utc).timestamp() * 1000
    best = min(stamps, key=lambda s: (abs(s - target), -s))
    image = col.filterDate(ee.Date(best - 45 * 86400e3), ee.Date(best + 45 * 86400e3)).mosaic()
    n = int(round(2 * half_m / scale_m))
    arr = ee.data.computePixels({
        "expression": image.select(["R", "G", "B"]),
        "fileFormat": "NUMPY_NDARRAY",
        "grid": {"dimensions": {"width": n, "height": n},
                 "affineTransform": {"scaleX": scale_m, "shearX": 0, "translateX": cx - half_m,
                                     "shearY": 0, "scaleY": -scale_m, "translateY": cy + half_m},
                 "crsCode": "EPSG:5070"}})
    rgb = np.dstack([arr["R"], arr["G"], arr["B"]]).astype(np.uint8)
    date = datetime.fromtimestamp(best / 1000, timezone.utc).date().isoformat()
    np.savez_compressed(key, rgb=rgb, date=date)
    return rgb, date


def window_of(values: np.ndarray, t, cx: float, cy: float, half_m: float) -> np.ndarray:
    c0 = int((cx - half_m - t.c) // 30)
    r0 = int((t.f - (cy + half_m)) // 30)
    n = int(round(2 * half_m / 30))
    return values[r0:r0 + n, c0:c0 + n]


def overlay(ax, rgb, cls, palette, alpha: float, half_m: float, transparent: tuple[int, ...] = ()):
    ax.imshow(rgb, extent=(-half_m, half_m, -half_m, half_m))
    over = np.zeros((*cls.shape, 4))
    for v, c in palette.items():
        if v in transparent:
            continue
        over[cls == v] = [*(np.array(hexrgb(c)) / 255), alpha]
    ax.imshow(over, extent=(-half_m, half_m, -half_m, half_m), interpolation="nearest")
    ax.set_xticks([]), ax.set_yticks([])
    for s in ax.spines.values():
        s.set_color(LINE)


def outline(ax, mask: np.ndarray, half_m: float, color: str) -> None:
    n = mask.shape[0]
    xs = np.linspace(-half_m + 15, half_m - 15, n)
    ax.contour(xs, xs[::-1], mask.astype(float), levels=[0.5], colors=[color], linewidths=1.6)


def added_patches(prov: np.ndarray, strata: np.ndarray, nwos: np.ndarray, t,
                  min_px: int = 150) -> pd.DataFrame:
    """Accepted patches in the AOI with centroid, size, modal stratum and interior depth."""
    labels, n = ndimage.label(prov == TP.ADDED_BACK, structure=np.ones((3, 3)))
    sizes = np.bincount(labels.ravel())
    depth = ndimage.distance_transform_edt(labels > 0)
    rows = []
    for pid, sl in enumerate(ndimage.find_objects(labels), start=1):
        if sl is None or sizes[pid] < min_px:
            continue
        m = labels[sl] == pid
        rr, cc = np.nonzero(m)
        s = np.bincount(strata[sl][m][strata[sl][m] < 6], minlength=6)
        rows.append({"pid": pid, "px": int(sizes[pid]), "stratum": int(s[1:].argmax() + 1),
                     "depth": float(depth[sl][m].max()),
                     "nwos_non_forest": float((nwos[sl][m] == 1).mean()),
                     "x": t.c + (sl[1].start + cc.mean() + 0.5) * 30,
                     "y": t.f - (sl[0].start + rr.mean() + 0.5) * 30})
    return pd.DataFrame(rows)


def pick_sites(p: pd.DataFrame, strata: tuple[int, ...], per: int, seed: int = 20260928,
               min_sep_m: float = 8000.0) -> pd.DataFrame:
    """Per stratum, a reproducible draw among the most interior (deepest) large patches,
    no two closer than ``min_sep_m`` so the chips show different places."""
    out: list[pd.Series] = []
    for s in strata:
        pool = p[p.stratum == s].nlargest(24, "depth").sample(frac=1, random_state=seed)
        taken = 0
        for _, row in pool.iterrows():
            if taken == per:
                break
            if all(np.hypot(row.x - o.x, row.y - o.y) >= min_sep_m for o in out):
                out.append(row)
                taken += 1
    return pd.DataFrame(out)


STRATUM_TEXT = {1: "S1 · logged 2016 → tree 2024", 2: "S2 · tree 2016 and 2024",
                3: "S3 · tree 2016 → open 2024 (gated)", 4: "S4 · open 2016 → tree 2024 (gated)"}


def fig_holes(sites: pd.DataFrame) -> None:
    """Slide 1: the same hole in TreeMap and NWOS, over NAIP."""
    prov, t, _ = read("treemap2022_provenance.tif")
    own, _, _ = read("nwos2022_published.tif")
    half = 1500.0
    tm_before = np.where(prov == TP.PUBLISHED, 1, 0)
    fig, axes = plt.subplots(len(sites), 3, figsize=(12, 4.1 * len(sites)))
    for row, (_, s) in zip(np.atleast_2d(axes), sites.iterrows()):
        rgb, date = naip_chip(s.x, s.y, half, 3.0)
        tm = window_of(tm_before, t, s.x, s.y, half)
        ow = window_of(own, t, s.x, s.y, half)
        hole = window_of(prov, t, s.x, s.y, half) == TP.ADDED_BACK
        row[0].imshow(rgb, extent=(-half, half, -half, half))
        row[0].set_xticks([]), row[0].set_yticks([])
        outline(row[0], hole, half, "#ffffff")
        overlay(row[1], rgb, tm, {1: PUBLISHED}, 0.62, half)
        overlay(row[2], rgb, ow, {i: c for i, c in enumerate(OWNERS)}, 0.62, half, transparent=(1,))
        for ax in row[1:]:
            outline(ax, hole, half, "#ffffff")
        row[0].set_title(f"NAIP {date}", loc="left", fontsize=10.5, color=INK)
        row[1].set_title("TreeMap 2022: no plot in the hole", loc="left", fontsize=10.5, color=INK)
        row[2].set_title("NWOS 2022: Non-forest (clear) in the same hole", loc="left", fontsize=10.5,
                         color=INK)
    fig.legend(handles=[Patch(color=PUBLISHED, label="TreeMap forest"),
                        Patch(facecolor="none", edgecolor="#777", label="white line: land the repair adds back")]
               + [Patch(color=OWNERS[i], label=f"NWOS {OWNER_LABELS[i]}") for i in (0, 3, 4, 6)],
               loc="lower center", ncol=6, frameon=False, fontsize=9.5, bbox_to_anchor=(0.5, -0.01))
    fig.subplots_adjust(wspace=0.03, hspace=0.12, bottom=0.05)
    save_jpg(fig, "fig01_holes_treemap_nwos.jpg")


def fig_bookend_chips(sites: pd.DataFrame) -> None:
    """Slide 4: added-back land (bookends), one site per stratum, next to NAIP."""
    prov, t, _ = read("treemap2022_provenance.tif")
    half = 900.0
    n = len(sites)
    fig, axes = plt.subplots(2, n, figsize=(3.2 * n, 6.6))
    pal = {TP.PUBLISHED: PUBLISHED, TP.ADDED_BACK: ADDED, TP.UNMAPPED_LAND: UNMAPPED, TP.WATER: WATER}
    for i, (_, s) in enumerate(sites.iterrows()):
        rgb, date = naip_chip(s.x, s.y, half, 2.0)
        cls = window_of(prov, t, s.x, s.y, half)
        overlay(axes[0, i], np.full_like(rgb, 255), cls, pal, 1.0, half)
        axes[1, i].imshow(rgb, extent=(-half, half, -half, half))
        axes[1, i].set_xticks([]), axes[1, i].set_yticks([])
        outline(axes[1, i], cls == TP.ADDED_BACK, half, "#ffffff")
        axes[0, i].set_title(f"{STRATUM_TEXT[s.stratum]}\n{s.px * ACRES_PER_PIXEL:,.0f} ac patch",
                             loc="left", fontsize=9.5, color=INK)
        axes[1, i].set_title(f"NAIP {date}", loc="left", fontsize=9.5, color=INK_SOFT)
    fig.legend(handles=[Patch(color=PUBLISHED, label="TreeMap forest"),
                        Patch(color=ADDED, label="added back (bookends)"),
                        Patch(color=UNMAPPED, label="still unmapped"),
                        Patch(facecolor="none", edgecolor="#777", label="white line on NAIP: added back")],
               loc="lower center", ncol=4, frameon=False, fontsize=9.5, bbox_to_anchor=(0.5, -0.02))
    fig.subplots_adjust(wspace=0.04, hspace=0.16, bottom=0.07)
    save_jpg(fig, "fig04_bookend_chips.jpg")


FOREST_GROUPS = [("Longleaf / slash pine", range(140, 150)), ("Loblolly / shortleaf pine", range(160, 170)),
                 ("Oak / pine", range(400, 410)), ("Oak / gum / cypress", range(600, 610)),
                 ("Oak / hickory & other hardwood", range(500, 1000))]
GROUP_COLORS = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4"]


def forest_group_lut() -> np.ndarray:
    import pyogrio

    vat = pyogrio.read_dataframe(str(Inputs().treemap) + ".vat.dbf", columns=["Value", "FORTYPCD"],
                                 read_geometry=False)
    value = vat.Value.to_numpy().astype(np.int64)   # the raster's own cell value
    fortype = vat.FORTYPCD.to_numpy().astype(np.int64)
    lut = np.zeros(int(value.max()) + 1, dtype=np.uint8)
    for gi, (_, codes) in enumerate(FOREST_GROUPS, start=1):
        sel = np.isin(fortype, list(codes)) & (lut[value] == 0)
        lut[value[sel]] = gi
    lut[value[lut[value] == 0]] = len(FOREST_GROUPS) + 1  # other
    return lut


def fig_imputation(site: pd.Series) -> None:
    """Slide 15: one place, vegetation and ownership, before and after imputation."""
    half = 900.0
    lut = forest_group_lut()
    fig, axes = plt.subplots(1, 4, figsize=(15, 4.6))
    rgb, date = naip_chip(site.x, site.y, half, 2.0)
    for ax, name, title in ((axes[0], "treemap2022_published.tif", "TreeMap forest type, before"),
                            (axes[1], "treemap2022_improved.tif", "after: donor plot's forest type")):
        ids, t, nd = read(name)
        w = window_of(ids, t, site.x, site.y, half)
        cls = np.where(w == nd, 0, lut[np.where(w == nd, 0, w).clip(0, len(lut) - 1)])
        pal = {i + 1: c for i, c in enumerate(GROUP_COLORS)} | {len(FOREST_GROUPS) + 1: "#8a8a84"}
        overlay(ax, rgb, cls, pal, 0.8, half)
        ax.set_title(title, loc="left", fontsize=10.5, color=INK)
    prov, t, _ = read("treemap2022_provenance.tif")
    added = window_of(prov, t, site.x, site.y, half) == TP.ADDED_BACK
    for ax, name, title in ((axes[2], "nwos2022_published.tif", "NWOS owner, before"),
                            (axes[3], "nwos2022_improved.tif", "after: nearest known owner")):
        own, t, _ = read(name)
        overlay(ax, rgb, window_of(own, t, site.x, site.y, half), dict(enumerate(OWNERS)), 0.75, half,
                transparent=(1,))
        ax.set_title(title, loc="left", fontsize=10.5, color=INK)
    for ax in axes:
        outline(ax, added, half, "#ffffff")
    fig.legend(handles=[Patch(color=c, label=l) for (l, _), c in zip(FOREST_GROUPS, GROUP_COLORS)]
               + [Patch(color="#8a8a84", label="Other forest type")],
               loc="upper center", ncol=3, frameon=False, fontsize=9, bbox_to_anchor=(0.31, 0.06))
    fig.legend(handles=[Patch(color=OWNERS[i], label=OWNER_LABELS[i]) for i in (0, 3, 4, 6, 7, 8)],
               loc="upper center", ncol=3, frameon=False, fontsize=9, bbox_to_anchor=(0.72, 0.06))
    fig.subplots_adjust(wspace=0.03)
    save_jpg(fig, "fig15_imputation_zoom.jpg")
    return date


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", nargs="*", default=["fia", "maps", "chips"],
                        choices=["fia", "maps", "chips"])
    args = parser.parse_args()
    FIG.mkdir(exist_ok=True)
    DATA.mkdir(exist_ok=True)
    aoi, counties = summaries()
    if "fia" in args.only:
        fia = fia_estimates()
        fig_fia_gap(fia, treemap_florida(), counties, aoi)
        fig_area_vs_fia(fia, counties, aoi)
        pd.json_normalize(counties).to_csv(DATA / "county_summaries.csv", index=False)
        (DATA / "aoi_summary.json").write_text(json.dumps(aoi, indent=2))
    if "maps" in args.only:
        fig_aoi_maps(aoi)
    if "chips" in args.only:
        prov, t, _ = read("treemap2022_provenance.tif")
        strata, _, _ = read("bookend_strata.tif")
        nwos, _, _ = read("nwos2022_published.tif")
        patches = added_patches(prov, strata, nwos, t)
        chips = pick_sites(patches, (1, 2, 3, 4), 1)
        # Slide 1 shows the defect both rasters share: holes NWOS also calls non-forest.
        holes = pick_sites(patches[patches.nwos_non_forest >= 0.8], (1,), 2, seed=7)
        chips.assign(use="bookend_chip").pipe(
            lambda d: pd.concat([d, holes.assign(use="hole_example")])).to_csv(DATA / "chip_sites.csv", index=False)
        fig_bookend_chips(chips)
        fig_holes(holes)
        shown = set(chips.pid) | set(holes.pid)
        fig_imputation(pick_sites(patches[~patches.pid.isin(shown) & (patches.nwos_non_forest >= 0.8)],
                                  (1,), 1, seed=11).iloc[0])
    for p in sorted(FIG.iterdir()):
        print(f"{p.stat().st_size / 1024:8.0f} KB  {p.name}")


if __name__ == "__main__":
    main()
