"""The ``outputs`` stage's figures: removals, activity and vegetation over time, and maps.

Every number drawn is in a CSV beside it in ``qa/outputs/`` (or in the trajectory table), so a
figure can be redrawn without recomputing. Maps are previews read from the unit index COG at
most :data:`MAX_MAP_PIXELS` a side, one panel per timestep on a shared colour scale; the
rasters in ``outputs/rasters/`` are the full-resolution products.

Encoding, once: owner group is an identity, so it takes the first slots of the validated
categorical order, fixed per group name so a run without one group does not repaint the
others; a legend names it (the lines converge, so direct labels would collide). Activity is
ordered (none < thinning < clearcut), and vegetation measures are magnitudes, so both take
one blue hue light to dark. Groups whose totals differ by orders of
magnitude get their own axis (small multiples), never a second y scale.
"""

from __future__ import annotations

import math
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import numpy as np
import pandas as pd
import rasterio
from matplotlib.colors import BoundaryNorm, LinearSegmentedColormap, ListedColormap, Normalize
from matplotlib.figure import Figure
from matplotlib.lines import Line2D
from matplotlib.patches import Patch
from matplotlib.ticker import FuncFormatter
from rasterio.enums import Resampling

from pipeline.outputs_stage import VEGETATION_MEASURES, Activity, cycle_luts, vegetation_by_cycle
from pipeline.qa_report import FIGURES

MAX_MAP_PIXELS = 900
SURFACE = "#fcfcfb"
INK, INK_MUTED, GRID = "#0b0b0b", "#52514e", "#e4e3df"
CATEGORICAL = ("#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6",
               "#e34948")
GROUP_ORDER = ("Private", "Federal (NF)", "Other public")      # config/tpo_targets.yaml's groups
ACTIVITY_COLORS = {Activity.NONE: "#e4e3df", Activity.THINNING: "#86b6ef",
                   Activity.CLEARCUT: "#184f95"}
