"""Region inputs for the ``leto`` stage: an FIA extract from the full FIADB, and staged hydrography.

Every run in a region reads the same FIA plots and the same streams. This step prepares both
once per region (a state) into its region inputs folder, so no run reads the 70 GB FIADB or the
Southeast-wide NHD again (ADR 0004):

    uv run python -m pipeline.s1_initial_state.region_prep --data-root <mirror> fia \\
        --region-dir <regions>/fl --repair <v4>/aoi_5county --repair <v5>/fl_statewide
    uv run python -m pipeline.s1_initial_state.region_prep --data-root <mirror> hydrography \\
        --region-dir <regions>/fl --state FL

Each source defaults to the ``config/data_paths.yaml`` key its ``--help`` names; ``--data-root``
moves those paths from the drive to a local mirror of the R2 bucket.

**fia** reads each ``--repair`` run's ``treemap2022_improved.tif`` and
``treemap2022_provenance.tif`` and, through TreeMap's VAT, the plots it maps. It writes:

- ``fia_extract.db``: those plots' rows of :data:`EXTRACT_TABLES` (PLOT, COND, TREE and FVS's
  plot-level StandInit and TreeInit), under the FIADB's own schema, each indexed on its plot
  column. Any FIADB reader works on it unchanged, e.g.
  :func:`pipeline.s1_initial_state.data_sources.load_leto_attribute_sources`.
- ``fia_extract_missing.csv``: each plot it cannot fully serve, by :class:`PlotCoverage`, with
  its ``TM_ID`` and the runs that map it.
- ``fia_extract.json``: the FIADB (path, size, latest release), each run (its ``manifest.json``
  sha256, and its plots and pixels by coverage), the row counts, and the extract's sha256.

What the extract covers:

- **Every plot a run's improved TreeMap maps**, over all the runs. A run's own raster stamps an
  add-back pixel (provenance 2 or 4) with its neighbour plot's ``TM_ID`` (AGENTS.md), so
  neighbour plots are in the extract too: a young plot's FVS stand row may take its site from
  one. Add-back pixels are never counted as that plot's: the manifest gives them apart, and a
  plot only ever stamped there as a neighbour-only plot.
- **No young-plot ID** (from :data:`add_back_ids.NEW_ID_START`): its trees come from the run's
  ``young_plots/``. Any other value without a VAT row fails closed (:class:`UnknownTreeMapValues`).
- **Exact control numbers.** Rows are copied SQL to SQL and matched as text, never through a
  float, so a 19-digit ``PLT_CN`` arrives as it left.

The FIADB is read once. A rerun with the inputs the manifest records keeps the extract, as long as
its files are there and its sha256 still matches. A new run or plot set, or an extract edited by
hand, rebuilds it beside the old one and swaps it in whole.

**hydrography** writes ``hydrography.gpkg`` (layers ``flowline`` and ``waterbody``) and
``hydrography.json``. A layer keeps each feature that intersects the state's county extent,
padded by :data:`HYDROGRAPHY_PAD_M`, reprojected to the project CRS, with only :data:`FCODE`:
what :mod:`pipeline.leto_riparian` buffers by. Florida's sources are EPA's NHDPlus 2022
flowlines (``raw.nhd.fl_gdb``) and the Southeast NHD waterbodies (``raw.nhd.se_gdb``), both in
NAD83 degrees. Every FCode is kept, 55800 (artificial path) included: the ``leto`` stage buffers
it as the NHDArea stand-in.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import sqlite3
import warnings
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

import numpy as np
import pandas as pd
import rasterio

from pipeline.ids import as_id_series
from pipeline.s1_initial_state.add_back_ids import NEW_ID_START, read_vat
from pipeline.s1_initial_state.county_improvement import TreeMapProvenance
from pipeline.spatial_ref import assert_project_crs, project_crs

FIA_EXTRACT_DB = "fia_extract.db"
FIA_EXTRACT_MISSING_CSV = "fia_extract_missing.csv"
FIA_EXTRACT_JSON = "fia_extract.json"
RUN_MANIFEST = "manifest.json"
IMPROVED_TREEMAP = "treemap2022_improved.tif"
PROVENANCE = "treemap2022_provenance.tif"
# The extracted tables, each with the column that names its plot.
EXTRACT_TABLES = {"PLOT": "CN", "COND": "PLT_CN", "TREE": "PLT_CN",
                  "FVS_STANDINIT_PLOT": "STAND_CN", "FVS_TREEINIT_PLOT": "STAND_CN"}
LIVE_TREE = 1   # TREE.STATUSCD of a live tree
HYDROGRAPHY_GPKG = "hydrography.gpkg"
HYDROGRAPHY_JSON = "hydrography.json"
FCODE = "fcode"   # the column leto_riparian buffers by
# Past the state's counties: a feature just outside still buffers into a unit at the state line, and
# the widest buffer rule is 75 ft (leto_riparian.FLORIDA_FIRST_PASS_RULES).
HYDROGRAPHY_PAD_M = 1000.0


class PlotCoverage(StrEnum):
    """What the extract holds for a plot a run references."""

    COMPLETE = "complete"
    NOT_IN_FIADB = "not_in_fiadb"     # no PLOT row
    NO_LIVE_TREES = "no_live_trees"   # a PLOT row, but no TREE row with STATUSCD 1
    NO_FVS_STAND = "no_fvs_stand"     # live trees, but no FVS_STANDINIT_PLOT row to run


# Per plot: its rows in the extract. The extract's tables are indexed on their plot column.
COVERAGE_SQL = f"""
    SELECT w.PLT_CN,
           (SELECT COUNT(*) FROM main.PLOT p WHERE p.CN = w.PLT_CN) AS plot_rows,
           (SELECT COUNT(*) FROM main.TREE t
             WHERE t.PLT_CN = w.PLT_CN AND t.STATUSCD = {LIVE_TREE}) AS live_trees,
           (SELECT COUNT(*) FROM main.FVS_STANDINIT_PLOT s
             WHERE s.STAND_CN = w.PLT_CN) AS fvs_stand_rows,
           (SELECT COUNT(*) FROM main.FVS_TREEINIT_PLOT r
             WHERE r.STAND_CN = w.PLT_CN) AS fvs_tree_rows
      FROM temp.wanted w ORDER BY w.PLT_CN
