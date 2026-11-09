"""The ``leto`` stage's QA report: CSV tables, PNG maps and a static ``index.html``.

Every run writes ``<run_dir>/qa/leto/``, which opens offline (``index.html`` links its tables
and maps by relative path and fetches nothing). It follows the ``qa/<stage>/`` convention
``library`` and ``schedule`` will adopt.

**Per county** (:func:`write_county_qa`, inside the county's job so it runs in parallel and is
kept on resume), ``<run_dir>/leto/<FIPS>/qa/`` holds:

- ``parent_sizes.csv``: each parent stand's cells and geodesic acres;
- ``qa_cells.tif``: band 1 each forest cell's forest type, band 2 its add-back flag;
- ``donor_plots_missing.csv``: the county's TreeMap plots with no ``COND`` row in the FIA
  extract (their attributes fell back to TreeMap's value table), exact ``PLT_CN``;
- ``riparian_by_source.csv`` (:func:`riparian_by_source`);
- ``timings.csv``: seconds per step and the peak memory (:class:`MemoryScope`);
- ``figures/<FIPS>_<map>.png``, one per :class:`QaMap`.

**Per run** (:func:`write_report`), from every finished county's own files:

- ``area_accounting.csv`` (written by the stage);
- ``acres_by_class.csv``: units and acres by county × owner class × unit class, masked land
  included (owner class ``masked``);
- ``size_distribution.csv``: per county and for the region, parent stands and units:
  count, acres, median, p10, p90, the number under 1 ac and under 5 ac, and ``one_cell``
  (parents of one cell; units no larger than one cell);
- ``donor_qa.csv``: per county and for the region, kept donor plots per parent, the
  representative plot's share of its parent's cells (:func:`representative_share`), and the
  plots missing from FIA;
- ``donor_plots_missing.csv``, ``riparian_by_source.csv`` and ``timings.csv``: the counties'
  tables, joined;
- ``figures/``: the counties' maps, and ``region_<map>.png``, the region overview;
- ``index.html`` (:func:`write_index`): every CSV and PNG in the folder, so a table a later
  ticket adds shows up without touching this module.
"""

from __future__ import annotations

import math
import resource
import shutil
import time
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
from matplotlib.colors import ListedColormap
from matplotlib.figure import Figure
from matplotlib.patches import Patch
from rasterio import features
from rasterio.merge import merge

from pipeline import leto_layout, qa_report
from pipeline.ids import read_id_csv
from pipeline.leto_riparian import FEET_TO_METERS, SQUARE_METERS_PER_ACRE, BufferRules
from pipeline.leto_speckle import UnitClass
from pipeline.s3_management.owner_classes import MASKED

QA_SUBDIR = "qa"                              # inside a county's folder
AREA_ACCOUNTING = "area_accounting.csv"       # written by the stage, per county and per run
DONOR_WEIGHTS = "donor_weights.csv"           # the stage's filtered parent donor weights
SPECKLE = "speckle.csv"                       # the stage's speckle-merge QA (#167)
PARENT_SIZES = "parent_sizes.csv"
QA_CELLS = "qa_cells.tif"
MISSING_PLOTS = "donor_plots_missing.csv"
RIPARIAN_SOURCES = "riparian_by_source.csv"
TIMINGS = "timings.csv"
MAPS = qa_report.FIGURES                      # the maps, as every qa/<stage>/ keeps its figures
INDEX = qa_report.INDEX
ACRES_BY_CLASS = "acres_by_class.csv"
SIZE_DISTRIBUTION = "size_distribution.csv"
DONOR_QA = "donor_qa.csv"
COUNTY_QA = tuple(Path(QA_SUBDIR) / n for n in (PARENT_SIZES, QA_CELLS, MISSING_PLOTS,
                                                RIPARIAN_SOURCES, TIMINGS))
REGION = "region"                             # the scope of a whole-run row or map
MAX_MAP_PIXELS = 2000                         # a map's long side; larger grids are coarsened
MAX_BOUNDARY_FACTOR = 2                       # parent boundaries drawn up to this coarsening
MAP_DPI = 150
LEGEND_INCHES = 2.5
MAX_FOREST_TYPES = 12                         # the rest share one colour
NO_BUFFER = ("none", "outside every buffer")
LAYER_ORDER = ("flowline", "waterbody", "area")
RIPARIAN_COLUMNS = ["layer", "fcode", "label", "buffer_feet", "pieces", "acres"]


