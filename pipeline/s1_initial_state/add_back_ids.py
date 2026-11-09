"""New TreeMap IDs for add-back pixels: a dated stand age and a young plot each (ADR 0003, #88).

After this step an add-back pixel holds everything any other TreeMap pixel does: a ``TM_ID``
with a full VAT row and a tree list. It is a second pass over a finished run folder, like
:mod:`add_back_stand_age`, and never changes the run's own files:

    uv run python -m pipeline.s1_initial_state.add_back_ids <run>/aoi_5county [--obata-tif <lastDist.tif>] [--hansen-tif <hansen.tif>] [--landfire-dist-dir <landfire/>] [--data-root data/r2_cache]

Each detector the run used dates its pixels: one not passed comes from the run's ``manifest.json``.
LANDFIRE disturbance proposes no pixel, so no run uses it: it dates only when passed.

Every add-back pixel of the run (provenance 2 or 4: either ADR 0002 mode) gets:

1. **A stand age.** From its cut year (:func:`add_back_stand_age.cut_year`: any detector year
   from 2001 to 2022, whatever the run's window). An undated pixel takes the median of the
   dated ones in its bookend stratum. Either way it carries an :class:`AgeSource`.
2. **A young plot.** :meth:`YoungPlotLibrary.match` on its neighbour plot (the ``TM_ID``
   the run stamped there), the stand age and the neighbour plot's species mix. A pixel with
   none is :class:`UnmatchedYoungPlots`, and nothing is written.
3. **A new ID.** One per distinct :data:`NEW_ID_KEY`, from :data:`NEW_ID_START` in sorted key order.

Writes into ``<run>/young_plots/``:

- ``treemap2022_improved.tif``: the run's raster, each add-back pixel under its new ID.
- ``treemap2022_provenance.tif``: the run's, with every add-back pixel provenance 4.
- ``treemap2022_improved_vat_extension.csv``: one row per new ID, the :data:`VAT_COLUMNS`
  of its young plot, then :data:`EXTENSION_COLUMNS`. ``TM_ID`` equals ``Value``.
- ``treemap2022_improved_tree_extension.csv``: each new ID's trees (:meth:`YoungPlotLibrary.tree_list`).
- ``validation.json``: whether the four join (:func:`validate`), the acres by stand age,
  match level and age source, and the detector inputs that dated the cut years.

Control numbers stay strings end to end: read the CSVs with :func:`pipeline.ids.read_id_csv`
and :data:`EXTENSION_ID_COLUMNS`.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

import numpy as np
import pandas as pd
import rasterio
from rasterio import windows
from rasterio.transform import Affine
from rasterio.windows import Window

from pipeline.ids import as_id_series, read_id_csv
from pipeline.s1_initial_state.add_back_methods import AddBackMethod
from pipeline.s1_initial_state.add_back_stand_age import (
    UNDATED,
    CutYearDetector,
    read_cut_year,
    stand_age,
)
from pipeline.s1_initial_state.county_improvement import Grid, TreeMapProvenance
from pipeline.s1_initial_state.finalize_add_back import ACRES_PER_PIXEL
from pipeline.spatial_ref import project_crs
from pipeline.s1_initial_state.young_plot_library import (
    MatchLevel,
    YoungPlotLibrary,
    YoungPlotMatch,
    live_trees,
    read_young_plot_library,
    species_mix,
)

OUT_DIR = "young_plots"
VAT_EXTENSION_CSV = "treemap2022_improved_vat_extension.csv"
TREE_EXTENSION_CSV = "treemap2022_improved_tree_extension.csv"
COUNTY_CSV = "add_back_acres_by_county.csv"
OUTSIDE_COUNTIES = "outside listed counties"   # the county of an add-back pixel in no listed county
UNRESOLVED = "unresolved"   # the age source of an add-back pixel whose ID has no VAT extension row
IMPROVED = "treemap2022_improved.tif"
PROVENANCE = "treemap2022_provenance.tif"
STRIP_ROWS = 1024            # rows read at a time: a statewide grid is never held whole (#172)
NEW_ID_START = 1_000_000     # above TreeMap 2022's own values (at most 70,035)
YOUNG_PLOTS_PROVENANCE = ("0 water, 1 published, 3 unmapped land, "
                          "4 added back: a young plot under a new TM_ID (ADR 0003)")
# TreeMap 2022's VAT, all 27 columns. A new row copies them from its young plot, except
# Value and TM_ID (the new ID) and Count (the new ID's pixels).
VAT_COLUMNS = ["Value", "TM_ID", "PLT_CN", "Count", "FORTYPCD", "ForTypName", "FLDTYPCD",
               "FldTypName", "STDSZCD", "FLDSZCD", "BALIVE", "ALSTK", "GSSTK", "QMD", "SDIsum",
               "TPA_LIVE", "TPA_DEAD", "VOLCFNET_L", "VOLCFNET_D", "VOLBFNET_L", "DRYBIO_L",
               "DRYBIO_D", "CARBON_L", "CARBON_D", "CARBON_DWN", "STANDHT", "CANOPYPCT"]
# What a new row adds: its age (authoritative, unlike the young plot's FIA age), how the
# young plot was found, and the two plots behind it.
EXTENSION_COLUMNS = ["STAND_AGE_2022", "CUT_YEAR", "AGE_SOURCE", "MATCH_LEVEL", "AGE_GAP",
                     "YOUNG_TM_ID", "YOUNG_PLT_CN", "NEIGHBOUR_TM_ID", "NEIGHBOUR_PLT_CN"]
EXTENSION_ID_COLUMNS = ("TM_ID", "PLT_CN", "YOUNG_TM_ID", "YOUNG_PLT_CN",
                        "NEIGHBOUR_TM_ID", "NEIGHBOUR_PLT_CN")   # read back as exact strings
# One new ID per distinct key (ADR 0003 amendment). The young plot follows from the
# neighbour plot and the stand age, and the stand age from the cut year, except where the
# age source is a stratum median.
NEW_ID_KEY = ["NEIGHBOUR_TM_ID", "YOUNG_TM_ID", "STAND_AGE_2022", "CUT_YEAR", "AGE_SOURCE"]


class AgeSource(StrEnum):
    """What set a pixel's stand age: the detectors dating its cut year, or its stratum's median."""

    OBATA = "obata"
    HANSEN = "hansen"
    OBATA_HANSEN = "obata_hansen"
    LANDFIRE = "landfire"
    OBATA_LANDFIRE = "obata_landfire"
    HANSEN_LANDFIRE = "hansen_landfire"
    OBATA_HANSEN_LANDFIRE = "obata_hansen_landfire"
    STRATUM_MEDIAN = "stratum_median"


def _age_source(bits: CutYearDetector) -> AgeSource:
    """The detectors' names joined in bit order; undated (no bit) is a stratum median."""
    names = [str(d.name).lower() for d in CutYearDetector if d in bits]
    return AgeSource("_".join(names)) if names else AgeSource.STRATUM_MEDIAN