"""


class UnknownTreeMapValues(ValueError):
    """A run maps a value that is neither a TreeMap value nor a young-plot ID: no plot to extract."""


def treemap_values(run_dir: Path) -> tuple[pd.Series, pd.Series]:
    """Pixels per value of a run's improved raster: on TreeMap's own pixels, and on add-back pixels.

    Add-back pixels are provenance 2 and 4 (:meth:`TreeMapProvenance.added_back`). Each
    Series is indexed by the raster value; nodata is left out.
    """
    counts: tuple[dict[int, int], dict[int, int]] = ({}, {})
    with rasterio.open(run_dir / IMPROVED_TREEMAP) as src, rasterio.open(run_dir / PROVENANCE) as prov:
        for _, window in src.block_windows(1):
            block = src.read(1, window=window)
            added = TreeMapProvenance.added_back(prov.read(1, window=window))
            for tally, pixels in zip(counts, (~added, added)):
                values, n = np.unique(block[pixels & (block != src.nodata)], return_counts=True)
                for value, count in zip(values.tolist(), n.tolist()):
                    tally[value] = tally.get(value, 0) + count
    treemap, add_back = (pd.Series(c, dtype=np.int64).sort_index() for c in counts)
    return treemap, add_back


@dataclass(frozen=True)
class RunReferences:
    """What one raster-repair run's improved TreeMap references."""

    run_dir: Path
    plots: pd.DataFrame               # TM_ID, PLT_CN, pixels: TreeMap values on TreeMap's own pixels
    neighbours: pd.DataFrame          # the same, for the neighbour plot stamped on add-back pixels
    young_plot_pixels: pd.Series      # pixels per young-plot ID, which names no plot

    @property
    def label(self) -> str:
        """The run's name in the extract's tables: its folder and the folder above it."""
        return "/".join(Path(self.run_dir).parts[-2:])

    @property
    def path(self) -> str:
        return str(self.run_dir.resolve())

    @property
    def manifest_sha256(self) -> str | None:
        """The sha256 of the run's ``manifest.json``, or None for a run without one."""
        manifest = self.run_dir / RUN_MANIFEST
        return _sha256(manifest) if manifest.exists() else None

    @property
    def referenced(self) -> pd.DataFrame:
        """Every plot the run names, its own pixels' and its add-back pixels' neighbours."""
        return pd.concat([self.plots, self.neighbours], ignore_index=True)