class MemoryScope(StrEnum):
    """What a county's ``peak_rss_mb`` measures."""

    COUNTY = "county"                         # its own worker process: the county's peak
    PROCESS = "process"                       # a process shared with earlier counties: its high-water mark


class QaMap(StrEnum):
    PARENT_STANDS = "parent_stands"           # parent stands over forest type
    OWNER_CLASS = "owner_class"
    RIPARIAN = "riparian"
    STAND_AGE = "stand_age"
    YOUNG_PLOTS = "young_plots"               # add-back cells and young-plot units
    REPRESENTATIVE_SHARE = "representative_share"


class Layer(StrEnum):
    PARENT_STAND = "parent_stand"
    UNIT = "unit"


MAP_TITLES = {
    QaMap.PARENT_STANDS: "Parent stands over forest type",
    QaMap.OWNER_CLASS: "Owner class",
    QaMap.RIPARIAN: "Riparian units",
    QaMap.STAND_AGE: "Stand age (2022)",
    QaMap.YOUNG_PLOTS: "Add-back cells and young-plot units",
    QaMap.REPRESENTATIVE_SHARE: "Representative plot's share of its parent",
}

TABLE_NOTES = {                               # known tables, in the order index.html shows them
    AREA_ACCOUNTING: "Forest cells → parent stands → units, the unit acres split into "
                           "managed, unknown-flagged, riparian and masked, with the drift.",
    SPECKLE: "Speckle merge: units and speckle before and after, merges by step, managed acres "
             "turned grow-only, speckle kept, and owner-class acre drift.",
    ACRES_BY_CLASS: "Units and acres by county × owner class × unit class (masked land included).",
    SIZE_DISTRIBUTION: "Parent-stand and unit sizes. one_cell: parents of one cell, units no "
                       "larger than one cell.",
    DONOR_QA: "Kept donor plots per parent, the representative plot's share of its parent's "
              "cells, and TreeMap plots missing from the FIA extract.",
    MISSING_PLOTS: "TreeMap plots with no COND row in the FIA extract: their attributes came "
                   "from TreeMap's value table.",
    RIPARIAN_SOURCES: "Riparian acres by buffer source. Each riparian piece goes whole to the "
                      "widest buffer holding its representative point.",
    leto_layout.IMPUTATION_SUMMARY: "LETO layout: LETO stage 4's imputation summary.",
    leto_layout.IMPUTATION_DIAGNOSTICS: "LETO layout: LETO stage 4's imputation diagnostics.",
    leto_layout.MISSING_SPECIES: "LETO layout: tree species with no FVS code.",
    leto_layout.MISSING_SPECIES_BY_STAND: "LETO layout: tree species with no FVS code, by stand.",
    TIMINGS: "Seconds per step and peak memory per county. memory_scope 'process': the "
             "high-water mark of a process shared with earlier counties.",
}


# ── timings and memory ─────────────────────────────────────────────────────────────────


class Stopwatch:
    """Seconds per step: each :meth:`lap` closes the step since the previous one."""

    def __init__(self):
        self.started = self._last = time.perf_counter()
        self.seconds: dict[str, float] = {}

    def lap(self, step: str) -> None:
        now = time.perf_counter()
        self.seconds[step] = round(now - self._last, 3)
        self._last = now

    def total(self) -> float:
        return round(time.perf_counter() - self.started, 3)


def peak_rss_mb() -> float:
    """This process's peak resident memory, in MB (Linux reports KB)."""
    return round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1)


# ── tables ─────────────────────────────────────────────────────────────────────────────


def size_row(acres: pd.Series, one_cell: int) -> dict:
    """Count, acres, median, p10, p90 and the small tail of one layer's feature sizes."""
    values = acres.to_numpy(dtype=float)
    p10, median, p90 = (np.percentile(values, [10, 50, 90]) if len(values)
                        else (np.nan, np.nan, np.nan))
    return {"count": len(values), "acres": round(float(values.sum()), 4),
            "median_acres": round(float(median), 4), "p10_acres": round(float(p10), 4),
            "p90_acres": round(float(p90), 4), "under_1_acre": int((values < 1).sum()),
            "under_5_acres": int((values < 5).sum()), "one_cell": int(one_cell)}