AGE_SOURCE_BY_DETECTOR = {CutYearDetector(bits): _age_source(CutYearDetector(bits))
                          for bits in range(1 << len(CutYearDetector))}


def pixel_stand_ages(years: np.ndarray, detector: np.ndarray,
               strata: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Each add-back pixel's stand age and :class:`AgeSource`, from its cut year and detector bits.

    An undated pixel (no detector year: proposed by the bookends alone) takes the median
    stand age of the dated pixels in its bookend stratum. It is the lower median, so an age
    some dated pixel has. A stratum with undated pixels and no dated one is an error.
    """
    lookup = np.array([AGE_SOURCE_BY_DETECTOR[CutYearDetector(bits)]
                       for bits in range(len(AGE_SOURCE_BY_DETECTOR))], dtype=object)
    ages = stand_age(years)
    undated = ages == UNDATED
    for stratum in np.unique(strata[undated]):
        in_stratum = strata == stratum
        dated_ages = ages[in_stratum & ~undated]
        if not dated_ages.size:
            raise ValueError(f"stratum {stratum}: {int((in_stratum & undated).sum())} undated "
                             "add-back pixels and no dated one to take a median stand age from")
        ages[in_stratum & undated] = np.quantile(dated_ages, 0.5, method="lower")
    return ages, lookup[np.asarray(detector)]


def number_keys(pixels: pd.DataFrame, treemap_ids: Iterable[str]) -> tuple[pd.DataFrame, np.ndarray]:
    """One new ID per distinct :data:`NEW_ID_KEY` among ``pixels``, from :data:`NEW_ID_START` in key order.

    Keys sort column by column, IDs as strings and an undated pixel's missing cut year last.
    Returns the keys (``Value``, ``TM_ID``, the :data:`NEW_ID_KEY` columns, ``pixels``) and each
    pixel's ``Value``. An ID TreeMap already uses (``treemap_ids``) is refused.
    """
    groups = pixels.groupby(NEW_ID_KEY, sort=True, dropna=False)
    keys = groups.size().rename("pixels").reset_index()
    keys.insert(0, "Value", NEW_ID_START + np.arange(len(keys), dtype=np.int64))
    keys.insert(1, "TM_ID", as_id_series(keys["Value"], column="TM_ID"))
    taken = sorted(set(keys["TM_ID"]) & set(treemap_ids))
    if taken:
        raise ValueError(f"new IDs collide with TreeMap values: {', '.join(taken[:5])}")
    keys["AGE_SOURCE"] = keys["AGE_SOURCE"].map(str)
    return keys, (NEW_ID_START + groups.ngroup().to_numpy()).astype(np.uint32)


# ── the step on a run folder ────────────────────────────────────────────────────────────


class UnmatchedYoungPlots(ValueError):
    """Add-back pixels with no young plot: an error, never a partial VAT row (ADR 0003 amendment)."""


class TreelessYoungPlots(ValueError):
    """Add-back pixels whose young plot has no tree to give them: a new ID needs a tree list."""


@dataclass(frozen=True)
class AddBackIds:
    """Each add-back pixel's new ``Value``, and the rows the new IDs resolve to."""

    values: np.ndarray            # uint32, aligned with the add-back pixels passed in
    vat_extension: pd.DataFrame
    tree_extension: pd.DataFrame


def add_back_ids(pixels: pd.DataFrame, vat: pd.DataFrame, library: YoungPlotLibrary,
                 neighbour_trees: pd.DataFrame) -> AddBackIds:
    """New IDs for add-back pixels (``NEIGHBOUR_TM_ID``, ``STAND_AGE_2022``, ``CUT_YEAR``, ``AGE_SOURCE``).

    ``vat`` is TreeMap's VAT (:data:`VAT_COLUMNS`, IDs as strings); ``neighbour_trees`` the
    neighbour plots' live trees, for their species mix.
    """
    vat_by_id = vat.set_index("TM_ID", drop=False)
    matches = _match(pixels, vat_by_id, library, neighbour_trees)
    pixels = pixels.assign(YOUNG_TM_ID=[m.tm_id for m in _matches_of(pixels, matches)])
    keys, values = number_keys(pixels, vat["TM_ID"])
    key_matches = _matches_of(keys, matches)
    trees = _tree_extension(keys, library, key_matches)
    treeless = ~keys["TM_ID"].isin(trees["TM_ID"]).to_numpy()
    if treeless.any():
        plots = sorted({(m.tm_id, m.plt_cn) for m, empty in zip(key_matches, treeless) if empty})
        raise TreelessYoungPlots(
            f"{keys.loc[treeless, 'pixels'].sum()} add-back pixels would get a young plot with "
            "no trees: " + ", ".join(f"TM_ID {tm_id} (PLT_CN {plt_cn})" for tm_id, plt_cn in plots))
    return AddBackIds(values, _vat_extension(keys, vat_by_id, key_matches), trees)


def _matches_of(rows: pd.DataFrame, matches: dict[tuple[str, int], YoungPlotMatch]) -> list[YoungPlotMatch]:
    """The young-plot match of each row's (neighbour plot, stand age)."""
    return [matches[pair] for pair in zip(rows["NEIGHBOUR_TM_ID"], rows["STAND_AGE_2022"])]


def _match(pixels: pd.DataFrame, vat_by_id: pd.DataFrame, library: YoungPlotLibrary,
           neighbour_trees: pd.DataFrame) -> dict[tuple[str, int], YoungPlotMatch]:
    """The young plot of each (neighbour plot, stand age) among ``pixels``."""
    mixes = {tm_id: species_mix(trees) for tm_id, trees in neighbour_trees.groupby("TM_ID")}
    pairs = pixels.groupby(["NEIGHBOUR_TM_ID", "STAND_AGE_2022"]).size()
    fortypcd = {tm_id: int(vat_by_id.at[tm_id, "FORTYPCD"]) for tm_id, _ in pairs.index}
    matches = {(tm_id, age): library.match(fortypcd[tm_id], int(age),
                                           mixes.get(tm_id, pd.Series(dtype=float)))
               for tm_id, age in pairs.index}
    unmatched = pd.Series({pair: n for pair, n in pairs.items()
                           if matches[pair].level is MatchLevel.UNMATCHED}, dtype=np.int64)
    if len(unmatched):
        by_type = unmatched.groupby([fortypcd[tm_id] for tm_id, _ in unmatched.index]).sum()
        raise UnmatchedYoungPlots(
            f"{unmatched.sum()} add-back pixels have no young plot of their neighbour plot's "
            "forest type or group within 2 years of their stand age: "
            + ", ".join(f"FORTYPCD {code}: {n} px" for code, n in by_type.items()))
    return matches


def _vat_extension(keys: pd.DataFrame, vat_by_id: pd.DataFrame,
                   key_matches: list[YoungPlotMatch]) -> pd.DataFrame:
    """One VAT row per new ID: its young plot's row under the new ``Value``/``TM_ID``, then
    :data:`EXTENSION_COLUMNS`."""
    young = vat_by_id.loc[keys["YOUNG_TM_ID"], VAT_COLUMNS].reset_index(drop=True)
    rows = young.assign(
        Value=keys["Value"], TM_ID=keys["TM_ID"], Count=keys["pixels"],
        STAND_AGE_2022=keys["STAND_AGE_2022"], CUT_YEAR=keys["CUT_YEAR"],
        AGE_SOURCE=keys["AGE_SOURCE"],
        MATCH_LEVEL=[str(m.level) for m in key_matches],
        AGE_GAP=[m.age_gap for m in key_matches],
        YOUNG_TM_ID=keys["YOUNG_TM_ID"], YOUNG_PLT_CN=young["PLT_CN"],
        NEIGHBOUR_TM_ID=keys["NEIGHBOUR_TM_ID"],
        NEIGHBOUR_PLT_CN=vat_by_id.loc[keys["NEIGHBOUR_TM_ID"], "PLT_CN"].to_numpy())
    return rows[VAT_COLUMNS + EXTENSION_COLUMNS]


def _tree_extension(keys: pd.DataFrame, library: YoungPlotLibrary,
                    key_matches: list[YoungPlotMatch]) -> pd.DataFrame:
    """Each new ID's tree list: its young plot's trees, plus its seedlings at stand age 0-2."""
    lists = [library.tree_list(match, age).assign(TM_ID=tm_id)
             for tm_id, age, match in zip(keys["TM_ID"], keys["STAND_AGE_2022"], key_matches)]
    return pd.concat(lists, ignore_index=True)


DETECTOR_INPUTS = {AddBackMethod.OBATA_DISTURBANCE: ("obata", "--obata-tif"),
                   AddBackMethod.HANSEN_LOSS: ("hansen", "--hansen-tif")}   # manifest input, CLI flag


def run_detectors(run_dir: Path, obata: Path | None,
                  hansen: Path | None) -> tuple[Path | None, Path | None]:
    """The detector rasters that date a run's pixels: every detector the run used.

    A raster passed in wins. A detector the run's ``manifest.json`` says ran, and that was
    not passed, falls back to the manifest's input path; if that is gone, it is an error,
    since without it every pixel only that detector dates would take a stratum median.
    A run folder without a manifest dates with what it is given.
    """
    given = {"obata": obata, "hansen": hansen}
    manifest_path = run_dir / "manifest.json"
    if not manifest_path.exists():
        return obata, hansen
    manifest = json.loads(manifest_path.read_text())
    for method, (name, flag) in DETECTOR_INPUTS.items():
        if manifest.get("methods", {}).get(method) != "run" or given[name] is not None:
            continue
        recorded = manifest["inputs"].get(name)
        if recorded is None or not Path(recorded).exists():
            raise ValueError(f"the run used {method}, so its cut years must too: pass {flag} "
                             f"(the manifest's {recorded} is not here)")
        given[name] = Path(recorded)
    return given["obata"], given["hansen"]


def write_add_back_ids(run_dir: Path, *, vat: pd.DataFrame, library: YoungPlotLibrary,
                       tree_table: Path, obata: Path | None = None,
                       hansen: Path | None = None, landfire: Path | None = None) -> dict:
    """Give a run folder's add-back pixels new IDs; write them under ``<run_dir>/young_plots/``.

    Returns :func:`validate`'s report, also written as ``validation.json``. Obata and Hansen
    not passed come from the run's manifest (:func:`run_detectors`). ``landfire``, the
    folder of LANDFIRE disturbance products, dates pixels only when given.
    """
    obata, hansen = run_detectors(run_dir, obata, hansen)
    added = AddBackPixels.of(run_dir / PROVENANCE, TreeMapProvenance.added_back)
    years, detector = added.cut_years(obata=obata, hansen=hansen, landfire=landfire)
    ages, sources = pixel_stand_ages(years, detector, added.gather(run_dir / "bookend_strata.tif"))
    neighbours = added.gather(run_dir / IMPROVED)
    pixels = pd.DataFrame({"NEIGHBOUR_TM_ID": as_id_series(pd.Series(neighbours), column="TM_ID"),
                           "STAND_AGE_2022": ages,
                           "CUT_YEAR": pd.Series(years, dtype="Int64").where(years > 0),
                           "AGE_SOURCE": sources})
    neighbour_trees = live_trees(tree_table, set(pixels["NEIGHBOUR_TM_ID"]))
    ids = add_back_ids(pixels, vat, library, neighbour_trees)

    out = run_dir / OUT_DIR
    out.mkdir(exist_ok=True)
    with rasterio.open(run_dir / IMPROVED) as src:
        nodata = src.nodata
    added.rewrite(run_dir / IMPROVED, out / IMPROVED, ids.values, nodata=nodata,
                  description="TreeMap 2022 TM_ID; an add-back pixel's new TM_ID resolves in "
                              + VAT_EXTENSION_CSV)
    added.rewrite(run_dir / PROVENANCE, out / PROVENANCE, TreeMapProvenance.ADDED_BACK_YOUNG,
                  nodata=255, description=YOUNG_PLOTS_PROVENANCE)
    ids.vat_extension.to_csv(out / VAT_EXTENSION_CSV, index=False)
    ids.tree_extension.to_csv(out / TREE_EXTENSION_CSV, index=False)
    report = validate(run_dir, vat["TM_ID"])
    report["cut_year_detectors"] = {name: None if path is None else str(path) for name, path in
                                    (("obata", obata), ("hansen", hansen), ("landfire", landfire))}
    (out / "validation.json").write_text(json.dumps(report, indent=2))
    counties = manifest_counties(run_dir)
    if counties is not None:
        acres_by_county(run_dir, *counties).to_csv(out / COUNTY_CSV, index=False,
                                                   float_format="%.4f")
    return report


def manifest_counties(run_dir: Path) -> tuple[Path, list[str]] | None:
    """The county layer and FIPS a run's ``manifest.json`` records; ``None`` if it records none.

    The manifest lists counties as ``"<FIPS> <name>"``, where the name may be the FIPS again:
    only the FIPS is read, and names come from the layer.
    """
    path = run_dir / "manifest.json"
    if not path.exists():
        return None
    manifest = json.loads(path.read_text())
    layer, listed = manifest.get("inputs", {}).get("counties"), manifest.get("counties")
    if not layer or not listed:
        return None
    return Path(layer), [str(entry).split()[0] for entry in listed]


def acres_by_county(run_dir: Path, counties: Path, fips: list[str]) -> pd.DataFrame:
    """``<run_dir>/young_plots/``'s add-back acres by county and :class:`AgeSource`.

    Read back from the written raster and VAT extension, so it counts what ``leto`` will
    read. A pixel belongs to the county its centre falls in; one in no listed county keeps
    an empty ``county_fips`` and :data:`OUTSIDE_COUNTIES`, and one whose ID has no VAT
    extension row the age source :data:`UNRESOLVED`, so the rows sum to every add-back pixel.
    """
    malformed = [f for f in fips if not (len(f) == 5 and f.isdigit())]
    if malformed:
        raise ValueError(f"a county FIPS is 5 digits, not {malformed[0]!r}")
    import geopandas as gpd

    from pipeline.spatial_ref import to_project_crs

    out = run_dir / OUT_DIR
    added = AddBackPixels.of(out / PROVENANCE, _young)
    values = added.gather(out / IMPROVED)
    quoted = ", ".join(f"'{f}'" for f in fips)
    layer = to_project_crs(gpd.read_file(counties, where=f"ADMIN_FIPS IN ({quoted})"))
    layer = layer.dissolve(by="ADMIN_FIPS", aggfunc="first").reset_index()
    index = added.rasterized([(geom, i + 1) for i, geom in enumerate(layer.geometry)])
    fips_of = np.array([""] + layer["ADMIN_FIPS"].astype(str).tolist(), dtype=object)
    name_of = np.array([OUTSIDE_COUNTIES] + layer["NAME"].astype(str).tolist(), dtype=object)
    vat = read_id_csv(out / VAT_EXTENSION_CSV, id_columns=EXTENSION_ID_COLUMNS)
    source_of = pd.Series(vat["AGE_SOURCE"].to_numpy(), index=vat["Value"].astype(np.int64))
    pixels = pd.DataFrame({"county_fips": fips_of[index], "county": name_of[index],
                           "age_source": source_of.reindex(values.astype(np.int64)).fillna(UNRESOLVED).to_numpy()})
    table = (pixels.groupby(["county_fips", "county", "age_source"]).size()
             .rename("pixels").reset_index())
    table["acres"] = table["pixels"] * ACRES_PER_PIXEL
    return table


def validate(run_dir: Path, treemap_ids: Iterable[str]) -> dict:
    """Read ``<run_dir>/young_plots/`` back: does it join, and how many acres by age and match?"""
    out = run_dir / OUT_DIR
    added = AddBackPixels.of(out / PROVENANCE, _young)
    vat = read_id_csv(out / VAT_EXTENSION_CSV, id_columns=EXTENSION_ID_COLUMNS)
    trees = read_id_csv(out / TREE_EXTENSION_CSV)
    values, pixels = np.unique(added.gather(out / IMPROVED), return_counts=True)
    checks = {
        "every_add_back_pixel_resolves_to_a_vat_row": bool(np.isin(values, vat["Value"]).all()),
        "every_vat_row_is_full": bool(vat[VAT_COLUMNS].notna().all(axis=None)),
        "every_new_id_has_trees": set(vat["TM_ID"]) <= set(trees["TM_ID"]),
        "no_new_id_is_a_treemap_value": not set(vat["TM_ID"]) & set(treemap_ids),
        "vat_counts_match_the_raster":
            pd.Series(pixels, index=values).reindex(vat["Value"]).tolist() == vat["Count"].tolist(),
        "other_pixels_unchanged": added.others_equal(out / IMPROVED, run_dir / IMPROVED),
    }

    def acres(column: str) -> dict[str, float]:
        totals = vat.groupby(column)["Count"].sum() * ACRES_PER_PIXEL
        return {str(k): round(float(v), 4) for k, v in totals.items()}

    return {
        "checks": checks,
        "counts": {"add_back_pixels": added.count, "new_ids": len(vat),
                   "young_plots": int(vat["YOUNG_TM_ID"].nunique()),
                   "neighbour_plots": int(vat["NEIGHBOUR_TM_ID"].nunique())},
        "acres_by_stand_age": acres("STAND_AGE_2022"),
        "acres_by_match_level": acres("MATCH_LEVEL"),
        "acres_by_age_source": acres("AGE_SOURCE"),
    }


def _young(provenance: np.ndarray) -> np.ndarray:
    return provenance == TreeMapProvenance.ADDED_BACK_YOUNG


def _strips(height: int, width: int) -> Iterator[Window]:
    """Full-width bands of :data:`STRIP_ROWS` rows, top to bottom."""
    for row in range(0, height, STRIP_ROWS):
        yield Window(0, row, width, min(STRIP_ROWS, height - row))


@dataclass(frozen=True)
class AddBackPixels:
    """Where a run's add-back pixels are, one strip of rows at a time.

    Every raster is read a strip at a time and only these pixels are kept, so a statewide
    grid (618M pixels) is never held whole. Values come back in row-major order, as
    indexing a whole band by a mask would give them.
    """

    transform: Affine
    strips: tuple[tuple[Window, np.ndarray], ...]   # each strip and its pixels' flat indices in it

    @classmethod
    def of(cls, provenance: Path, chosen: Callable[[np.ndarray], np.ndarray]) -> AddBackPixels:
        """The pixels of ``provenance`` that ``chosen`` (a mask of a provenance block) picks."""
        with rasterio.open(provenance) as src:
            strips = tuple((w, np.flatnonzero(chosen(src.read(1, window=w))))
                           for w in _strips(src.height, src.width))
            return cls(src.transform, strips)

    @property
    def count(self) -> int:
        return sum(len(index) for _, index in self.strips)

    def gather(self, path: Path) -> np.ndarray:
        """Band 1 of ``path`` (on this grid) at these pixels."""
        with rasterio.open(path) as src:
            parts = [src.read(1, window=w).ravel()[index] for w, index in self.strips]
            return np.concatenate(parts) if parts else np.empty(0, dtype=src.dtypes[0])

    def cut_years(self, **detectors: Path | None) -> tuple[np.ndarray, np.ndarray]:
        """:func:`read_cut_year` at these pixels, strip by strip."""
        years, detector = [np.empty(0, np.uint16)], [np.empty(0, np.uint8)]
        for w, index in self.strips:
            if not len(index):
                continue
            where = np.zeros(int(w.height) * int(w.width), dtype=bool)
            where[index] = True
            grid = Grid(windows.transform(w, self.transform), (int(w.height), int(w.width)))
            y, d = read_cut_year(grid, where=where.reshape(grid.shape), **detectors)
            years.append(y)
            detector.append(d)
        return np.concatenate(years), np.concatenate(detector)

    def rasterized(self, shapes: list) -> np.ndarray:
        """``shapes`` (geometry, value) burnt onto the grid by pixel centre, at these pixels; 0 off them."""
        from rasterio import features

        return np.concatenate([np.empty(0, np.uint16)] + [
            features.rasterize(shapes, out_shape=(int(w.height), int(w.width)), fill=0, dtype="uint16",
                               transform=windows.transform(w, self.transform)).ravel()[index]
            for w, index in self.strips])

    def others_equal(self, a: Path, b: Path) -> bool:
        """Whether ``a`` and ``b`` agree everywhere but these pixels."""
        with rasterio.open(a) as one, rasterio.open(b) as other:
            for w, index in self.strips:
                keep = np.ones(int(w.height) * int(w.width), dtype=bool)
                keep[index] = False
                if not (one.read(1, window=w).ravel()[keep] == other.read(1, window=w).ravel()[keep]).all():
                    return False
        return True

    def rewrite(self, source: Path, path: Path, values: np.ndarray | int, *, nodata,
                description: str) -> None:
        """``source`` with these pixels set to ``values``, as a Cloud Optimized GeoTIFF at ``path``.

        Written a strip at a time to a tiled scratch file beside ``path``, then copied to a
        COG (overviews nearest: the values are classes), so neither is held whole.
        """
        from rasterio.shutil import copy as rio_copy

        values = np.asarray(values)
        scratch = path.with_name(path.stem + ".partial.tif")
        with rasterio.open(source) as src:
            profile = dict(driver="GTiff", height=src.height, width=src.width, count=1,
                           dtype=src.dtypes[0], crs=project_crs(), transform=self.transform,
                           nodata=nodata, tiled=True, blockxsize=256, blockysize=256,
                           compress="DEFLATE", BIGTIFF="IF_SAFER")
            start = 0
            with rasterio.open(scratch, "w", **profile) as dst:
                for w, index in self.strips:
                    block = src.read(1, window=w)
                    block.reshape(-1)[index] = values if values.ndim == 0 else values[start:start + len(index)]
                    start += len(index)
                    dst.write(block, 1, window=w)
                dst.update_tags(description=description)
        rio_copy(scratch, path, driver="COG", compress="DEFLATE", blocksize=256,
                 overview_resampling="NEAREST", resampling="NEAREST")
        scratch.unlink()


def read_vat(path: Path) -> pd.DataFrame:
    """TreeMap's VAT (:data:`VAT_COLUMNS`), its float ``Value``, ``TM_ID`` and ``PLT_CN`` as exact strings."""
    import pyogrio

    vat = pyogrio.read_dataframe(path, columns=VAT_COLUMNS, read_geometry=False)
    for column in ("Value", "TM_ID", "PLT_CN"):
        vat[column] = as_id_series(vat[column], column=column)
    return vat[VAT_COLUMNS]


def default_inputs(data_root: Path | None) -> dict[str, Path]:
    """The TreeMap VAT, tree table and entire FIADB ``config/data_paths.yaml`` declares,
    moved under ``data_root`` (a mirror of the drive, e.g. ``data/r2_cache``) when given."""
    from pipeline.data_access import data_paths, under_data_root

    raw = data_paths()["raw"]
    treemap = raw["treemap_2022"]
    return {"vat": under_data_root(treemap["tif"] + ".vat.dbf", data_root),
            "tree_table": under_data_root(treemap["tree_table"], data_root),
            "db": under_data_root(raw["fia_sqlite"]["db"], data_root)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("run_dir", type=Path,
                        help="a stitched run folder holding treemap2022_improved.tif, "
                             "treemap2022_provenance.tif and bookend_strata.tif")
    parser.add_argument("--obata-tif", type=Path, default=None,
                        help="geepipe lastDist raster on the TreeMap grid (default: the run's manifest)")
    parser.add_argument("--hansen-tif", type=Path, default=None,
                        help="Hansen GFC raster on the TreeMap grid, band 1 lossyear "
                             "(default: the run's manifest)")
    parser.add_argument("--landfire-dist-dir", type=Path, default=None,
                        help="folder of LANDFIRE annual disturbance products "
                             "(LF<version>_Dist<yy>_CONUS/); its clearcuts and harvests date pixels "
                             "(default: not used)")
    parser.add_argument("--vat", type=Path, default=None,
                        help="TreeMap's VAT (default: raw.treemap_2022.tif + .vat.dbf)")
    parser.add_argument("--tree-table", type=Path, default=None,
                        help="TreeMap's tree table (default: raw.treemap_2022.tree_table)")
    parser.add_argument("--db", type=Path, default=None,
                        help="the entire FIADB SQLite (default: raw.fia_sqlite.db)")
    parser.add_argument("--data-root", type=Path, default=None,
                        help="a mirror of the drive the defaults name, e.g. data/r2_cache")
    parser.set_defaults(**default_inputs(parser.parse_known_args()[0].data_root))
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    obata, hansen = run_detectors(args.run_dir, args.obata_tif, args.hansen_tif)   # before the slow reads
    report = write_add_back_ids(
        args.run_dir, vat=read_vat(args.vat),
        library=read_young_plot_library(args.vat, args.db, args.tree_table),
        tree_table=args.tree_table, obata=obata, hansen=hansen, landfire=args.landfire_dist_dir)
    print(json.dumps(report, indent=2))
    if not all(report["checks"].values()):
        sys.exit("a check failed: the young_plots outputs do not join")


if __name__ == "__main__":
    main()