def run_references(run_dir: Path, vat: pd.DataFrame) -> RunReferences:
    """The plots a run maps, through TreeMap's VAT (``TM_ID`` and ``PLT_CN`` as exact strings).

    An add-back pixel's value is not its trees' plot. In a run's own raster it is the
    neighbour plot's ``TM_ID`` (forest type and species mix only), kept apart in
    :attr:`RunReferences.neighbours`. In ``young_plots/`` it is a young-plot ID (from
    :data:`add_back_ids.NEW_ID_START`), whose trees come from ``young_plots/``: it names no plot
    to extract. Any other value without a VAT row is :class:`UnknownTreeMapValues`.
    """
    treemap, add_back = treemap_values(run_dir)
    young = add_back.index >= NEW_ID_START
    plots, neighbours = (_plots_of(run_dir, values, vat) for values in (treemap, add_back[~young]))
    return RunReferences(Path(run_dir), plots, neighbours, add_back[young])


def _plots_of(run_dir: Path, values: pd.Series, vat: pd.DataFrame) -> pd.DataFrame:
    """``TM_ID``, ``PLT_CN`` and ``pixels`` for raster values that must all be TreeMap values."""
    mapped = pd.DataFrame({"TM_ID": as_id_series(pd.Series(values.index, dtype=np.int64), column="TM_ID"),
                           "pixels": values.to_numpy()})
    mapped = mapped.merge(vat[["TM_ID", "PLT_CN"]], on="TM_ID", how="left", validate="one_to_one")
    unknown = mapped[mapped["PLT_CN"].isna()]
    if len(unknown):
        raise UnknownTreeMapValues(
            f"{run_dir} maps values in no TreeMap VAT row: "
            + ", ".join(f"{v} ({n} px)" for v, n in zip(unknown["TM_ID"], unknown["pixels"])))
    return mapped


def coverage_status(coverage: pd.DataFrame) -> pd.Series:
    """Each plot's :class:`PlotCoverage`, from its row counts in the extract."""
    status = np.select(
        [coverage["plot_rows"] == 0, coverage["live_trees"] == 0, coverage["fvs_stand_rows"] == 0],
        [PlotCoverage.NOT_IN_FIADB, PlotCoverage.NO_LIVE_TREES, PlotCoverage.NO_FVS_STAND],
        PlotCoverage.COMPLETE)
    return pd.Series(status, index=coverage.index).map(PlotCoverage)


