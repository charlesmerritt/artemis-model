"""The ``outputs`` stage: a scheduled run folder to its trajectory table, per-timestep maps and QA.

    uv run python main.py outputs <run_dir> [--rasters all|activity|vegetation|none]
                                            [--qa full|tables|none] [--grid <raster>]

It joins the three things ``schedule`` leaves apart (AGENTS.md step 4, #134): ``plan.csv.gz``
(one selected trajectory per unit), ``fvs_cycles.csv.gz`` (each FVS run's per-cycle state,
keyed by ``run_hash``) and ``management_units.gpkg`` (the ``leto`` stage's unit polygons).
``library.csv`` maps a unit's selected prescription to its run, and the run's keyfile in
``keyfiles/`` says which cycles it thins or clearcuts (:func:`keyfile_activities`: a
``ThinDBH`` removing every tree is a clearcut, as `trajectory_library.build_entries` decides).

Writes, under ``<run_dir>/outputs/``:

- ``trajectories.csv.gz`` (:func:`trajectory_table`): one row per unit and cycle, 0 to the
  projection's ``n_cycles`` (2022-2072): the unit, its selected prescription and timing
  offset, the cycle's :class:`Activity`, and its run's FVS ``Age``, ``BA``, ``MCuFt`` and
  removals, per acre and for the unit's acres. Every unit keeps its own rows when many share a
  run; control numbers stay exact strings.
- ``rasters/activity/activity_<year>.tif``: per timestep, each cell's :class:`Activity`
  (uint8, nodata 255), and ``rasters/vegetation/vegetation_<year>.tif``: per timestep, one band
  per :data:`VEGETATION_MEASURES` (float32, nodata -9999). Both are Cloud Optimized GeoTIFFs.
- ``rasters/unit_index.tif`` and ``unit_index.csv``: each cell's unit (1..N, 0 off every
  planned unit), the key from a cell to its rows of the table.
- ``outputs.json``: the cycles, the grid and the options it ran with.

**The grid.** Units are burnt in by cell centre on the run's grid: the resolution and
alignment of ``--grid``, else of the ``leto`` stage's ``leto/<FIPS>/parent_stands.tif``
(the repaired TreeMap grid), else of the project's TreeMap snap grid
(:func:`pipeline.spatial_ref.snap_transform`), over the whole units layer's extent. A unit
smaller than a cell can miss every cell centre; the coverage table counts those. Units the plan does not
hold (masked land) are nodata. Rasters are written in strips, so a statewide grid never sits
in memory whole.

**Fails closed** on a planned unit with no run, cycle or keyfile (:class:`MissingTrajectory`,
:class:`MissingKeyfile`), no polygon (:class:`UnitsMissingFromLayer`), on a riparian unit
with any activity or removal (:class:`RiparianActivity`), and on removals that do not sum to
the plan's per owner group and cycle (:class:`PlanMismatch`, checked whatever ``--qa`` says).

**QA** (``--qa``, :class:`QaLevel`) goes to ``<run_dir>/qa/outputs/`` (#160's ``qa/<stage>/``):
the tables in :data:`QA_NOTES`, then with ``full`` the figures (removals per owner group
against the TPO target, activity acres, vegetation over time, and small-multiple maps of
activity and of each vegetation measure per timestep) and ``index.html``
(:func:`pipeline.qa_report.write_index`).
"""

from __future__ import annotations

import json
import logging
import math
import shutil
import tempfile
from dataclasses import dataclass
from enum import IntEnum, StrEnum
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
from affine import Affine
from rasterio import features
from rasterio import shutil as rio_shutil
from rasterio import windows as rio_windows
from rasterio.windows import Window
from shapely.geometry import box

from pipeline import qa_report
from pipeline.ids import as_id_series
from pipeline.leto_stage import COUNTY_DIR, PARENT_STANDS, UNITS_GPKG, UNITS_LAYER
from pipeline.leto_riparian import SQUARE_METERS_PER_ACRE
from pipeline.run import CYCLES, LIBRARY, PLAN
from pipeline.s4_fvs import trajectory_library as tl
from pipeline.s4_fvs.regime_templates import FIELD_WIDTH
from pipeline.spatial_ref import assert_project_crs, snap_transform