def representative_share(weights: pd.DataFrame, keys: tuple[str, ...] = ("SEG_ID",)) -> pd.Series:
    """Per parent (``keys``), the representative plot's share of the parent's cells.

    ``weights`` is the parents' filtered donor rows (:func:`pipeline.leto_donors.filter_weights`);
    the representative plot is a parent's largest kept donor, whose ``WEIGHT_ORIGINAL`` is its
    share before the small donors were dropped.
    """
    keys = list(keys)
    top = (weights.sort_values([*keys, "WEIGHT", "TREEMAP_VALUE"],
                               ascending=[True] * len(keys) + [False, True])
           .drop_duplicates(keys).set_index(keys))
    return top["WEIGHT_ORIGINAL"].astype(float).rename("representative_share")


def _buffer_frame(layers: dict[str, gpd.GeoDataFrame], rules: BufferRules) -> gpd.GeoDataFrame:
    rows = []
    for order, layer in enumerate(LAYER_ORDER):
        frame = layers.get(layer)
        if frame is None or frame.empty:
            continue
        by_code = getattr(rules, layer)
        for code, geom in zip(pd.to_numeric(frame["fcode"]), frame.geometry):
            label, feet = by_code.get(int(code), ("", 0.0))
            if feet > 0 and geom is not None and not geom.is_empty:
                rows.append({"layer": layer, "order": order, "fcode": int(code), "label": label,
                             "buffer_feet": float(feet),
                             "geometry": geom.buffer(feet * FEET_TO_METERS)})
    crs = next((f.crs for f in layers.values() if f is not None), None)
    return gpd.GeoDataFrame(rows, columns=["layer", "order", "fcode", "label", "buffer_feet",
                                           "geometry"], geometry="geometry", crs=crs)


def riparian_by_source(riparian: gpd.GeoDataFrame, layers: dict[str, gpd.GeoDataFrame],
                       rules: BufferRules) -> pd.DataFrame:
    """Riparian acres by buffer source (layer, FCode, label), summing to ``riparian``'s ``Acres``.

    ``riparian`` is the riparian units. Each goes whole to the widest buffer holding its
    representative point, ties to the layer order (flowline, waterbody, area) then the FCode;
    one no buffer holds (a managed speckle merged into it, say) is :data:`NO_BUFFER`.
    """
    if riparian.empty:
        return pd.DataFrame(columns=RIPARIAN_COLUMNS)
    points = gpd.GeoDataFrame({"piece": riparian.index, "acres": riparian["Acres"].to_numpy()},
                              geometry=riparian.geometry.representative_point().to_numpy(),
                              crs=riparian.crs)
    buffers = _buffer_frame(layers, rules)
    hits = gpd.sjoin(points, buffers, how="left", predicate="within")
    hits = (hits.sort_values(["piece", "buffer_feet", "order", "fcode"],
                             ascending=[True, False, True, True], na_position="last")
            .drop_duplicates("piece"))
    unheld = hits["layer"].isna()
    hits.loc[unheld, "layer"], hits.loc[unheld, "label"] = NO_BUFFER
    hits["fcode"] = hits["fcode"].astype("Int64")
    table = (hits.groupby(["layer", "fcode", "label", "buffer_feet"], dropna=False)
             .agg(pieces=("piece", "size"), acres=("acres", "sum")).reset_index())
    table["acres"] = table["acres"].round(4)
    return table[RIPARIAN_COLUMNS]


def acres_by_class(units: pd.DataFrame) -> pd.DataFrame:
    """Units and acres by county × owner class × unit class."""
    table = (units.groupby(["county_fips", "county", "owner_class", "unit_class"])
             .agg(units=("unit_id", "size"), acres=("Acres", "sum")).reset_index())
    table["acres"] = table["acres"].round(4)
    return table


def _one_cell_units(acres: pd.Series, cell_acres: float) -> int:
    return int((acres <= cell_acres * (1 + 1e-3)).sum())    # geodesic acres: projection noise


def size_distribution(parents: dict[str, pd.DataFrame], units: pd.DataFrame,
                      cell_acres: dict[str, float]) -> pd.DataFrame:
    """Parent-stand and unit sizes per county (``parents`` by FIPS) and for the region."""
    rows = []

    def add(scope, county_parents, county_units, acres_per_cell):
        rows.append({"scope": scope, "layer": Layer.PARENT_STAND.value,
                     **size_row(county_parents["acres"], (county_parents["cells"] == 1).sum())})
        rows.append({"scope": scope, "layer": Layer.UNIT.value,
                     **size_row(county_units["Acres"],
                                _one_cell_units(county_units["Acres"], acres_per_cell))})

    for fips, frame in parents.items():
        add(fips, frame, units[units["county_fips"] == fips], cell_acres[fips])
    add(REGION, pd.concat(parents.values()), units, min(cell_acres.values()))
    return pd.DataFrame(rows)