SEQUENTIAL = LinearSegmentedColormap.from_list(
    "blue", ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"])
MEASURE_TITLES = {"Age": "Stand age (years)", "BA": "Basal area (ft²/ac)",
                  "MCuFt": "Standing merchantable volume (ft³/ac)"}


def group_colors(groups) -> dict[str, str]:
    """Each owner group's fixed hue: the known groups first, any other in name order after."""
    extra = sorted(set(groups) - set(GROUP_ORDER))
    return {g: CATEGORICAL[i % len(CATEGORICAL)] for i, g in enumerate([*GROUP_ORDER, *extra])}


def _figure(width: float, height: float) -> Figure:
    return Figure(figsize=(width, height), dpi=130, facecolor=SURFACE)


def _style(ax) -> None:
    ax.set_facecolor(SURFACE)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(INK_MUTED)
    ax.tick_params(colors=INK_MUTED, labelsize=7.5)
    ax.grid(axis="y", color=GRID, linewidth=0.6)
    ax.set_axisbelow(True)


def _millions(ax) -> None:
    peak = max(abs(v) for v in ax.get_ylim())
    scale, unit = (1e6, "M") if peak >= 1e6 else (1e3, "k") if peak >= 1e3 else (1, "")
    ax.yaxis.set_major_formatter(FuncFormatter(
        lambda v, _p: f"{v / scale:,.1f}{unit}" if unit == "M" else f"{v / scale:,.0f}{unit}"))


def harvest_figure(harvest: pd.DataFrame, out: Path) -> Path:
    order = list(group_colors(harvest["owner_group"].unique()))
    groups = sorted(harvest["owner_group"].unique(), key=order.index)
    colors = group_colors(groups)
    fig = _figure(3.2 * len(groups) + 0.4, 3.0)
    axes = fig.subplots(1, len(groups), squeeze=False)[0]
    for ax, group in zip(axes, groups):
        rows = harvest[harvest["owner_group"] == group].sort_values("cycle")
        ax.bar(rows["calendar_year"], rows["removed_cuft"], width=3.4, color=colors[group],
               edgecolor=SURFACE, linewidth=1)
        if "target_cuft" in rows and rows["target_cuft"].notna().any():
            ax.plot(rows["calendar_year"], rows["target_cuft"], color=INK, linestyle="--",
                    linewidth=1.2, label="TPO target")
            ax.legend(frameon=False, fontsize=7, loc="lower right")
        _style(ax)
        _millions(ax)
        ax.set_title(f"{group} — {rows['removed_cuft'].sum() / 1e6:,.2f}M ft³", fontsize=9,
                     color=INK, loc="left")
        ax.set_xlabel("cycle start year", fontsize=8, color=INK_MUTED)
    axes[0].set_ylabel("merch. removals (ft³)", fontsize=8, color=INK_MUTED)
    fig.suptitle("Removals per owner group and cycle", fontsize=10, color=INK, x=0.01, ha="left")
    fig.tight_layout()
    return _save(fig, out / "harvest_by_owner_group.png")


def _lines(ax, frame: pd.DataFrame, value: str, colors: dict[str, str]) -> None:
    for group, rows in frame.groupby("owner_group"):
        rows = rows.sort_values("calendar_year")
        ax.plot(rows["calendar_year"], rows[value], color=colors[group], linewidth=2,
                marker="o", markersize=3.5, label=group)
    _style(ax)


def activity_figure(table: pd.DataFrame, out: Path) -> Path:
    managed = table[table["activity"] != Activity.NONE.label]
    years = sorted(table["calendar_year"].unique())
    groups = sorted(table["owner_group"].unique())
    colors = {g: c for g, c in group_colors(groups).items() if g in groups}
    fig = _figure(8.0, 3.0)
    axes = fig.subplots(1, 2, sharey=True)
    for ax, kind in zip(axes, (Activity.THINNING.label, Activity.CLEARCUT.label)):
        acres = (managed[managed["activity"] == kind]
                 .groupby(["owner_group", "calendar_year"])["acres"].sum()
                 .reindex(pd.MultiIndex.from_product([groups, years],
                                                     names=["owner_group", "calendar_year"]),
                          fill_value=0.0).reset_index())
        _lines(ax, acres, "acres", colors)
        ax.set_title(f"Acres {kind}", fontsize=9, color=INK, loc="left")
        ax.set_xlabel("cycle start year", fontsize=8, color=INK_MUTED)
        ax.set_xlim(years[0] - 2, years[-1] + 2)
    axes[0].set_ylabel("acres", fontsize=8, color=INK_MUTED)
    _legend(fig, colors)
    fig.tight_layout(rect=(0, 0, 1, 0.9))
    return _save(fig, out / "activity_acres_by_cycle.png")


def _legend(fig: Figure, colors: dict[str, str]) -> None:
    handles = [Line2D([], [], color=c, linewidth=2, marker="o", markersize=3.5, label=g)
               for g, c in colors.items()]
    fig.legend(handles=handles, loc="upper right", ncols=len(handles), frameon=False, fontsize=7.5)


def vegetation_figure(table: pd.DataFrame, out: Path) -> Path:
    veg = vegetation_by_cycle(table)
    veg = veg[veg["owner_group"] != "all"]
    groups = sorted(veg["owner_group"].unique())
    colors = {g: c for g, c in group_colors(groups).items() if g in groups}
    panels = (("mean_age", "Mean stand age (years)"), ("mean_ba", "Mean basal area (ft²/ac)"),
              ("standing_mcuft", "Standing merch. volume (ft³)"))
    fig = _figure(10.5, 3.0)
    for ax, (col, title) in zip(fig.subplots(1, 3), panels):
        _lines(ax, veg, col, colors)
        ax.set_title(title, fontsize=9, color=INK, loc="left")
        ax.set_xlabel("year", fontsize=8, color=INK_MUTED)
        years = veg["calendar_year"]
        ax.set_xlim(years.min() - 2, years.max() + 2)
        if col == "standing_mcuft":
            _millions(ax)
    _legend(fig, colors)
    fig.tight_layout(rect=(0, 0, 1, 0.9))
    return _save(fig, out / "vegetation_by_cycle.png")


def preview_index(index_tif: Path) -> np.ndarray:
    """The unit index at most :data:`MAX_MAP_PIXELS` a side (nearest), 0 off every unit."""
    with rasterio.open(index_tif) as src:
        scale = max(src.width, src.height) / MAX_MAP_PIXELS
        shape = ((max(1, round(src.height / scale)), max(1, round(src.width / scale)))
                 if scale > 1 else (src.height, src.width))
        return src.read(1, out_shape=shape, resampling=Resampling.nearest)


def _panels(n: int) -> tuple[int, int]:
    cols = min(n, 4)
    return math.ceil(n / cols), cols


def _map_grid(index: np.ndarray, years: list[int], values, cmap, norm, title: str,
              out: Path, legend=None, colorbar_label: str | None = None) -> Path:
    rows, cols = _panels(len(years))
    aspect = index.shape[0] / max(1, index.shape[1])
    fig = _figure(2.6 * cols, (2.6 * min(max(aspect, 0.4), 1.6) + 0.35) * rows + 0.8)
    axes = np.atleast_1d(fig.subplots(rows, cols, squeeze=False)).ravel()
    image = None
    for ax, year in zip(axes, years):
        data = np.ma.masked_where(index == 0, values[year][index])
        image = ax.imshow(data, cmap=cmap, norm=norm, interpolation="nearest")
        ax.set_title(str(year), fontsize=8, color=INK)
        ax.set_axis_off()
    for ax in axes[len(years):]:
        ax.set_axis_off()
    fig.suptitle(title, fontsize=10, color=INK, x=0.01, ha="left")
    if legend:
        fig.legend(handles=legend, loc="lower center", ncols=len(legend), frameon=False, fontsize=8)
        fig.tight_layout(rect=(0, 0.06, 1, 0.95))
    else:
        fig.tight_layout(rect=(0, 0, 0.9, 0.95))
        cax = fig.add_axes((0.92, 0.15, 0.015, 0.7))
        bar = fig.colorbar(image, cax=cax)
        bar.set_label(colorbar_label or "", fontsize=8, color=INK_MUTED)
        bar.ax.tick_params(labelsize=7, colors=INK_MUTED)
    return _save(fig, out)


def _by_year(table: pd.DataFrame) -> tuple[list[int], dict[int, dict[str, np.ndarray]]]:
    """Each timestep's year and its unit-index lookups (`outputs_stage.cycle_luts`)."""
    luts = cycle_luts(table, int(table["index"].max()))
    years = table.groupby("cycle")["calendar_year"].first().astype(int)
    return years.tolist(), {int(years[c]): lut for c, lut in luts.items()}


def activity_maps(index: np.ndarray, table: pd.DataFrame, out: Path) -> Path:
    years, luts = _by_year(table)
    cmap = ListedColormap(list(ACTIVITY_COLORS.values()))
    norm = BoundaryNorm([-0.5, 0.5, 1.5, 2.5], cmap.N)
    legend = [Patch(facecolor=c, label=a.label) for a, c in ACTIVITY_COLORS.items()]
    return _map_grid(index, years, {y: lut["activity"] for y, lut in luts.items()}, cmap, norm,
                     "Management activity per timestep", out / "map_activity.png", legend=legend)


def measure_maps(index: np.ndarray, table: pd.DataFrame, measure: str, out: Path) -> Path:
    years, luts = _by_year(table)
    lo, hi = float(table[measure].min()), float(table[measure].max())
    norm = Normalize(vmin=lo, vmax=hi if hi > lo else lo + 1)
    return _map_grid(index, years, {y: lut[measure] for y, lut in luts.items()}, SEQUENTIAL, norm,
                     f"{MEASURE_TITLES[measure]} per timestep", out / f"map_{measure}.png",
                     colorbar_label=MEASURE_TITLES[measure])


def _save(fig: Figure, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, facecolor=SURFACE)
    return path


def draw(index_tif: Path, table: pd.DataFrame, qa: Path, harvest: pd.DataFrame) -> list[Path]:
    """Every figure into ``qa/figures``: the charts, then the per-timestep maps."""
    out = qa / FIGURES
    paths = [harvest_figure(harvest, out), activity_figure(table, out), vegetation_figure(table, out)]
    placed = table.dropna(subset=["index"]).astype({"index": int})
    index = preview_index(index_tif)
    paths.append(activity_maps(index, placed, out))
    paths += [measure_maps(index, placed, m, out) for m in VEGETATION_MEASURES]
    return paths