OUT_DIR = Path("outputs")
TABLE = OUT_DIR / "trajectories.csv.gz"
RECORD = OUT_DIR / "outputs.json"
UNIT_INDEX_CSV = OUT_DIR / "unit_index.csv"
RASTER_DIR = OUT_DIR / "rasters"
UNIT_INDEX_TIF = RASTER_DIR / "unit_index.tif"
ACTIVITY_DIR = RASTER_DIR / "activity"
VEGETATION_DIR = RASTER_DIR / "vegetation"
QA_DIR = Path("qa") / "outputs"
KEYFILES = "keyfiles"
DEVIATION = "deviation_by_cycle.csv"          # schedule's per owner group targets

HARVEST_QA = "harvest_by_owner_group.csv"
ACTIVITY_QA = "activity_by_cycle.csv"
VEGETATION_QA = "vegetation_by_cycle.csv"
CONSISTENCY_QA = "activity_vs_removals.csv"
COVERAGE_QA = "raster_coverage.csv"
QA_NOTES = {
    HARVEST_QA: "Merchantable removals per owner group and cycle, from the trajectory table, "
                "against plan.csv.gz (they must agree) and the TPO target the schedule chased.",
    ACTIVITY_QA: "Units and acres per owner group, unit class, cycle and activity. Riparian "
                 "rows are only ever 'none'.",
    VEGETATION_QA: "Acre-weighted mean age and basal area, and total standing and removed "
                   "merchantable volume, per owner group and for the landscape ('all').",
    CONSISTENCY_QA: "Unit-cycles by keyfile activity and whether FVS removed merchantable "
                    "volume: a thinning with no removal took only sub-merchantable trees.",
    COVERAGE_QA: "How the planned units land on the raster grid: units smaller than a cell "
                 "can miss every cell centre.",
    "harvest_by_owner_group.png": "Merchantable removals per cycle, each owner group on its "
                                  "own axis; the dashed line is its TPO target.",
    "activity_acres_by_cycle.png": "Acres thinned and clearcut per cycle, by owner group.",
    "vegetation_by_cycle.png": "Landscape vegetation over the horizon, by owner group.",
    "map_activity.png": "Management activity per timestep.",
}

VEGETATION_MEASURES = ("Age", "BA", "MCuFt")
VEGETATION_UNITS = {"Age": "years", "BA": "ft²/ac", "MCuFt": "merch ft³/ac"}
ACTIVITY_NODATA = 255
VEGETATION_NODATA = -9999.0
STRIP_ROWS = 1024                              # rows rasterised and written at once
MAX_MAP_PIXELS = 900                           # a map panel's long side; larger grids are decimated
TOLERANCE = 1e-6                               # relative: the plan's volumes and the table's

log = logging.getLogger("artemis.outputs")


class Activity(IntEnum):
    """What a unit's trajectory does in a cycle; the value is its activity raster code."""

    NONE = 0
    THINNING = 1
    CLEARCUT = 2

    @property
    def label(self) -> str:
        return self.name.lower()


class RasterSet(StrEnum):
    """Which per-timestep rasters to write."""

    ALL = "all"
    ACTIVITY = "activity"
    VEGETATION = "vegetation"
    NONE = "none"

    @property
    def activity(self) -> bool:
        return self in (RasterSet.ALL, RasterSet.ACTIVITY)

    @property
    def vegetation(self) -> bool:
        return self in (RasterSet.ALL, RasterSet.VEGETATION)


class QaLevel(StrEnum):
    """How much of the QA report to write."""

    FULL = "full"                              # tables, figures and maps, index.html
    TABLES = "tables"                          # the CSV tables only
    NONE = "none"


class MissingTrajectory(ValueError):
    """A planned unit's selected prescription has no run in the library, or its run no cycles."""


class MissingKeyfile(FileNotFoundError):
    """A selected run's keyfile is not in ``keyfiles/``: its activities cannot be read."""


class UnitsMissingFromLayer(ValueError):
    """A planned unit has no polygon in ``management_units.gpkg``."""