def _donor_row(scope: str, weights: pd.DataFrame, parents: pd.DataFrame,
               missing: pd.DataFrame) -> dict:
    donors = weights.groupby(["county_fips", "SEG_ID"]).size()
    share = parents["representative_share"]
    poor = share < 0.5
    return {"scope": scope, "parent_stands": len(parents),
            "donor_plots_mean": round(float(donors.mean()), 4),
            "donor_plots_median": float(donors.median()),
            "donor_plots_max": int(donors.max()) if len(donors) else 0,
            "representative_share_median": round(float(share.median()), 4),
            "representative_share_p10": round(float(share.quantile(0.1)), 4),
            "representative_share_acre_weighted": round(float(
                np.average(share, weights=parents["acres"])) if len(parents) else np.nan, 4),
            "parents_under_half_share": int(poor.sum()),
            "acres_under_half_share": round(float(parents.loc[poor, "acres"].sum()), 4),
            "plots_missing_from_fia": int(missing["PLT_CN"].nunique()),
            "cells_missing_from_fia": int(missing["cells"].sum()),
            "acres_missing_from_fia": round(float(missing["acres"].sum()), 4)}


def donor_qa(weights: pd.DataFrame, parents: pd.DataFrame, missing: pd.DataFrame) -> pd.DataFrame:
    """Donor QA per county and for the region; every frame carries ``county_fips``.

    ``parents`` has ``acres`` and ``representative_share`` per parent.
    """
    rows = [_donor_row(fips, weights[weights["county_fips"] == fips], frame,
                       missing[missing["county_fips"] == fips])
            for fips, frame in parents.groupby("county_fips", sort=True)]
    rows.append(_donor_row(REGION, weights, parents, missing))
    return pd.DataFrame(rows)


# ── maps ───────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class MapGrid:
    """The rasters a run's (or a county's) maps draw, at most :data:`MAX_MAP_PIXELS` a side."""

    forest_type: np.ndarray                   # 0 off the forest
    added_back: np.ndarray                    # bool
    unit: np.ndarray                          # row of ``units`` + 1; 0 off every unit
    units: pd.DataFrame
    factor: int                               # cells per pixel along a side; 1 is native

    def values(self, column: str) -> np.ndarray:
        """``units[column]`` per pixel, ``None``/NaN off every unit."""
        lookup = np.concatenate([[None], self.units[column].to_numpy(dtype=object)])
        return lookup[self.unit]

    def edges(self, columns: list[str]) -> np.ndarray:
        """Pixels where the unit's ``columns`` key differs from the pixel right or below."""
        keys = np.concatenate([[0], pd.MultiIndex.from_frame(self.units[columns].astype(str))
                               .factorize()[0] + 1])[self.unit]
        edge = np.zeros(keys.shape, dtype=bool)
        edge[:, :-1] |= keys[:, :-1] != keys[:, 1:]
        edge[:-1, :] |= keys[:-1, :] != keys[1:, :]
        return edge & (self.unit > 0)


def map_grid(units: gpd.GeoDataFrame, cells: list[Path], max_pixels: int = MAX_MAP_PIXELS) -> MapGrid:
    """The units rasterised over the counties' ``qa_cells.tif`` mosaic, coarsened to fit."""
    with rasterio.open(cells[0]) as src:
        res = src.res[0]
    bounds = [rasterio.open(p).bounds for p in cells]
    west, south = min(b.left for b in bounds), min(b.bottom for b in bounds)
    east, north = max(b.right for b in bounds), max(b.top for b in bounds)
    factor = max(1, math.ceil(max(east - west, north - south) / res / max_pixels))
    mosaic, transform = merge([str(p) for p in cells], bounds=(west, south, east, north),
                              res=res * factor, nodata=0)
    units = units.reset_index(drop=True)
    index = features.rasterize(((g, i + 1) for i, g in enumerate(units.geometry)),
                               out_shape=mosaic.shape[1:], transform=transform, fill=0,
                               dtype="int32")
    return MapGrid(mosaic[0].astype(np.int64), mosaic[1] > 0, index,
                   pd.DataFrame(units.drop(columns="geometry")), factor)