def write_fia_extract(region_dir: Path, *, fiadb: Path, runs: Sequence[Path],
                      vat: pd.DataFrame) -> dict:
    """Write the FIADB rows of every plot the runs reference to ``<region_dir>``; return the manifest.

    - ``fia_extract.db``: :data:`EXTRACT_TABLES` under the FIADB's own schema, each indexed on
      its plot column, so any FIADB reader works on it unchanged.
    - ``fia_extract_missing.csv``: each referenced plot the extract cannot fully serve
      (:class:`PlotCoverage`), with the runs that map it.
    - ``fia_extract.json``: the FIADB, each run and its coverage, the row counts, and the
      extract's sha256.

    The FIADB is read once: an extract whose manifest records these inputs (the FIADB's
    identity, each run's manifest and the values it maps) is kept and its manifest returned.
    Otherwise it is rebuilt beside the old one and swapped in whole.
    """
    if not runs:
        raise ValueError("an extract needs at least one raster-repair run to name its plots")
    references = [run_references(run, vat) for run in runs]
    identity = _fiadb_identity(fiadb)
    inputs_sha256 = _inputs_sha256(identity, references)
    db = region_dir / FIA_EXTRACT_DB
    current = _manifest_if_current(region_dir / FIA_EXTRACT_JSON, inputs_sha256,
                                   [db, region_dir / FIA_EXTRACT_MISSING_CSV])
    if current is not None and current.get("extract_sha256") == _sha256(db):
        return current
    region_dir.mkdir(parents=True, exist_ok=True)
    plots = sorted({cn for ref in references for cn in ref.referenced["PLT_CN"]})
    with _swapped_in(region_dir / FIA_EXTRACT_DB) as partial:
        tables, coverage = _extract(partial, fiadb, plots)
    coverage["status"] = coverage_status(coverage)
    _missing_table(coverage, references).to_csv(region_dir / FIA_EXTRACT_MISSING_CSV, index=False)
    status = coverage.set_index("PLT_CN")["status"]
    manifest = {
        "fiadb": identity,
        "runs": [_run_summary(ref, status) for ref in references],
        "plots": len(plots),
        "plots_by_status": _counts(status),
        "tables": tables,
        "extract_sha256": _sha256(region_dir / FIA_EXTRACT_DB),
        "inputs_sha256": inputs_sha256,
    }
    (region_dir / FIA_EXTRACT_JSON).write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def _inputs_sha256(identity: dict, references: Sequence[RunReferences]) -> str:
    """A hash of everything the extract is built from: the FIADB and what each run maps."""
    runs = [{"path": ref.path, "manifest_sha256": ref.manifest_sha256,
             "values": ref.plots[["TM_ID", "PLT_CN", "pixels"]].values.tolist(),
             "neighbours": ref.neighbours[["TM_ID", "PLT_CN", "pixels"]].values.tolist(),
             "young_plot_pixels": [[int(k), int(v)] for k, v in ref.young_plot_pixels.items()]}
            for ref in references]
    return _json_sha256({"fiadb": identity, "runs": runs})


def _json_sha256(inputs) -> str:
    """The sha256 of ``inputs`` as canonical JSON: what a manifest's ``inputs_sha256`` records."""
    return hashlib.sha256(json.dumps(inputs, sort_keys=True, default=int).encode()).hexdigest()


def _manifest_if_current(path: Path, inputs_sha256: str, outputs: Sequence[Path]) -> dict | None:
    """The manifest at ``path`` if it records these inputs and every output it lists is there."""
    if not (path.exists() and all(output.exists() for output in outputs)):
        return None
    manifest = json.loads(path.read_text())
    return manifest if manifest.get("inputs_sha256") == inputs_sha256 else None


@contextmanager
def _swapped_in(final: Path) -> Iterator[Path]:
    """A path beside ``final`` to write it at, moved over ``final`` only if the block succeeds."""
    partial = final.with_name(f"{final.stem}.partial{final.suffix}")
    partial.unlink(missing_ok=True)
    try:
        yield partial
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    os.replace(partial, final)


def _extract(out: Path, fiadb: Path, plots: list[str]) -> tuple[dict[str, int], pd.DataFrame]:
    """Copy the plots' rows of each :data:`EXTRACT_TABLES` table; return row counts and coverage.

    One scan per table: the FIADB has no index on its control numbers. They compare as
    text, so an integer-typed column matches by its exact digits, never through a float.
    """
    con = sqlite3.connect(out.as_uri(), uri=True)
    try:
        con.execute("ATTACH DATABASE ? AS fia", (f"{Path(fiadb).resolve().as_uri()}?mode=ro&immutable=1",))
        con.execute("CREATE TEMP TABLE wanted (PLT_CN TEXT PRIMARY KEY)")
        con.executemany("INSERT INTO temp.wanted VALUES (?)", [(cn,) for cn in plots])
        tables = {}
        for table, key in EXTRACT_TABLES.items():
            row = con.execute("SELECT sql FROM fia.sqlite_master WHERE type = 'table' AND name = ?",
                              (table,)).fetchone()
            if row is None:
                raise ValueError(f"{fiadb} has no {table} table: not a full FIADB")
            con.execute(row[0])
            con.execute(f'INSERT INTO main."{table}" SELECT * FROM fia."{table}" '
                        f'WHERE CAST("{key}" AS TEXT) IN (SELECT PLT_CN FROM temp.wanted)')
            con.execute(f'CREATE INDEX "{table}_{key}" ON "{table}" ("{key}")')
            (tables[table],) = con.execute(f'SELECT COUNT(*) FROM main."{table}"').fetchone()
        con.commit()
        return tables, pd.read_sql(COVERAGE_SQL, con)
    finally:
        con.close()