class RiparianActivity(AssertionError):
    """A riparian unit thins, clearcuts or removes volume in some cycle."""


class PlanMismatch(AssertionError):
    """The table's removals per owner group and cycle differ from ``plan.csv.gz``'s."""


# ── activities from a keyfile ──────────────────────────────────────────────────────────


def keyfile_activities(text: str) -> dict[int, Activity]:
    """Each cycle a keyfile's ``ThinDBH`` records fall in, and the heaviest cut there.

    A record removing every tree in its window (proportion 1) is a clearcut, the rule
    `trajectory_library.build_entries` attaches regeneration by; any lighter one is a thinning.
    """
    out: dict[int, Activity] = {}
    for line in text.splitlines():
        if line[:FIELD_WIDTH].strip() != "ThinDBH":
            continue
        fields = [line[i:i + FIELD_WIDTH].strip() for i in range(FIELD_WIDTH, len(line), FIELD_WIDTH)]
        year, proportion = int(fields[0]), float(fields[3])
        cycle = (year - tl.INV_YEAR) // tl.CYCLE_YEARS
        kind = Activity.CLEARCUT if proportion >= 1.0 else Activity.THINNING
        out[cycle] = max(out.get(cycle, Activity.NONE), kind)
    return out


def _activities(run_dir: Path, run_hashes) -> pd.DataFrame:
    rows = []
    for h in sorted(set(run_hashes)):
        path = run_dir / KEYFILES / f"{h}.key"
        if not path.exists():
            raise MissingKeyfile(f"run {h} is selected by the plan but {path} is missing")
        rows += [{"run_hash": h, "cycle": c, "activity": a.label}
                 for c, a in keyfile_activities(path.read_text()).items()]
    return pd.DataFrame(rows, columns=["run_hash", "cycle", "activity"])


# ── the trajectory table ───────────────────────────────────────────────────────────────


def n_cycles() -> int:
    from pipeline.s3_management import annealer
    return int(annealer.load_config()["n_cycles"])


def read_plan(run_dir: Path) -> pd.DataFrame:
    plan = pd.read_csv(run_dir / PLAN, dtype={"stand_id": str, "PLT_CN": str, "prescription": str})
    plan["PLT_CN"] = as_id_series(plan["PLT_CN"], column="PLT_CN")
    return plan.rename(columns={"stand_id": "unit_id"})


def trajectory_table(run_dir: Path, cycles: int) -> pd.DataFrame:
    """One row per planned unit and cycle ``0..cycles``: its regime, activity and vegetation."""
    plan = read_plan(run_dir)
    library = pd.read_csv(run_dir / LIBRARY, usecols=["unit_id", "prescription", "run_hash"],
                          dtype=str)
    chosen = plan.merge(library, on=["unit_id", "prescription"], how="left", validate="1:1")
    lost = chosen.loc[chosen["run_hash"].isna(), "unit_id"]
    if len(lost):
        raise MissingTrajectory(f"{len(lost)} planned units have no run in {LIBRARY}: "
                                f"{lost.head(5).tolist()}")
    runs = pd.read_csv(run_dir / CYCLES, dtype={"run_hash": str, "PLT_CN": str})
    runs = runs[runs["run_hash"].isin(set(chosen["run_hash"])) & runs["cycle"].between(0, cycles)]
    short = runs.groupby("run_hash")["cycle"].nunique().reindex(chosen["run_hash"].unique()).fillna(0)
    if (short < cycles + 1).any():
        raise MissingTrajectory(f"runs without every cycle 0..{cycles} in {CYCLES}: "
                                f"{short.index[short < cycles + 1][:5].tolist()}")
    keep = ["unit_id", "PLT_CN", "county", "owner_class", "owner_group", "unit_class", "acres",
            "prescription", "base_prescription", "timing_offset_years", "trajectory_id", "run_hash"]
    table = chosen[keep].merge(
        runs[["run_hash", "cycle", "calendar_year", *VEGETATION_MEASURES, "removed_merch_cuft_per_ac"]],
        on="run_hash", how="left", validate="m:m")
    table = table.merge(_activities(run_dir, chosen["run_hash"]), on=["run_hash", "cycle"], how="left")
    table["activity"] = table["activity"].fillna(Activity.NONE.label)
    table["removed_cuft"] = table["removed_merch_cuft_per_ac"] * table["acres"]
    table["standing_mcuft"] = table["MCuFt"] * table["acres"]
    table = table.sort_values(["unit_id", "cycle"], kind="stable").reset_index(drop=True)
    check_riparian(table)
    return table


