"""The young-plot step's QA report: ``<run>/qa/young_plots/``, one offline page (#172).

It reads a raster run's ``young_plots/`` (:mod:`~pipeline.s1_initial_state.add_back_ids`)
back and lays out, through :func:`pipeline.qa_report.write_index`, as every ``qa/<stage>/``:

- **Tables:** the six join checks; add-back acres by age source, match level and stand age;
  and, when the run's manifest names its counties, acres by county and age source.
- **Maps:** the add-back pixels by age source, stand age and match level, with county lines.
  A map cell shows its add-back pixels' most common category, or their mean age.
- **Chart:** acres by county, stacked by age source.

    uv run python -m pipeline.young_plot_qa <run>

The ID step's command line writes it after ``young_plots/``.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
import rasterio
from matplotlib.colors import LinearSegmentedColormap, ListedColormap
from matplotlib.figure import Figure
from matplotlib.patches import Patch
from matplotlib.ticker import FuncFormatter

from pipeline import qa_report
from pipeline.ids import read_id_csv
from pipeline.s1_initial_state.add_back_ids import (
    EXTENSION_ID_COLUMNS,
    IMPROVED,
    OUT_DIR,
    PROVENANCE,
    VAT_EXTENSION_CSV,
    AddBackPixels,
    AgeSource,
    acres_by,
    young_plot_pixels,
)
from pipeline.s1_initial_state.finalize_add_back import ACRES_PER_PIXEL
from pipeline.s1_initial_state.run_counties import (
    manifest_counties,
    manifest_county_layer,
    read_counties,
)
from pipeline.s1_initial_state.young_plot_library import MatchLevel

QA_DIR = Path("qa") / "young_plots"
COUNTY_CSV = "add_back_acres_by_county.csv"
OUTSIDE_COUNTIES = "outside listed counties"   # the county of an add-back pixel in no listed county
UNRESOLVED = "unresolved"   # the age source of an add-back pixel whose ID has no VAT extension row
TITLE = "Young plots QA"
SURFACE = "#fcfcfb"                           # the page's image background: the 2px gap colour

# Fixed slots: a colour always means the same category, whichever a run holds.
AGE_SOURCE_COLORS = {str(s): c for s, c in zip(
    [s for s in AgeSource if s is not AgeSource.STRATUM_MEDIAN], qa_report.CATEGORICAL)}
AGE_SOURCE_COLORS[str(AgeSource.STRATUM_MEDIAN)] = qa_report.NEUTRAL   # no detector: the fallback
AGE_SOURCE_COLORS[UNRESOLVED] = qa_report.CATEGORICAL[7]
MATCH_LEVEL_COLORS = {str(m): c for m, c in zip(
    [m for m in MatchLevel if m is not MatchLevel.UNMATCHED], qa_report.CATEGORICAL)}

NOTES = {
    "checks.csv": "The step's six join checks, read back from young_plots/validation.json.",
    COUNTY_CSV: "Add-back acres by county and age source: a pixel's county is the one its "
                "centre falls in.",
    "acres_by_age_source.csv": "What dated each add-back pixel's cut year; stratum_median is "
                               "the fallback for pixels no detector dates.",
    "acres_by_match_level.csv": "How each pixel's young plot was found: its neighbour plot's "
                                "forest type, then group, then the group's parent pool.",
    "acres_by_stand_age.csv": "Add-back acres by 2022 stand age.",
    "age_source.png": "Add-back pixels by age source (each map cell: its most common).",
    "stand_age.png": "Add-back pixels by 2022 stand age (each map cell: the mean).",
    "match_level.png": "Add-back pixels by young-plot match level (each map cell: its most common).",
    "county_age_source.png": "Add-back acres by county, stacked by age source.",
}


# ── tables ─────────────────────────────────────────────────────────────────────────────


def acres_by_county(run_dir: Path, counties: Path, fips: list[str]) -> pd.DataFrame:
    """``<run_dir>/young_plots/``'s add-back acres by county and age source.

    Read back from the written raster and VAT extension, so it counts what ``leto`` will
    read. A pixel belongs to the county its centre falls in; one in no listed county keeps
    an empty ``county_fips`` and :data:`OUTSIDE_COUNTIES`, and one whose ID has no VAT
    extension row the age source :data:`UNRESOLVED`, so the rows sum to every add-back pixel.
    """
    layer = read_counties(counties, fips)
    added = young_plot_pixels(run_dir)
    index = added.rasterized([(geom, i + 1) for i, geom in enumerate(layer.geometry)])
    fips_of = np.array([""] + layer["ADMIN_FIPS"].tolist(), dtype=object)
    name_of = np.array([OUTSIDE_COUNTIES] + layer["NAME"].astype(str).tolist(), dtype=object)
    sources = _per_pixel(added, run_dir, _vat(run_dir), "AGE_SOURCE")
    pixels = pd.DataFrame({"county_fips": fips_of[index], "county": name_of[index],
                           "age_source": sources})
    table = (pixels.groupby(["county_fips", "county", "age_source"]).size()
             .rename("pixels").reset_index())
    table["acres"] = table["pixels"] * ACRES_PER_PIXEL
    return table


def _vat(run_dir: Path) -> pd.DataFrame:
    return read_id_csv(run_dir / OUT_DIR / VAT_EXTENSION_CSV, id_columns=EXTENSION_ID_COLUMNS)


def _per_pixel(added: AddBackPixels, run_dir: Path, vat: pd.DataFrame, column: str) -> np.ndarray:
    """Each add-back pixel's ``column`` from the VAT row of its new ID; :data:`UNRESOLVED` if none."""
    values = added.gather(run_dir / OUT_DIR / IMPROVED).astype(np.int64)
    lookup = pd.Series(vat[column].to_numpy(), index=vat["Value"].astype(np.int64))
    return lookup.reindex(values).fillna(UNRESOLVED).to_numpy()


