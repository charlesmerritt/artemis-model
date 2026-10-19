"""Shared map rendering for the deck's full-extent FL5 slides.

Every full-extent map is the AOI grid (2919 x 4245, EPSG:5070, 30 m) painted as RGB and saved
at exactly the pixel size of its box on the 1920x1080 slide, times ``SCALE``, so the slide can
place overlays (the hole circles) in slide pixels without guessing at letterboxing.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import geopandas as gpd
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import rasterio  # noqa: E402
from matplotlib.colors import LinearSegmentedColormap  # noqa: E402

import deck_paths as P  # noqa: E402

BACKGROUND = "#f6f4ee"
NON_FOREST = "#e4e0d5"
WATER = "#c9dde8"
COUNTY_LINE = "#3a3a38"
SCALE = 2

# Slide boxes, in slide pixels. ONE: a single map beside a sidebar. PAIR: two maps side by side.
ONE_BOX = {"left": 96, "top": 90, "width": 1309, "height": 900}
PAIR_BOX = {"width": 820, "height": 564}

GREENS = LinearSegmentedColormap.from_list(
    "deck_greens", ["#e3ecca", "#9cc47a", "#4f8a4a", "#1e4d2b"])


def hex_rgb(h: str) -> np.ndarray:
    return np.array([int(h[i:i + 2], 16) for i in (1, 3, 5)], dtype=np.float32) / 255


@lru_cache(maxsize=1)
def grid() -> tuple[np.ndarray, rasterio.coords.BoundingBox]:
    """The outside-the-five-counties mask and the grid bounds."""
    with rasterio.open(P.NWOS_TIF) as src:
        return src.read(1) == src.nodata, src.bounds


@lru_cache(maxsize=1)
def counties() -> gpd.GeoDataFrame:
    c = gpd.read_file(P.viz.COUNTIES_SHP)
    return (c[c["ADMIN_FIPS"].isin(P.viz.PILOT_COUNTY_FIPS)]
            .dissolve(by="NAME", as_index=False).to_crs("EPSG:5070"))


def base_rgb(nonforest: str = NON_FOREST) -> np.ndarray:
    outside, _ = grid()
    rgb = np.empty((*outside.shape, 3), dtype=np.float32)
    rgb[:] = hex_rgb(nonforest)
    rgb[outside] = hex_rgb(BACKGROUND)
    return rgb


def paint_greens(rgb: np.ndarray, mask: np.ndarray, values: np.ndarray, vmax: float) -> None:
    rgb[mask] = GREENS(np.clip(values[mask] / vmax, 0, 1))[:, :3]


def render(rgb: np.ndarray, path: Path, width_px: int, height_px: int,
           labels: bool = True, stamp: str | None = None) -> None:
    """Save the grid at exactly width_px x height_px, county lines over it."""
    _, b = grid()
    dpi = 100
    fig = plt.figure(figsize=(width_px / dpi, height_px / dpi), dpi=dpi)
    ax = fig.add_axes((0, 0, 1, 1))
    fig.patch.set_facecolor(BACKGROUND)
    ax.imshow(rgb, extent=(b.left, b.right, b.bottom, b.top), interpolation="antialiased")
    c = counties()
    lw = width_px / 1300
    c.boundary.plot(ax=ax, color=COUNTY_LINE, linewidth=lw)
    if labels:
        for _, row in c.iterrows():
            p = row.geometry.representative_point()
            ax.text(p.x, p.y, row["NAME"], fontsize=9 * width_px / 1300, ha="center",
                    color="#1f1f1d", weight="bold", family="DejaVu Sans",
                    bbox={"facecolor": BACKGROUND, "edgecolor": "none", "alpha": 0.8, "pad": 2})
    if stamp:
        ax.text(0.985, 0.975, stamp, transform=ax.transAxes, ha="right", va="top",
                fontsize=48 * width_px / 2618, color="#1f2a24", weight="bold", family="DejaVu Sans",
                bbox={"facecolor": BACKGROUND, "edgecolor": "none", "alpha": 0.9, "pad": 6})
    ax.set_xlim(b.left, b.right)
    ax.set_ylim(b.bottom, b.top)
    ax.set_axis_off()
    fig.savefig(path, facecolor=BACKGROUND, dpi=dpi)
    plt.close(fig)


def render_one(rgb: np.ndarray, path: Path, **kw) -> None:
    render(rgb, path, ONE_BOX["width"] * SCALE, ONE_BOX["height"] * SCALE, **kw)


def render_pair(rgb: np.ndarray, path: Path, **kw) -> None:
    render(rgb, path, PAIR_BOX["width"] * SCALE, PAIR_BOX["height"] * SCALE, **kw)