def check_riparian(table: pd.DataFrame) -> None:
    """No riparian unit thins, clearcuts or removes volume in any cycle."""
    riparian = table["unit_class"] == "riparian"
    bad = riparian & ((table["activity"] != Activity.NONE.label)
                      | (table["removed_merch_cuft_per_ac"] > 0))
    if bad.any():
        units = sorted(table.loc[bad, "unit_id"].unique())
        raise RiparianActivity(f"{len(units)} riparian units are managed: {units[:10]}")


# ── QA tables ──────────────────────────────────────────────────────────────────────────


def harvest_reconciliation(table: pd.DataFrame, plan: pd.DataFrame, cycles: int,
                           targets: pd.DataFrame | None = None) -> pd.DataFrame:
    """Removals per owner group and cycle ``1..cycles``: the table's, the plan's, the target."""
    scored = table[table["cycle"].between(1, cycles)]
    got = scored.groupby(["owner_group", "cycle"], as_index=False).agg(
        calendar_year=("calendar_year", "first"), removed_cuft=("removed_cuft", "sum"))
    cols = [f"cuft_cycle_{c}" for c in range(1, cycles + 1)]
    planned = (plan.groupby("owner_group")[cols].sum().reset_index()
               .melt(id_vars="owner_group", var_name="cycle", value_name="plan_cuft"))
    planned["cycle"] = planned["cycle"].str.removeprefix("cuft_cycle_").astype(int)
    out = got.merge(planned, on=["owner_group", "cycle"], how="outer").fillna(
        {"removed_cuft": 0.0, "plan_cuft": 0.0})
    out["difference_cuft"] = out["removed_cuft"] - out["plan_cuft"]
    if targets is not None and len(targets):
        out = out.merge(targets, on=["owner_group", "cycle"], how="left")
    return out.sort_values(["owner_group", "cycle"]).reset_index(drop=True)


def check_reconciled(harvest: pd.DataFrame) -> None:
    off = harvest[harvest["difference_cuft"].abs() > TOLERANCE * harvest["plan_cuft"].abs().clip(lower=1)]
    if len(off):
        raise PlanMismatch("removals differ from plan.csv.gz for (owner group, cycle) "
                           f"{list(zip(off['owner_group'], off['cycle']))[:10]}")


def owner_targets(run_dir: Path) -> pd.DataFrame | None:
    path = run_dir / DEVIATION
    if not path.exists():
        return None
    dev = pd.read_csv(path)
    dev = dev[dev["dimension"] == "owner_group"]
    return dev.rename(columns={"key": "owner_group", "target_cuft": "target_cuft"})[
        ["owner_group", "cycle", "target_cuft"]]


def activity_by_cycle(table: pd.DataFrame) -> pd.DataFrame:
    return (table.groupby(["owner_group", "unit_class", "cycle", "calendar_year", "activity"],
                          as_index=False)
            .agg(units=("unit_id", "size"), acres=("acres", "sum")))


def _vegetation_rows(frame: pd.DataFrame, group: str) -> pd.DataFrame:
    def weighted(col):
        return (frame[col] * frame["acres"]).groupby(frame["cycle"]).sum() / acres

    acres = frame.groupby("cycle")["acres"].sum()
    out = pd.DataFrame({
        "owner_group": group, "calendar_year": frame.groupby("cycle")["calendar_year"].first(),
        "acres": acres, "mean_age": weighted("Age"), "mean_ba": weighted("BA"),
        "standing_mcuft": frame.groupby("cycle")["standing_mcuft"].sum(),
        "removed_cuft": frame.groupby("cycle")["removed_cuft"].sum()})
    return out.rename_axis("cycle").reset_index()