def _categories(values: np.ndarray, order: list[str], colors: list[str]):
    codes = np.zeros(values.shape, dtype=np.int16)
    for i, name in enumerate(order, start=1):
        codes[values == name] = i
    return codes, ListedColormap(["#ffffff", *colors]), [Patch(color=c, label=n)
                                                          for n, c in zip(order, colors)]


PALETTE = ["#4e79a7", "#f28e2b", "#59a14f", "#e15759", "#76b7b2", "#edc948", "#b07aa1",
           "#ff9da7", "#9c755f", "#bab0ac", "#86bcb6", "#d4a6c8", "#8cd17d"]


def _categorical(grid: MapGrid, column: str):
    values = grid.values(column)
    order = sorted({v for v in values[grid.unit > 0]}, key=str)
    return _categories(values, order, (PALETTE * 2)[:len(order)])


def _forest_types(grid: MapGrid):
    codes, counts = np.unique(grid.forest_type[grid.forest_type > 0], return_counts=True)
    top = codes[np.argsort(-counts, kind="stable")][:MAX_FOREST_TYPES]
    labels = np.where(np.isin(grid.forest_type, top), grid.forest_type.astype(str), "other")
    labels[grid.forest_type == 0] = ""
    order = [str(c) for c in sorted(top)] + (["other"] if len(codes) > len(top) else [])
    colors = PALETTE[:len(top)] + (["#cccccc"] if len(codes) > len(top) else [])
    return _categories(labels, order, colors)


def _young(grid: MapGrid):
    origin = grid.values("origin")
    labels = np.where(grid.forest_type > 0, "other forest", "").astype(object)
    from pipeline.leto_stage import Origin       # the stage imports this module

    labels[(origin == Origin.TREEMAP) & grid.added_back] = "add-back cell, TreeMap unit"
    labels[origin == Origin.YOUNG_PLOT] = "young-plot unit"
    return _categories(labels, ["young-plot unit", "add-back cell, TreeMap unit", "other forest"],
                       ["#d62728", "#ff9896", "#e0e0e0"])


def _riparian(grid: MapGrid):
    labels = np.where(grid.values("owner_class") == MASKED, MASKED,
                      grid.values("unit_class")).astype(object)
    return _categories(labels, [UnitClass.MANAGED, UnitClass.RIPARIAN, MASKED],
                       ["#c9dfb9", "#1f77b4", "#9e9e9e"])


def _continuous(grid: MapGrid, column: str):
    values = pd.to_numeric(pd.Series(grid.values(column).ravel()), errors="coerce").to_numpy()
    return np.ma.masked_invalid(values.reshape(grid.unit.shape))


def _draw(grid: MapGrid, kind: QaMap, ax, fig) -> None:
    ax.imshow(np.where(grid.unit > 0, 1.0, np.nan), cmap=ListedColormap(["#bdbdbd"]),
              interpolation="nearest")                     # a unit with no value: grey
    if kind in (QaMap.STAND_AGE, QaMap.REPRESENTATIVE_SHARE):
        column = "stand_age" if kind == QaMap.STAND_AGE else "representative_share"
        values = _continuous(grid, column)
        vmax = 1.0 if kind == QaMap.REPRESENTATIVE_SHARE else float(
            np.nanpercentile(values.compressed(), 99)) if values.count() else 1.0
        image = ax.imshow(values, cmap="viridis" if kind == QaMap.STAND_AGE else "RdYlGn",
                          vmin=0, vmax=vmax, interpolation="nearest")
        fig.colorbar(image, ax=ax, shrink=0.6, label="years" if kind == QaMap.STAND_AGE else "share")
        return
    codes, cmap, legend = {QaMap.PARENT_STANDS: lambda: _forest_types(grid),
                           QaMap.OWNER_CLASS: lambda: _categorical(grid, "owner_class"),
                           QaMap.RIPARIAN: lambda: _riparian(grid),
                           QaMap.YOUNG_PLOTS: lambda: _young(grid)}[kind]()
    ax.imshow(np.ma.masked_equal(codes, 0), cmap=cmap, vmin=0, vmax=cmap.N - 1,
              interpolation="nearest")
    ax.legend(handles=legend, loc="upper left", bbox_to_anchor=(1.01, 1), fontsize=7, frameon=False,
              title="forest type" if kind == QaMap.PARENT_STANDS else None, title_fontsize=7)


