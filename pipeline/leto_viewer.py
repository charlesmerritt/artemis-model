"""An interactive viewer of one county's management units: a single offline HTML page.

The ``leto`` stage writes it per county (``<run_dir>/leto/<FIPS>/qa/viewer.html``) and the
report copies it to ``qa/leto/viewer/<FIPS>.html``, linked from ``qa/leto/index.html``. The
page fetches nothing: its data sits in a ``<script type="application/json" id="leto-data">``
and its code in the template ``leto_viewer.html``. Pan with the mouse, zoom with the wheel,
click a unit for its details.

It is for judging and tuning the segmentation, so it shows the CA's own inputs and outputs:

- **units** (the final management units), coloured by owner class, unit class, forest type,
  origin, stand age, the representative plot's share, size, or parent stand;
- **parent stands** (the CA's stands, before the riparian split and the speckle merge), as
  outlines, each with its cells, acres, owner and the mean and spread of the CA's inputs;
- **cell layers** under them: the CA's inputs per 30 m cell (forest type, stand age, basal
  area, QMD, TPA) and the add-back cells;
- each parent's **donor plots** (exact ``PLT_CN``, weight, share before the small donors
  were dropped), and the run's CA and speckle **settings**.

Outlines are simplified by :data:`TOLERANCE` metres (a buffer arc's vertices; cell edges are
30 m) and stored as whole metres from the county's south-west corner, each vertex as its
difference from the previous one (:func:`encode_geometry`).
"""

from __future__ import annotations

import base64
import io
import json
import math
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
from matplotlib import colormaps
from PIL import Image

TEMPLATE = Path(__file__).with_name("leto_viewer.html")
SCALE = 1                                     # stored units per metre
TOLERANCE = 1.0                               # metres an outline may move when simplified
PALETTE = ["#4e79a7", "#f28e2b", "#59a14f", "#e15759", "#76b7b2", "#edc948", "#b07aa1",
           "#ff9da7", "#9c755f", "#bab0ac", "#86bcb6", "#d4a6c8", "#8cd17d"]
MAX_FOREST_TYPES = 12
CELL_BANDS = {                                # qa_cells.tif band → (layer name, colour map)
    3: ("stand age", "viridis"), 4: ("basal area", "viridis"), 5: ("QMD", "viridis"),
    6: ("TPA", "viridis"),
}
UNIT_COLUMNS = ["unit_id", "owner_class", "unit_class", "origin", "acres", "stand_age", "SEG_ID",
                "PLT_CN", "tm_id", "FORTYPCD", "representative_share", "unknown_ownership"]


# ── geometry ───────────────────────────────────────────────────────────────────────────


def _ring(coords: np.ndarray, origin: tuple[float, float]) -> list[int]:
    xy = np.rint((coords[:-1] - origin) * SCALE).astype(np.int64)        # the ring, unclosed
    keep = np.ones(len(xy), dtype=bool)
    keep[1:] = np.any(xy[1:] != xy[:-1], axis=1)
    xy = xy[keep]
    return np.diff(xy, axis=0, prepend=np.zeros((1, 2), dtype=np.int64)).ravel().tolist()


def encode_geometry(geometry, origin: tuple[float, float], tolerance: float = TOLERANCE) -> list:
    """Every ring of a polygon or multipolygon, delta-encoded from ``origin``; drawn even-odd."""
    shape = geometry.simplify(tolerance, preserve_topology=True) if tolerance else geometry
    parts = getattr(shape, "geoms", [shape])
    rings = []
    for part in parts:
        for ring in (part.exterior, *part.interiors):
            encoded = _ring(np.asarray(ring.coords)[:, :2], np.asarray(origin))
            if len(encoded) >= 6:                                       # at least a triangle
                rings.append(encoded)
    return rings



# ── cell layers ────────────────────────────────────────────────────────────────────────


def _png(rgba: np.ndarray) -> str:
    buffer = io.BytesIO()
    Image.fromarray(rgba, "RGBA").save(buffer, format="PNG", optimize=True)
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


def _hex(rgb) -> str:
    return "#" + "".join(f"{int(round(c * 255)):02x}" for c in rgb[:3])