def _write_tables(run_dir: Path, qa: Path, vat: pd.DataFrame) -> dict:
    report = json.loads((run_dir / OUT_DIR / "validation.json").read_text())
    pd.DataFrame(list(report["checks"].items()), columns=["check", "passed"]).to_csv(
        qa / "checks.csv", index=False)
    for column, name in (("AGE_SOURCE", "age_source"), ("MATCH_LEVEL", "match_level"),
                         ("STAND_AGE_2022", "stand_age")):
        acres_by(vat, column).rename(columns={column: name}).to_csv(
            qa / f"acres_by_{name}.csv", index=False, float_format="%.4f")
    return report


# ── maps ───────────────────────────────────────────────────────────────────────────────


def _cells(rows: np.ndarray, cols: np.ndarray, shape: tuple[int, int], factor: int):
    """Each pixel's map cell (flat index) and the map's shape, ``factor`` pixels a side."""
    out = (math.ceil(shape[0] / factor), math.ceil(shape[1] / factor))
    return (rows // factor) * out[1] + cols // factor, out


def category_codes(labels: np.ndarray, order: list[str]) -> np.ndarray:
    """Each label's 1-based position in ``order``; 0 for a label ``order`` lacks."""
    code = {label: i + 1 for i, label in enumerate(order)}
    return np.array([code.get(label, 0) for label in labels], dtype=np.int64)


def coarse_majority(rows: np.ndarray, cols: np.ndarray, codes: np.ndarray, *,
                    shape: tuple[int, int], factor: int) -> np.ndarray:
    """A map of the most common positive ``code`` among each cell's pixels; ties go to the lower
    code, and a cell with no pixel is 0."""
    cell, out = _cells(rows, cols, shape, factor)
    occupied, inverse = np.unique(cell, return_inverse=True)
    width = int(codes.max(initial=0)) + 1
    counts = np.bincount(inverse * width + codes, minlength=len(occupied) * width)
    grid = np.zeros(out[0] * out[1], dtype=np.int16)
    grid[occupied] = counts.reshape(len(occupied), width).argmax(axis=1)
    return grid.reshape(out)


def coarse_mean(rows: np.ndarray, cols: np.ndarray, values: np.ndarray, *,
                shape: tuple[int, int], factor: int) -> np.ndarray:
    """A map of each cell's pixels' mean ``values``; NaN where a cell has no pixel."""
    cell, out = _cells(rows, cols, shape, factor)
    size = out[0] * out[1]
    n = np.bincount(cell, minlength=size)
    with np.errstate(invalid="ignore", divide="ignore"):
        mean = np.bincount(cell, weights=values.astype(float), minlength=size) / n
    return np.where(n > 0, mean, np.nan).reshape(out)


def _figure(shape: tuple[int, int], extra_inches: float = 2.5) -> tuple[Figure, object]:
    rows, cols = shape
    width = max(6.0, cols / qa_report.MAP_DPI)
    fig = Figure(figsize=(width + extra_inches, max(3.0, width * rows / max(cols, 1))),
                 dpi=qa_report.MAP_DPI, facecolor=SURFACE)
    return fig, fig.add_subplot()


def _render_maps(run_dir: Path, figures: Path, vat: pd.DataFrame, counties) -> None:
    added = young_plot_pixels(run_dir)
    with rasterio.open(run_dir / OUT_DIR / PROVENANCE) as src:
        transform, shape = src.transform, src.shape
    factor = max(1, math.ceil(max(shape) / qa_report.MAX_MAP_PIXELS))
    rows, cols = added.positions()
    map_shape = (math.ceil(shape[0] / factor), math.ceil(shape[1] / factor))
    extent = (transform.c, transform.c + map_shape[1] * factor * transform.a,
              transform.f + map_shape[0] * factor * transform.e, transform.f)
    scale = f"{factor} × {factor} pixels per map cell" if factor > 1 else "one pixel per map cell"

    def finish(fig, ax, name: str, title: str) -> None:
        if counties is not None:
            counties.boundary.plot(ax=ax, color="#52514e", linewidth=0.4)
        ax.set_xlim(extent[0], extent[1])
        ax.set_ylim(extent[2], extent[3])
        ax.set_title(f"{title} ({scale})", fontsize=9, color="#0b0b0b")
        ax.set_axis_off()
        fig.savefig(figures / name, bbox_inches="tight", facecolor=SURFACE)

    for column, name, title, colors in (
            ("AGE_SOURCE", "age_source.png", "Add-back pixels by age source", AGE_SOURCE_COLORS),
            ("MATCH_LEVEL", "match_level.png", "Add-back pixels by match level", MATCH_LEVEL_COLORS)):
        labels = _per_pixel(added, run_dir, vat, column).astype(str)
        order = [c for c in colors if (labels == c).any()]
        codes = category_codes(labels, order)
        image = coarse_majority(rows, cols, codes, shape=shape, factor=factor)
        fig, ax = _figure(map_shape)
        ax.imshow(np.ma.masked_equal(image, 0), extent=extent, interpolation="nearest",
                  cmap=ListedColormap([SURFACE] + [colors[c] for c in order]),
                  vmin=0, vmax=len(order))
        ax.legend(handles=[Patch(color=colors[c], label=c) for c in order], loc="upper left",
                  bbox_to_anchor=(1.01, 1), fontsize=7, frameon=False)
        finish(fig, ax, name, title)

    ages = pd.to_numeric(pd.Series(_per_pixel(added, run_dir, vat, "STAND_AGE_2022")),
                         errors="coerce").to_numpy()
    image = coarse_mean(rows, cols, ages, shape=shape, factor=factor)
    fig, ax = _figure(map_shape)
    shown = ax.imshow(np.ma.masked_invalid(image), extent=extent, interpolation="nearest",
                      cmap=LinearSegmentedColormap.from_list("age", qa_report.SEQUENTIAL),
                      vmin=0, vmax=max(1.0, float(np.nanmax(ages, initial=0))))
    fig.colorbar(shown, ax=ax, shrink=0.6, label="2022 stand age (years)")
    finish(fig, ax, "stand_age.png", "Add-back pixels by stand age")


def _render_county_chart(table: pd.DataFrame, path: Path) -> None:
    """Horizontal bars, one per county, stacked by age source in its fixed slot order."""
    wide = table.pivot_table(index="county", columns="age_source", values="acres",
                             aggfunc="sum", fill_value=0.0)
    wide = wide.loc[wide.sum(axis=1).sort_values().index]
    order = [s for s in AGE_SOURCE_COLORS if s in wide.columns]
    fig = Figure(figsize=(8.5, max(3.0, 0.18 * len(wide) + 1.2)), dpi=qa_report.MAP_DPI,
                 facecolor=SURFACE)
    ax = fig.add_subplot()
    left = np.zeros(len(wide))
    for source in order:
        ax.barh(wide.index, wide[source], left=left, height=0.8, color=AGE_SOURCE_COLORS[source],
                edgecolor=SURFACE, linewidth=1.0, label=source)
        left += wide[source].to_numpy()
    ax.set_xlabel("add-back acres", fontsize=8, color="#52514e")
    ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:,.0f}"))
    ax.tick_params(labelsize=7, colors="#52514e")
    ax.grid(axis="x", color="#d9d8d3", linewidth=0.5)
    ax.set_axisbelow(True)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color("#d9d8d3")
    ax.set_facecolor(SURFACE)
    ax.legend(loc="lower right", fontsize=7, frameon=False)
    ax.set_title("Add-back acres by county and age source", fontsize=9, color="#0b0b0b")
    fig.savefig(path, bbox_inches="tight", facecolor=SURFACE)