def _overlay(ax, mask: np.ndarray, color: str, alpha: float) -> None:
    if mask.any():
        ax.imshow(np.ma.masked_where(~mask, np.ones(mask.shape)), cmap=ListedColormap([color]),
                  alpha=alpha, interpolation="nearest")


def render_maps(grid: MapGrid, out: Path, scope: str, title: str) -> list[Path]:
    """One PNG per :class:`QaMap` as ``<out>/<scope>_<map>.png``."""
    out.mkdir(parents=True, exist_ok=True)
    parents = grid.edges(["county_fips", "SEG_ID"]) if grid.factor <= MAX_BOUNDARY_FACTOR else None
    counties = grid.edges(["county_fips"]) if grid.units["county_fips"].nunique() > 1 else None
    rows, cols = grid.unit.shape
    width = max(6.0, cols / MAP_DPI)              # one grid pixel per image pixel: boundaries stay
    written = []
    for kind in QaMap:
        fig = Figure(figsize=(width + LEGEND_INCHES, max(3.0, width * rows / max(cols, 1))),
                     dpi=MAP_DPI)
        ax = fig.add_subplot()
        _draw(grid, kind, ax, fig)
        caption = MAP_TITLES[kind]
        if kind == QaMap.PARENT_STANDS:
            if parents is None:
                caption += f" (boundaries not drawn: {grid.factor} cells per pixel)"
            else:
                _overlay(ax, parents, "#000000", 0.55)
        if counties is not None:
            _overlay(ax, counties, "#000000", 0.9)
        ax.set_title(f"{title}: {caption}", fontsize=9)
        ax.set_axis_off()
        path = out / f"{scope}_{kind.value}.png"
        fig.savefig(path, bbox_inches="tight")
        written.append(path)
    return written


# ── one county ─────────────────────────────────────────────────────────────────────────


def write_qa_cells(path: Path, forest_type: np.ndarray, added_back: np.ndarray, transform, crs) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(path, "w", driver="GTiff", height=forest_type.shape[0],
                       width=forest_type.shape[1], count=2, dtype="int16", crs=crs,
                       transform=transform, nodata=0, compress="deflate") as dst:
        dst.write(forest_type.astype(np.int16), 1)
        dst.write(added_back.astype(np.int16), 2)
        dst.set_band_description(1, "forest type")
        dst.set_band_description(2, "add-back")


def with_representative_share(units: gpd.GeoDataFrame, weights: pd.DataFrame) -> gpd.GeoDataFrame:
    """``units`` with each unit's parent's :func:`representative_share`; ``weights`` per county."""
    return units.join(representative_share(weights, ("county_fips", "SEG_ID")),
                      on=["county_fips", "SEG_ID"])


def write_county_qa(out: Path, *, fips: str, name: str, units: gpd.GeoDataFrame,
                    weights: pd.DataFrame, riparian: pd.DataFrame, missing: pd.DataFrame,
                    parent_sizes: pd.DataFrame) -> None:
    """A county's QA files under ``out`` (``<county>/qa``), its maps included.

    ``qa_cells.tif`` must already be there; timings are written last, by the stage.
    """
    out.mkdir(parents=True, exist_ok=True)
    parent_sizes.to_csv(out / PARENT_SIZES, index=False)
    missing.to_csv(out / MISSING_PLOTS, index=False)
    riparian.assign(county_fips=fips, county=name)[["county_fips", "county", *RIPARIAN_COLUMNS]] \
        .to_csv(out / RIPARIAN_SOURCES, index=False)
    mapped = with_representative_share(units, weights.assign(county_fips=fips))
    render_maps(map_grid(mapped, [out / QA_CELLS]), out / MAPS, fips, f"{name} ({fips})")


def write_timings(path: Path, fips: str, name: str, clock: Stopwatch, scope: MemoryScope) -> None:
    row = {"county_fips": fips, "county": name,
           **{f"{step}_seconds": s for step, s in clock.seconds.items()},
           "total_seconds": clock.total(), "peak_rss_mb": peak_rss_mb(), "memory_scope": scope.value}
    pd.DataFrame([row]).to_csv(path, index=False)


# ── the run ────────────────────────────────────────────────────────────────────────────


def _cell_acres(path: Path) -> float:
    with rasterio.open(path) as src:
        return abs(src.transform.a * src.transform.e) / SQUARE_METERS_PER_ACRE