def _fiadb_identity(fiadb: Path) -> dict:
    """The FIADB's path, size, modification time and latest release (``REF_FIADB_VERSION``)."""
    path = Path(fiadb).resolve()
    stat = path.stat()
    con = sqlite3.connect(f"{path.as_uri()}?mode=ro&immutable=1", uri=True)
    try:
        row = con.execute("SELECT VERSION FROM REF_FIADB_VERSION ORDER BY CREATED_DATE DESC LIMIT 1").fetchone()
    finally:
        con.close()
    return {"path": str(path), "bytes": stat.st_size, "mtime_ns": stat.st_mtime_ns,
            "version": row[0] if row else None}


def _run_summary(ref: RunReferences, status: pd.Series) -> dict:
    """One run's line in the manifest: where it is, what it maps, and how well the extract serves it."""
    plots = ref.plots.assign(status=ref.plots["PLT_CN"].map(status))
    return {
        "label": ref.label,
        "path": ref.path,
        "manifest_sha256": ref.manifest_sha256,
        "treemap_values": len(plots),
        "plots": int(plots["PLT_CN"].nunique()),
        "neighbour_only_plots": len(set(ref.neighbours["PLT_CN"]) - set(plots["PLT_CN"])),
        "add_back_pixels": int(ref.neighbours["pixels"].sum() + ref.young_plot_pixels.sum()),
        "young_plot_ids_excluded": len(ref.young_plot_pixels),
        "young_plot_pixels": int(ref.young_plot_pixels.sum()),
        "plots_by_status": _counts(plots.drop_duplicates("PLT_CN")["status"]),
        "pixels_by_status": {str(k): int(v) for k, v in plots.groupby("status")["pixels"].sum().items()},
    }


def _counts(status: pd.Series) -> dict[str, int]:
    return {str(k): int(v) for k, v in status.value_counts().sort_index().items()}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _missing_table(coverage: pd.DataFrame, references: Sequence[RunReferences]) -> pd.DataFrame:
    """The plots not :attr:`PlotCoverage.COMPLETE`, each with its ``TM_ID`` and the runs mapping it."""
    mapped = pd.concat([ref.referenced.assign(run=ref.label) for ref in references], ignore_index=True)
    runs = (mapped.drop_duplicates(["PLT_CN", "TM_ID", "run"])
            .groupby(["PLT_CN", "TM_ID"], sort=False)["run"].agg("; ".join).reset_index(name="runs"))
    missing = coverage[coverage["status"] != PlotCoverage.COMPLETE]
    missing = missing.merge(runs, on="PLT_CN", how="left", validate="one_to_many")
    return missing[["PLT_CN", "TM_ID", "status", "plot_rows", "live_trees", "fvs_stand_rows",
                    "fvs_tree_rows", "runs"]].sort_values(["PLT_CN", "TM_ID"], ignore_index=True)


# ── hydrography ─────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class LayerSource:
    """One vector layer of a source dataset (a .gdb, .gpkg or shapefile)."""

    path: Path
    layer: str


def state_bounds(counties: Path, state: str) -> tuple[float, float, float, float]:
    """A state's counties' extent in the project CRS, padded by :data:`HYDROGRAPHY_PAD_M`.

    ``counties`` is the national county layer (``raw.counties``): ``state`` is matched against
    its ``STATE`` (postal code) or, all digits, its ``STATE_FIPS``.
    """
    import geopandas as gpd

    if not (state.isascii() and state.isalnum()):
        raise ValueError(f"a state is a postal code or FIPS, e.g. FL or 12, not {state!r}")
    column = "STATE_FIPS" if state.isdigit() else "STATE"
    rows = gpd.read_file(counties, where=f"{column} = '{state.upper()}'")
    if rows.empty:
        raise ValueError(f"no county of state {state!r} in {counties}")
    minx, miny, maxx, maxy = rows.to_crs(project_crs()).total_bounds
    pad = HYDROGRAPHY_PAD_M
    return (float(minx - pad), float(miny - pad), float(maxx + pad), float(maxy + pad))


