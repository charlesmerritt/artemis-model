"""#167 figures: Union's units before (LETO's own, speckle ``keep``) and after the speckle merge.

    PYTHONPATH=. uv run python experiments/2026-11-09_speckle-merge/make_figures.py --out <demo out>

``--out`` is ``demo.py``'s output folder (``keep/`` and ``merge/`` run folders). Picks the
windows where speckle is densest before the merge, plus the densest window of young-plot
speckle, and writes one before/after PNG per window into ``figures/``.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import geopandas as gpd
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402

from pipeline.leto_speckle import UnitClass  # noqa: E402
from pipeline.leto_stage import UNITS_GPKG, UNITS_LAYER, Origin  # noqa: E402
from pipeline.s3_management.owner_classes import MASKED  # noqa: E402

HERE = Path(__file__).resolve().parent
WINDOW = 1200.0                     # metres a side
MINIMUM = 1.0                       # ac: speckle
SURFACE, INK, MUTED, EDGE = "#fcfcfb", "#0b0b0b", "#52514e", "#7d7c78"
MANAGED, RIPARIAN, YOUNG, SPECKLE = "#e6e4de", "#2a78d6", "#1baf7a", "#eb6834"


def _read(run: Path) -> gpd.GeoDataFrame:
    units = gpd.read_file(run / UNITS_GPKG, layer=UNITS_LAYER)
    return units[units["owner_class"] != MASKED].reset_index(drop=True)


def _densest(units: gpd.GeoDataFrame, mask, n: int, taken: list) -> list[tuple[float, float]]:
    """Lower-left corners of the ``n`` windows holding the most ``mask`` units, not overlapping."""
    pts = units[mask].representative_point()
    x0, y0 = units.total_bounds[:2]
    ix = ((pts.x - x0) // WINDOW).astype(int)
    iy = ((pts.y - y0) // WINDOW).astype(int)
    counts = (ix.astype(str) + "," + iy.astype(str)).value_counts()
    out = []
    for key in counts.index:
        i, j = map(int, key.split(","))
        corner = (x0 + i * WINDOW, y0 + j * WINDOW)
        if all(abs(corner[0] - c[0]) >= WINDOW or abs(corner[1] - c[1]) >= WINDOW
               for c in taken + out):
            out.append(corner)
        if len(out) == n:
            break
    return out


def _panel(ax, units: gpd.GeoDataFrame, corner, title: str) -> None:
    x0, y0 = corner
    view = units.cx[x0:x0 + WINDOW, y0:y0 + WINDOW]
    young = view["origin"] == Origin.YOUNG_PLOT
    riparian = view["unit_class"] == UnitClass.RIPARIAN
    speckle = view["Acres"] < MINIMUM
    for mask, color in ((~young & ~riparian, MANAGED), (young & ~riparian, YOUNG),
                        (riparian, RIPARIAN)):
        part = view[mask & ~speckle]
        if len(part):
            part.plot(ax=ax, color=color, alpha=1.0 if color == MANAGED else 0.38,
                      edgecolor=EDGE, linewidth=0.35)
    if speckle.any():
        view[speckle].plot(ax=ax, color=SPECKLE, edgecolor=SURFACE, linewidth=0.3)
    ax.set_xlim(x0, x0 + WINDOW)
    ax.set_ylim(y0, y0 + WINDOW)
    ax.set_aspect("equal")
    ax.set_xticks([])
    ax.set_yticks([])
    for spine in ax.spines.values():
        spine.set_color("#d4d3cf")
    ax.set_title(f"{title}\n{len(view):,} units, {int(speckle.sum()):,} speckle under 1 ac",
                 fontsize=10, color=INK, loc="left")


def _figure(keep, merge, corner, name: str, label: str) -> Path:
    fig, axes = plt.subplots(1, 2, figsize=(11, 6.1), facecolor=SURFACE)
    _panel(axes[0], keep, corner, "Before: LETO's units (speckle keep)")
    _panel(axes[1], merge, corner, "After: speckle merged (the default)")
    km = WINDOW / 1000
    fig.suptitle(f"Union County (12125), {label}: a {km:.1f} km window", x=0.02, ha="left",
                 fontsize=12, color=INK, fontweight="bold")
    handles = [Patch(facecolor=MANAGED, edgecolor=EDGE, label="Managed unit"),
               Patch(facecolor=YOUNG, alpha=0.38, edgecolor=EDGE, label="Young-plot (add-back) unit"),
               Patch(facecolor=RIPARIAN, alpha=0.38, edgecolor=EDGE, label="Riparian unit (grow only)"),
               Patch(facecolor=SPECKLE, edgecolor=SURFACE, label="Speckle, under 1 ac"),
               Patch(facecolor="white", edgecolor="#d4d3cf", label="No unit (non-forest or masked)")]
    fig.legend(handles=handles, loc="lower center", ncol=5, frameon=False, fontsize=8.5,
               labelcolor=MUTED, bbox_to_anchor=(0.5, 0.035))
    fig.text(0.02, 0.012, "Speckle left after the merge has no allowed neighbour: an isolated "
             "forest patch, a corner-only contact, or riparian speckle beside managed land only.",
             fontsize=8.5, color=MUTED)
    fig.tight_layout(rect=(0, 0.08, 1, 0.97))
    path = HERE / "figures" / f"{name}.png"
    path.parent.mkdir(exist_ok=True)
    fig.savefig(path, dpi=160, facecolor=SURFACE)
    plt.close(fig)
    return path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    keep, merge = _read(args.out / "keep"), _read(args.out / "merge")
    speckle = keep["Acres"] < MINIMUM
    dense = _densest(keep, speckle, 3, [])
    young = _densest(keep, speckle & (keep["origin"] == Origin.YOUNG_PLOT), 1, dense)
    for i, corner in enumerate(dense, 1):
        print(_figure(keep, merge, corner, f"speckle_window_{i}", f"densest speckle #{i}"))
    for corner in young:
        print(_figure(keep, merge, corner, "speckle_window_young", "densest young-plot speckle"))
    print("speckle per unit count:", np.round(speckle.mean(), 3))


if __name__ == "__main__":
    main()