def vegetation_by_cycle(table: pd.DataFrame) -> pd.DataFrame:
    groups = [_vegetation_rows(g, name) for name, g in table.groupby("owner_group")]
    return pd.concat([*groups, _vegetation_rows(table, "all")], ignore_index=True)


def activity_vs_removals(table: pd.DataFrame) -> pd.DataFrame:
    removal = np.where(table["removed_merch_cuft_per_ac"] > 0, "removed", "none")
    return (table.assign(removal=removal)
            .groupby(["activity", "removal"], as_index=False)
            .agg(unit_cycles=("unit_id", "size"), acres=("acres", "sum")))


# ── the grid and the unit index ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Grid:
    transform: Affine
    width: int
    height: int
    crs: str

    @property
    def cell_acres(self) -> float:
        return abs(self.transform.a * self.transform.e) / SQUARE_METERS_PER_ACRE

    def profile(self, dtype: str, nodata, count: int = 1) -> dict:
        return {"driver": "GTiff", "width": self.width, "height": self.height, "count": count,
                "dtype": dtype, "crs": self.crs, "transform": self.transform, "nodata": nodata,
                "tiled": True, "blockxsize": 512, "blockysize": 512, "compress": "deflate"}


def reference_alignment(run_dir: Path, grid: Path | None) -> tuple[float, float, float]:
    """``(cell size, x origin, y origin)``: of ``grid``, the leto rasters, or the snap grid."""
    candidates = [grid] if grid else sorted((run_dir / COUNTY_DIR).glob(f"*/{PARENT_STANDS}"))
    if candidates:
        with rasterio.open(candidates[0]) as src:
            assert_project_crs(src, str(candidates[0]))
            if abs(src.res[0] - src.res[1]) > 1e-9:
                raise ValueError(f"{candidates[0]} has non-square cells {src.res}")
            return src.res[0], src.transform.c, src.transform.f
    xs, _, x0, _, _, y0 = snap_transform()
    return abs(xs), x0, y0


def run_grid(bounds: tuple[float, float, float, float], alignment: tuple[float, float, float],
             crs: str) -> Grid:
    """The aligned grid covering ``bounds`` (minx, miny, maxx, maxy)."""
    res, x0, y0 = alignment
    minx, miny, maxx, maxy = bounds
    left = x0 + math.floor((minx - x0) / res) * res
    top = y0 + math.ceil((maxy - y0) / res) * res
    width = max(1, math.ceil((maxx - left) / res))
    height = max(1, math.ceil((top - miny) / res))
    return Grid(Affine(res, 0.0, left, 0.0, -res, top), width, height, crs)


def planned_units(run_dir: Path, unit_ids: pd.Series) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    """The planned units' polygons, in plan order with ``index`` 1..N, and the unplanned ones."""
    layer = gpd.read_file(run_dir / UNITS_GPKG, layer=UNITS_LAYER)
    assert_project_crs(layer, str(run_dir / UNITS_GPKG))
    layer["unit_id"] = layer["unit_id"].astype("string").astype(object)
    missing = sorted(set(unit_ids) - set(layer["unit_id"]))
    if missing:
        raise UnitsMissingFromLayer(f"{len(missing)} planned units have no polygon in "
                                    f"{UNITS_GPKG}: {missing[:10]}")
    order = pd.DataFrame({"unit_id": list(unit_ids), "index": np.arange(1, len(unit_ids) + 1)})
    planned = layer.merge(order, on="unit_id", how="inner").sort_values("index")
    return planned.reset_index(drop=True), layer[~layer["unit_id"].isin(set(unit_ids))]


def _strips(grid: Grid):
    for row in range(0, grid.height, STRIP_ROWS):
        yield Window(col_off=0, row_off=row, width=grid.width,
                     height=min(STRIP_ROWS, grid.height - row))