def stage_hydrography(region_dir: Path, *, flowlines: LayerSource, waterbodies: LayerSource,
                      bounds: tuple[float, float, float, float]) -> dict:
    """Write ``<region_dir>/hydrography.gpkg``, its ``flowline`` and ``waterbody`` layers; return the manifest.

    Each keeps the features intersecting ``bounds`` (project CRS), reprojected to the project
    CRS, with only their FCode, as :data:`FCODE`: what :mod:`pipeline.leto_riparian` buffers by.
    ``hydrography.json`` records each source and the features staged. Layers staged from the
    same sources (path, layer, size and modification time) and bounds are kept.
    """
    sources = {"flowline": flowlines, "waterbody": waterbodies}
    inputs = {"bounds": list(bounds), "sources": {name: [str(Path(src.path).resolve()), src.layer,
                                                        *_file_identity(Path(src.path))]
                                                 for name, src in sources.items()}}
    inputs_sha256 = _json_sha256(inputs)
    current = _manifest_if_current(region_dir / HYDROGRAPHY_JSON, inputs_sha256,
                                   [region_dir / HYDROGRAPHY_GPKG])
    if current is not None:
        return current
    region_dir.mkdir(parents=True, exist_ok=True)
    layers = {}
    with _swapped_in(region_dir / HYDROGRAPHY_GPKG) as partial:
        for name, source in sources.items():
            layer, source_crs = read_layer(source, bounds)
            assert_project_crs(layer, f"staged {name} layer")
            layer.to_file(partial, layer=name, driver="GPKG")
            layers[name] = {"path": str(Path(source.path).resolve()), "layer": source.layer,
                            "source_crs": source_crs, "features": len(layer)}
    manifest = {"crs": project_crs(), "bounds": list(bounds), "layers": layers,
                "inputs_sha256": inputs_sha256}
    (region_dir / HYDROGRAPHY_JSON).write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def _file_identity(path: Path) -> tuple[int, int]:
    """Bytes and latest modification time of a file, or of every file in a dataset folder (.gdb)."""
    files = [p for p in path.rglob("*") if p.is_file()] if path.is_dir() else [path]
    stats = [p.stat() for p in files]
    return sum(s.st_size for s in stats), max((s.st_mtime_ns for s in stats), default=0)


def read_layer(source: LayerSource, bounds: tuple[float, float, float, float]):
    """A source layer's features intersecting ``bounds``, in the project CRS, with :data:`FCODE` only.

    Returns the frame and the source's CRS.
    """
    import pyogrio
    from rasterio.warp import transform_bounds
    from shapely.geometry import box

    info = pyogrio.read_info(source.path, layer=source.layer)
    fcode = next((f for f in info["fields"] if f.lower() == FCODE), None)
    if fcode is None:
        raise ValueError(f"{source.path} layer {source.layer} has no FCode field: {list(info['fields'])}")
    with warnings.catch_warnings():   # NHDPlus flowlines are measured lines; the M values are not used
        warnings.filterwarnings("ignore", message="Measured \\(M\\) geometry types are not supported")
        frame = pyogrio.read_dataframe(source.path, layer=source.layer, columns=[fcode],
                                       bbox=transform_bounds(project_crs(), info["crs"], *bounds,
                                                             densify_pts=21))
    frame = frame.rename(columns={fcode: FCODE}).to_crs(project_crs())
    inside = frame.sindex.query(box(*bounds), predicate="intersects")
    frame = frame.iloc[sorted(inside)].reset_index(drop=True)
    fcodes = pd.to_numeric(frame[FCODE], errors="coerce")
    unusable = frame.loc[fcodes.isna(), FCODE]
    if len(unusable):   # leto_riparian would give it no buffer, silently
        raise ValueError(f"{source.path} layer {source.layer}: {len(unusable)} feature(s) with no "
                         f"usable FCode: {', '.join(map(str, unusable.unique()[:5]))}")
    frame[FCODE] = fcodes.astype("Int32")
    return frame[[FCODE, "geometry"]], info["crs"]