def _concat(paths: list[Path], **kwargs) -> pd.DataFrame:
    frames = [read_id_csv(p, **kwargs) for p in paths]
    return pd.concat([f for f in frames if not f.empty] or frames[:1], ignore_index=True)


def write_report(qa: Path, counties: dict[str, Path], units: gpd.GeoDataFrame) -> None:
    """Every run-level table and map into ``qa`` (``qa/leto``), then ``index.html``.

    ``counties`` maps each finished county's FIPS to its folder; ``units`` is the run's units
    layer. ``area_accounting.csv`` is expected in ``qa`` already.
    """
    county_qa = {fips: d / QA_SUBDIR for fips, d in counties.items()}
    ids = {"id_columns": ("county_fips", "PLT_CN")}
    acres_by_class(units).to_csv(qa / ACRES_BY_CLASS, index=False)

    parents = {fips: pd.read_csv(d / PARENT_SIZES) for fips, d in county_qa.items()}
    cell_acres = {fips: _cell_acres(d / QA_CELLS) for fips, d in county_qa.items()}
    size_distribution(parents, units, cell_acres).to_csv(qa / SIZE_DISTRIBUTION, index=False)

    weights = pd.concat([pd.read_csv(d / DONOR_WEIGHTS, usecols=lambda c: c != "PLT_CN")
                         .assign(county_fips=fips) for fips, d in counties.items()], ignore_index=True)
    missing = _concat([d / MISSING_PLOTS for d in county_qa.values()], **ids)
    missing.to_csv(qa / MISSING_PLOTS, index=False)
    shares = representative_share(weights, ("county_fips", "SEG_ID"))
    parent_frame = pd.concat([f.assign(county_fips=fips) for fips, f in parents.items()])
    parent_frame = parent_frame.join(shares, on=["county_fips", "SEG_ID"])
    donor_qa(weights, parent_frame, missing).to_csv(qa / DONOR_QA, index=False)

    for name, dtype in ((RIPARIAN_SOURCES, {"fcode": "Int64"}), (TIMINGS, {})):
        _concat([d / name for d in county_qa.values()], dtype=dtype, **ids).to_csv(qa / name,
                                                                                  index=False)

    maps = qa / MAPS
    maps.mkdir(parents=True, exist_ok=True)
    for d in county_qa.values():
        for png in sorted((d / MAPS).glob("*.png")):
            shutil.copy2(png, maps / png.name)
    mapped = units.join(shares, on=["county_fips", "SEG_ID"])
    render_maps(map_grid(mapped, [d / QA_CELLS for d in county_qa.values()]), maps, REGION,
                "Region overview")
    write_index(qa)


# ── index.html ─────────────────────────────────────────────────────────────────────────


def _map_scope(png: Path) -> tuple[str | None, str]:
    """``(scope, map)`` of a ``<region or FIPS>_<map>.png``; ``(None, stem)`` for any other map."""
    scope, _, kind = png.stem.partition("_")
    if kind and (scope == REGION or (len(scope) == 5 and scope.isdigit())):
        return scope, kind
    return None, png.stem


def _county_names(qa: Path) -> dict[str, str]:
    path = qa / AREA_ACCOUNTING
    if not path.exists():
        return {}
    frame = pd.read_csv(path, dtype=str, keep_default_na=False)
    return dict(zip(frame.get("county_fips", []), frame.get("county", [])))


def write_index(qa: Path) -> Path:
    """``qa/index.html`` (:func:`pipeline.qa_report.write_index`): every table, then the maps
    in sections: the region overview, any other map, then each county."""
    names = _county_names(qa)

    def group(png: Path) -> qa_report.FigureGroup:
        scope = _map_scope(png)[0]
        if scope == REGION:
            return qa_report.FigureGroup("0", "Region overview")
        if scope is None:
            return qa_report.FigureGroup("1", "Other maps")
        return qa_report.FigureGroup(f"2 {scope}", f"{scope} {names.get(scope, '')}".strip())

    pngs = (qa / MAPS).glob("*.png") if (qa / MAPS).is_dir() else []
    captions = {png.name: MAP_TITLES.get(_map_scope(png)[1], _map_scope(png)[1]) for png in pngs}
    return qa_report.write_index(qa, "LETO QA", {**TABLE_NOTES, **captions},
                                 summary="The leto stage's tables and maps, per county and for "
                                         "the region.", group=group)