def burn_unit_index(units: gpd.GeoDataFrame, grid: Grid, path: Path) -> np.ndarray:
    """Write each cell's unit index to a tiled GTiff, strip by strip; the cells per unit."""
    counts = np.zeros(len(units) + 1, dtype=np.int64)
    tree = units.sindex
    with rasterio.open(path, "w", **grid.profile("int32", 0)) as dst:
        for window in _strips(grid):
            transform = rio_windows.transform(window, grid.transform)
            hits = tree.query(box(*rio_windows.bounds(window, grid.transform)))
            shape = (int(window.height), int(window.width))
            block = (features.rasterize(zip(units.geometry.iloc[hits], units["index"].iloc[hits]),
                                        out_shape=shape, transform=transform, fill=0, dtype="int32")
                     if len(hits) else np.zeros(shape, dtype=np.int32))
            counts += np.bincount(block.ravel(), minlength=len(counts))
            dst.write(block, 1, window=window)
    return counts


def _to_cog(src: Path, dst: Path, resampling: str) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    rio_shutil.copy(src, dst, driver="COG", COMPRESS="DEFLATE", RESAMPLING=resampling,
                         BLOCKSIZE=512)


def write_mapped(index_tif: Path, grid: Grid, luts: list[np.ndarray], out: Path, dtype: str,
                 nodata, resampling: str, scratch: Path,
                 descriptions: tuple[str, ...] = ()) -> None:
    """A COG whose band ``b`` is ``luts[b][unit index]`` in every cell."""
    tmp = scratch / out.name
    with rasterio.open(index_tif) as index, \
            rasterio.open(tmp, "w", **grid.profile(dtype, nodata, count=len(luts))) as dst:
        for b, name in enumerate(descriptions, start=1):
            dst.set_band_description(b, name)
        for window in _strips(grid):
            idx = index.read(1, window=window)
            for b, lut in enumerate(luts, start=1):
                dst.write(lut[idx], b, window=window)
    _to_cog(tmp, out, resampling)
    tmp.unlink()


def cycle_luts(table: pd.DataFrame, n_units: int) -> dict[int, dict[str, np.ndarray]]:
    """Per cycle, a lookup from unit index (0 = none) to its activity code and each measure."""
    codes = table["activity"].map({a.label: int(a) for a in Activity}).to_numpy()
    out = {}
    for cycle, frame in table.groupby("cycle"):
        idx = frame["index"].to_numpy()
        act = np.full(n_units + 1, ACTIVITY_NODATA, dtype=np.uint8)
        act[idx] = codes[frame.index]
        luts = {"activity": act}
        for m in VEGETATION_MEASURES:
            lut = np.full(n_units + 1, VEGETATION_NODATA, dtype=np.float32)
            lut[idx] = frame[m].to_numpy(dtype=np.float32)
            luts[m] = lut
        out[int(str(cycle))] = luts
    return out


def coverage(planned: gpd.GeoDataFrame, unplanned: gpd.GeoDataFrame, counts: np.ndarray,
             grid: Grid, acres: pd.Series) -> pd.DataFrame:
    cells = counts[planned["index"].to_numpy()]
    unit_acres = acres.to_numpy()
    rows = {
        "planned_units": len(planned), "planned_acres": float(unit_acres.sum()),
        "unplanned_units": len(unplanned),
        "unplanned_acres": float(unplanned["Acres"].sum()) if "Acres" in unplanned else math.nan,
        "units_without_a_cell": int((cells == 0).sum()),
        "acres_without_a_cell": float(unit_acres[cells == 0].sum()),
        "rasterised_cells": int(cells.sum()), "cell_acres": grid.cell_acres,
        "rasterised_acres": float(cells.sum() * grid.cell_acres),
        "grid_width": grid.width, "grid_height": grid.height, "cell_size_m": grid.transform.a,
    }
    return pd.DataFrame({"measure": list(rows), "value": list(rows.values())})


# ── the stage ──────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class OutputsRun:
    table: pd.DataFrame
    cycles: list[int]
    grid: Grid | None