# ── the command ─────────────────────────────────────────────────────────────────────────

FLOWLINE_LAYER = "nhdflowline_fl"                       # in raw.nhd.fl_gdb (EPA NHDPlus 2022)
WATERBODY_LAYER = "NHDWaterbody_DissolveBoundaries1"    # in raw.nhd.se_gdb


def _declared(path: str, data_root: Path | None) -> Path:
    """A path declared in ``config/data_paths.yaml``, moved under ``data_root`` when given.

    ``data_root`` stands in for the drive (``/mnt/d``), e.g. a local mirror of the R2 bucket.
    """
    from pipeline.data_access import data_paths

    if data_root is None:
        return Path(path)
    return data_root / Path(path).relative_to(data_paths()["drive"])


def _parser() -> argparse.ArgumentParser:
    from pipeline.data_access import data_paths

    raw = data_paths()["raw"]
    parser = argparse.ArgumentParser(prog="python -m pipeline.s1_initial_state.region_prep",
                                     description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data-root", type=Path, default=None,
                        help="stands in for the drive in every default path, e.g. a local R2 mirror")
    commands = parser.add_subparsers(dest="command", required=True)

    fia = commands.add_parser("fia", help="the FIA extract: fia_extract.{db,json}, fia_extract_missing.csv")
    fia.add_argument("--region-dir", type=Path, required=True)
    fia.add_argument("--repair", type=Path, action="append", required=True,
                     help="a raster-repair run folder (treemap2022_improved.tif and "
                          "treemap2022_provenance.tif), or its young_plots/; repeat for each run")
    fia.add_argument("--fiadb", type=Path, help=f"default: {raw['fia_sqlite']['db']}")
    fia.add_argument("--vat", type=Path, help=f"default: {raw['treemap_2022']['tif']}.vat.dbf")

    hydro = commands.add_parser("hydrography", help="hydrography.{gpkg,json}: flowlines and waterbodies")
    hydro.add_argument("--region-dir", type=Path, required=True)
    hydro.add_argument("--state", required=True, help="postal code or state FIPS, e.g. FL or 12")
    hydro.add_argument("--counties", type=Path, help=f"default: {raw['counties']['p010g_shp']}")
    hydro.add_argument("--flowlines", type=Path, help=f"default: {raw['nhd']['fl_gdb']}")
    hydro.add_argument("--flowline-layer", default=FLOWLINE_LAYER)
    hydro.add_argument("--waterbodies", type=Path, help=f"default: {raw['nhd']['se_gdb']}")
    hydro.add_argument("--waterbody-layer", default=WATERBODY_LAYER)
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    from pipeline.data_access import data_paths

    args = _parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    raw = data_paths()["raw"]

    def path(given: Path | None, declared: str) -> Path:
        return given if given is not None else _declared(declared, args.data_root)

    if args.command == "fia":
        manifest = write_fia_extract(
            args.region_dir, fiadb=path(args.fiadb, raw["fia_sqlite"]["db"]), runs=args.repair,
            vat=read_vat(path(args.vat, raw["treemap_2022"]["tif"] + ".vat.dbf")))
        summary = {"plots": manifest["plots"], "plots_by_status": manifest["plots_by_status"],
                   "runs": {run["label"]: run["plots_by_status"] for run in manifest["runs"]}}
    else:
        bounds = state_bounds(path(args.counties, raw["counties"]["p010g_shp"]), args.state)
        manifest = stage_hydrography(
            args.region_dir, bounds=bounds,
            flowlines=LayerSource(path(args.flowlines, raw["nhd"]["fl_gdb"]), args.flowline_layer),
            waterbodies=LayerSource(path(args.waterbodies, raw["nhd"]["se_gdb"]), args.waterbody_layer))
        summary = {name: layer["features"] for name, layer in manifest["layers"].items()}
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
