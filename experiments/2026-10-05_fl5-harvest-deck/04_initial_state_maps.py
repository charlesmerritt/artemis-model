"""Initial-state maps, slides 2-5b: the two published layers, the NWOS holes, and both repairs.

Slide 2   outputs/map_treemap_published.png   TreeMap 2022 as published, live basal area
          outputs/map_nwos_published.png      Harris/NWOS 2022 as published, owner class
Slide 3   outputs/map_nwos_holes.png          published NWOS with its holes painted dark;
          outputs/nwos_hole_circles.json      circles around the densest hole clusters
Slide 4   outputs/map_nwos_improved.png       NWOS with every hole given the nearest owner
Slide 5   outputs/map_treemap_no_treelist.png improved TreeMap, add-back patches with no tree list
Slide 5b  outputs/map_treemap_young.png       the same patches carrying their young FIA plot

A hole is a pixel the improved TreeMap calls forest but NWOS does not carry as forest
(``nwos2022_provenance`` 2 imputed, 3 unresolved). Basal area is the plot's BALIVE, from the
extended VAT for published pixels and ``work/attributes.npz`` for the improved grid.

    uv run python experiments/2026-10-05_fl5-harvest-deck/04_initial_state_maps.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import rasterio
from scipy.ndimage import gaussian_filter

sys.path.insert(0, str(Path(__file__).resolve().parent))
import deck_maps as M  # noqa: E402
import deck_paths as P  # noqa: E402

OWNER_COLORS = {0: "#a3a3a3", 3: "#8f1d33", 4: "#c99a12", 5: "#6a3d9a", 6: "#3d55c9",
                7: "#c273b3", 8: "#0f8a6d"}
HOLE = "#141a16"
MUTE = 0.6                        # owner colours faded toward the surface on the holes slide
NO_TREELIST = "#d9601a"
BA_MAX = 150.0
N_CIRCLES = 6
CIRCLE_RADIUS_PX = 210            # raster pixels, ~6.3 km
NWOS_IMPUTED, NWOS_UNRESOLVED = 2, 3
TM_PUBLISHED, TM_ADDED_YOUNG = 1, 4
NWOS_PROVENANCE_TIF = P.IMPROVED / "nwos2022_provenance.tif"
NWOS_PUBLISHED_TIF = P.IMPROVED / "nwos2022_published.tif"
TREEMAP_PUBLISHED_TIF = P.IMPROVED / "treemap2022_published.tif"


def read(path: Path) -> np.ndarray:
    with rasterio.open(path) as src:
        return src.read(1)


def nwos_rgb(nwos: np.ndarray) -> np.ndarray:
    rgb = M.base_rgb()
    rgb[nwos == 2] = M.hex_rgb(M.WATER)
    for code, color in OWNER_COLORS.items():
        rgb[nwos == code] = M.hex_rgb(color)
    return rgb


def hole_circles(holes: np.ndarray) -> list[dict]:
    """Greedy peaks of smoothed hole density, no two circles overlapping."""
    density = gaussian_filter(holes.astype(np.float32), sigma=CIRCLE_RADIUS_PX / 2, mode="constant")
    # Keep every circle wholly on the map.
    density[:CIRCLE_RADIUS_PX], density[-CIRCLE_RADIUS_PX:] = 0, 0
    density[:, :CIRCLE_RADIUS_PX], density[:, -CIRCLE_RADIUS_PX:] = 0, 0
    rows, cols = np.indices(holes.shape)
    out = []
    for _ in range(N_CIRCLES):
        r, c = np.unravel_index(np.argmax(density), density.shape)
        near = (rows - r) ** 2 + (cols - c) ** 2 <= CIRCLE_RADIUS_PX ** 2
        acres = float(holes[near].sum() * P.viz.CELL_ACRES)
        out.append({"row": int(r), "col": int(c), "radius_px": CIRCLE_RADIUS_PX, "hole_acres": round(acres)})
        density[(rows - r) ** 2 + (cols - c) ** 2 <= (2 * CIRCLE_RADIUS_PX) ** 2] = 0
    return out


def slide_circles(circles: list[dict], shape: tuple[int, int]) -> list[dict]:
    """Circle boxes in slide pixels for a map drawn in deck_maps.ONE_BOX."""
    box = M.ONE_BOX
    sx, sy = box["width"] / shape[1], box["height"] / shape[0]
    for c in circles:
        d = 2 * c["radius_px"] * sx
        c["slide"] = {"left": round(box["left"] + c["col"] * sx - d / 2),
                      "top": round(box["top"] + c["row"] * sy - d / 2), "size": round(d)}
    return circles


def main() -> None:
    A = P.viz.CELL_ACRES
    nwos_pub = read(NWOS_PUBLISHED_TIF)
    nwos_imp = read(P.NWOS_TIF)
    nwos_prov = read(NWOS_PROVENANCE_TIF)
    tm_pub = read(TREEMAP_PUBLISHED_TIF).astype(np.int64)
    tm_prov = read(P.TREEMAP_PROVENANCE_TIF)
    attrs = np.load(P.ATTRIBUTES_NPZ)
    vat = pd.read_csv(P.VAT_CSV).set_index("Value")

    # Slide 2a: published TreeMap, BALIVE of each pixel's plot.
    published = tm_prov == TM_PUBLISHED
    ba_pub = np.zeros(tm_pub.shape, dtype=np.float32)
    ba_pub[published] = vat["BALIVE"].reindex(tm_pub[published]).fillna(0).to_numpy()
    rgb = M.base_rgb()
    M.paint_greens(rgb, published, ba_pub, BA_MAX)
    M.render_pair(rgb, P.OUTPUTS / "map_treemap_published.png")

    # Slide 2b: published NWOS.
    M.render_pair(nwos_rgb(nwos_pub), P.OUTPUTS / "map_nwos_published.png")

    # Slide 3: the holes.
    holes = np.isin(nwos_prov, (NWOS_IMPUTED, NWOS_UNRESOLVED))
    rgb = nwos_rgb(nwos_pub)
    rgb = rgb * (1 - MUTE) + M.hex_rgb(M.BACKGROUND) * MUTE
    rgb[holes] = M.hex_rgb(HOLE)
    M.render_one(rgb, P.OUTPUTS / "map_nwos_holes.png")
    circles = slide_circles(hole_circles(holes), holes.shape)

    # Slide 4: holes filled.
    M.render_one(nwos_rgb(nwos_imp), P.OUTPUTS / "map_nwos_improved.png")

    # Slides 5, 5b: improved TreeMap before and after the young plots.
    valid, young, ba = attrs["valid"], attrs["young"], attrs["BALIVE"]
    rgb = M.base_rgb()
    M.paint_greens(rgb, valid & ~young, ba, BA_MAX)
    rgb[young] = M.hex_rgb(NO_TREELIST)
    M.render_one(rgb, P.OUTPUTS / "map_treemap_no_treelist.png")
    rgb = M.base_rgb()
    M.paint_greens(rgb, valid, ba, BA_MAX)
    M.render_one(rgb, P.OUTPUTS / "map_treemap_young.png")

    stats = {
        "nwos_hole_acres": round(float(holes.sum() * A)),
        "nwos_imputed_acres": round(float((nwos_prov == NWOS_IMPUTED).sum() * A)),
        "nwos_unresolved_acres": round(float((nwos_prov == NWOS_UNRESOLVED).sum() * A)),
        "nwos_holes_on_published_treemap_acres": round(float((holes & published).sum() * A)),
        "treemap_published_forest_acres": round(float(published.sum() * A)),
        "treemap_improved_forest_acres": round(float(valid.sum() * A)),
        "treemap_added_back_acres": round(float(np.isin(tm_prov, (2, TM_ADDED_YOUNG)).sum() * A)),
        "young_plot_acres": round(float(young.sum() * A)),
        "young_ba_mean": round(float(ba[young].mean()), 1),
        "published_ba_mean": round(float(ba_pub[published].mean()), 1),
        "circles": circles,
    }
    (P.OUTPUTS / "initial_state_maps.json").write_text(json.dumps(stats, indent=2))
    print(json.dumps(stats, indent=2))


if __name__ == "__main__":
    main()