def _forest_type_layer(forest_type: np.ndarray, forest: np.ndarray) -> dict:
    codes, counts = np.unique(forest_type[forest], return_counts=True)
    top = codes[np.argsort(-counts, kind="stable")][:MAX_FOREST_TYPES]
    rgba = np.zeros((*forest_type.shape, 4), dtype=np.uint8)
    legend = []
    for code, color in zip(sorted(top), PALETTE):
        rgba[forest_type == code] = [*(int(color[i:i + 2], 16) for i in (1, 3, 5)), 255]
        legend.append([str(int(code)), color])
    other = forest & ~np.isin(forest_type, top)
    if other.any():
        rgba[other] = [204, 204, 204, 255]
        legend.append(["other", "#cccccc"])
    return {"name": "forest type", "png": _png(rgba), "legend": legend}


def _ramp_layer(name: str, values: np.ndarray, forest: np.ndarray, cmap: str) -> dict:
    finite = forest & np.isfinite(values)
    lo, hi = (np.percentile(values[finite], [2, 98]) if finite.any() else (0.0, 1.0))
    hi = hi if hi > lo else lo + 1.0
    colors = colormaps[cmap]((np.clip(values, lo, hi) - lo) / (hi - lo))
    rgba = (colors * 255).astype(np.uint8)
    rgba[..., 3] = np.where(finite, 255, 0)
    stops = np.linspace(lo, hi, 5)
    legend = [[f"{v:.4g}", _hex(colormaps[cmap]((v - lo) / (hi - lo)))] for v in stops]
    return {"name": name, "png": _png(rgba), "legend": legend}


def cell_layers(qa_cells: Path) -> tuple[list[dict], list[float]]:
    """The cell layers of ``qa_cells.tif`` as PNGs, and its bounds (west, south, east, north)."""
    with rasterio.open(qa_cells) as src:
        bands = src.read().astype(float)
        bounds = list(src.bounds)
    forest = bands[0] > 0
    layers = [_forest_type_layer(bands[0].astype(np.int64), forest)]
    layers += [_ramp_layer(name, bands[band - 1], forest, cmap) for band, (name, cmap) in CELL_BANDS.items()]
    added = np.zeros((*forest.shape, 4), dtype=np.uint8)
    added[bands[1] > 0] = [214, 39, 40, 255]
    layers.append({"name": "add-back", "png": _png(added), "legend": [["add-back cell", "#d62728"]]})
    return layers, bounds


# ── the page ───────────────────────────────────────────────────────────────────────────


def _table(frame: pd.DataFrame) -> dict:
    rows = frame.astype(object).where(frame.notna(), None).to_numpy().tolist()
    return {"columns": list(frame.columns), "rows": rows}


def _rounded(frame: pd.DataFrame, digits: int = 4) -> pd.DataFrame:
    floats = frame.select_dtypes("float").columns
    return frame.assign(**{c: frame[c].round(digits) for c in floats})


def viewer_data(*, fips: str, name: str, units: gpd.GeoDataFrame, parents: pd.DataFrame,
                parent_shapes: gpd.GeoDataFrame, weights: pd.DataFrame, qa_cells: Path,
                settings: dict) -> dict:
    """The page's data. ``units`` carries ``representative_share``; ``parents`` is the
    county's parent table (by ``SEG_ID``) and ``parent_shapes`` its polygons; ``weights``
    its filtered donor rows."""
    layers, bounds = cell_layers(qa_cells)
    origin = (math.floor(bounds[0]), math.floor(bounds[1]))
    units = units.reset_index(drop=True)
    table = units.rename(columns={"Acres": "acres"})[UNIT_COLUMNS]
    parent_shapes = parent_shapes.set_index("SEG_ID").loc[parents["SEG_ID"]]
    donors = {str(int(seg)): [[row.PLT_CN, int(row.TREEMAP_VALUE), round(float(row.WEIGHT), 4),
                                round(float(row.WEIGHT_ORIGINAL), 4)] for row in rows.itertuples()]
              for seg, rows in weights.groupby("SEG_ID")}
    return {
        "fips": fips, "county": name, "origin": list(origin), "bounds": bounds, "scale": SCALE,
        "palette": PALETTE, "settings": settings,
        "units": _table(_rounded(table)),
        "unit_rings": [encode_geometry(g, origin) for g in units.geometry],
        "parents": _table(_rounded(parents)),
        "parent_rings": [encode_geometry(g, origin) for g in parent_shapes.geometry],
        "donors": donors, "cell_layers": layers,
    }


def write_viewer(path: Path, data: dict) -> Path:
    """The page, its data inlined (``</`` escaped so no string can close the script)."""
    payload = json.dumps(data, separators=(",", ":"), allow_nan=False).replace("</", "<\\/")
    page = (TEMPLATE.read_text().replace("__TITLE__", f"{data['county']} ({data['fips']}) units")
            .replace("__DATA__", payload))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(page)
    return path