# ── the report ─────────────────────────────────────────────────────────────────────────


def write_young_plot_qa(run_dir: Path) -> Path:
    """Write ``<run_dir>/qa/young_plots/``: its tables, maps and ``index.html``."""
    qa = run_dir / QA_DIR
    figures = qa / qa_report.FIGURES
    figures.mkdir(parents=True, exist_ok=True)
    vat = _vat(run_dir)
    report = _write_tables(run_dir, qa, vat)
    layer, counties = manifest_county_layer(run_dir), None
    if layer is not None:
        fips = manifest_counties(run_dir)
        counties = read_counties(layer, fips)
        table = acres_by_county(run_dir, layer, fips)
        table.to_csv(qa / COUNTY_CSV, index=False, float_format="%.4f")
        _render_county_chart(table, figures / "county_age_source.png")
    _render_maps(run_dir, figures, vat, counties)
    counts = report["counts"]
    passed = sum(report["checks"].values())
    summary = (f"{run_dir}: {counts['add_back_pixels']:,} add-back pixels "
               f"({counts['add_back_pixels'] * ACRES_PER_PIXEL:,.0f} ac) under {counts['new_ids']:,} "
               f"new IDs from {counts['young_plots']:,} young plots; "
               f"{passed} of {len(report['checks'])} checks pass.")
    return qa_report.write_index(qa, TITLE, NOTES, summary)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("run_dir", type=Path, help="a raster run folder holding young_plots/")
    print(write_young_plot_qa(parser.parse_args().run_dir))


if __name__ == "__main__":
    main()