def _write_rasters(run_dir: Path, table: pd.DataFrame, planned: gpd.GeoDataFrame, grid: Grid,
                   rasters: RasterSet, scratch: Path) -> np.ndarray:
    index_tmp = scratch / "unit_index.tif"
    counts = burn_unit_index(planned, grid, index_tmp)
    _to_cog(index_tmp, run_dir / UNIT_INDEX_TIF, "NEAREST")
    planned[["index", "unit_id"]].to_csv(run_dir / UNIT_INDEX_CSV, index=False)
    luts = cycle_luts(table, len(planned))
    for cycle, lut in luts.items():
        year = int(table.loc[table["cycle"] == cycle, "calendar_year"].iloc[0])
        if rasters.activity:
            write_mapped(index_tmp, grid, [lut["activity"]],
                         run_dir / ACTIVITY_DIR / f"activity_{year}.tif", "uint8",
                         ACTIVITY_NODATA, "NEAREST", scratch, ("activity",))
        if rasters.vegetation:
            write_mapped(index_tmp, grid, [lut[m] for m in VEGETATION_MEASURES],
                         run_dir / VEGETATION_DIR / f"vegetation_{year}.tif", "float32",
                         VEGETATION_NODATA, "AVERAGE", scratch, VEGETATION_MEASURES)
    return counts


def run_outputs(run_dir: Path, *, rasters: RasterSet = RasterSet.ALL, qa: QaLevel = QaLevel.FULL,
                grid: Path | None = None) -> OutputsRun:
    """The stage: table, rasters and QA for a scheduled ``run_dir``; see the module docstring."""
    run_dir = Path(run_dir)
    horizon = n_cycles()
    table = trajectory_table(run_dir, horizon)
    plan = read_plan(run_dir)
    harvest = harvest_reconciliation(table, plan, horizon, owner_targets(run_dir))

    for stale in (run_dir / OUT_DIR, run_dir / QA_DIR):
        shutil.rmtree(stale, ignore_errors=True)
    qa_dir = run_dir / QA_DIR
    if qa is not QaLevel.NONE:          # written before the check, so a mismatch can be read
        qa_dir.mkdir(parents=True)
        harvest.to_csv(qa_dir / HARVEST_QA, index=False)
        activity_by_cycle(table).to_csv(qa_dir / ACTIVITY_QA, index=False)
        vegetation_by_cycle(table).to_csv(qa_dir / VEGETATION_QA, index=False)
        activity_vs_removals(table).to_csv(qa_dir / CONSISTENCY_QA, index=False)
    check_reconciled(harvest)

    (run_dir / OUT_DIR).mkdir(parents=True)
    table.to_csv(run_dir / TABLE, index=False)
    log.info("outputs: %d units x %d cycles -> %s", plan.shape[0], horizon + 1, run_dir / TABLE)

    the_grid = None
    if rasters is not RasterSet.NONE or qa is QaLevel.FULL:
        planned, unplanned = planned_units(run_dir, plan["unit_id"])
        bounds = pd.concat([planned.geometry, unplanned.geometry]).total_bounds  # the layer's
        the_grid = run_grid(tuple(bounds), reference_alignment(run_dir, grid), str(planned.crs))
        table = table.merge(planned[["unit_id", "index"]], on="unit_id", how="left")
        (run_dir / RASTER_DIR).mkdir(parents=True)
        with tempfile.TemporaryDirectory(dir=run_dir / OUT_DIR) as tmp:
            counts = _write_rasters(run_dir, table, planned, the_grid, rasters, Path(tmp))
        if qa is not QaLevel.NONE:
            acres = plan.set_index("unit_id").loc[planned["unit_id"], "acres"]
            coverage(planned, unplanned, counts, the_grid, acres).to_csv(qa_dir / COVERAGE_QA,
                                                                         index=False)
        if qa is QaLevel.FULL:
            from pipeline import outputs_figures
            outputs_figures.draw(run_dir / UNIT_INDEX_TIF, table, qa_dir, harvest)
            qa_report.write_index(qa_dir, "Outputs QA", QA_NOTES,
                                  f"{run_dir} — {len(plan)} units, cycles 0-{horizon}")

    cycles = list(range(horizon + 1))
    record = {"cycles": cycles,
              "calendar_years": sorted(int(y) for y in table["calendar_year"].unique()),
              "units": int(len(plan)), "rasters": rasters.value, "qa": qa.value,
              "grid": None if the_grid is None else {
                  "crs": the_grid.crs, "transform": list(the_grid.transform)[:6],
                  "width": the_grid.width, "height": the_grid.height}}
    (run_dir / RECORD).write_text(json.dumps(record, indent=2) + "\n")
    return OutputsRun(table, cycles, the_grid)
