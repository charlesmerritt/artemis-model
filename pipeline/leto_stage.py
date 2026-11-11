"""The ``leto`` stage: a raster-repair run's counties to management units and ``stands.csv``.

    uv run python main.py leto <run_dir> --repair <raster run> [--county FIPS ...] [--workers N]
                               [--region-dir <dir>] [--layout plot|leto] [--species-crosswalk <xls>]

Without ``--county`` it runs every county the raster run's ``manifest.json`` lists
(:class:`~pipeline.s1_initial_state.run_counties.NoCountiesInManifest` when it lists none). Each county is its own job
(:func:`run_county`), ``--workers`` at a time in separate processes; a county's outputs do not
depend on which process ran it, so a parallel run's files equal a serial run's.

**Resume.** ``leto.json`` records each county's status (:class:`CountyStatus`) and input hash
(:func:`input_sha256`: the county, the raster run's manifest, repaired rasters and
``young_plots/``, the VAT, the region inputs, the resolved settings, the layout and, under the
LETO layout, the species crosswalk). A county asked for again whose hash
matches, with its outputs on disk, is skipped (:class:`CountyOutcome`); a changed input or
setting redoes it. A county finished earlier and not asked for stays in the run if its hash
still matches, else it is marked ``stale`` and left out, so outputs of different settings never
mix. A county that fails is recorded as ``failed`` with its error; the others still finish, the
first failure is raised, and no run-level output is written.

**One county** composes the LETO v1 modules (ADR 0004) under its regional settings
(``config/leto.yaml``, chosen by the FIPS's state) and region inputs (``region_prep``'s
``fia_extract.db`` and ``hydrography.gpkg``). The county line is a hard edge: only the
county's cells are read, so no parent stand crosses it. In order:

1. **Clip** the repaired rasters to the county (cells whose centre falls inside it). An
   add-back pixel (provenance 2 or 4) carries its neighbour plot's ``TM_ID`` in the run's own
   raster, so the stage reads ``young_plots/`` instead and refuses a county with add-back pixels
   and no ``young_plots/`` (:class:`AddBackWithoutYoungPlots`), or a ``young_plots/`` raster that
   still holds a neighbour ID on one (:class:`NeighbourIdOnAddBack`).
2. **Stage 1 attributes** (:mod:`pipeline.leto_attributes`): the FIA extract first, the TreeMap
   value table as fallback, then the nearest-cell fill. A young-plot ID takes its dated 2022 age
   and forest type from the young-plot VAT extension and its BALIVE, TPA and QMD from its own
   trees; one missing from those tables is :class:`UnknownYoungPlotIds`.
3. **The cellular automata, then the small-stand action** (:func:`pipeline.leto_ca.segment`):
   the region's ``small_stands.remaining_small_stand_action``, read as upstream reads it
   (:func:`pipeline.leto_ca.small_stand_action`). ``delete`` leaves its cells in no parent stand;
   ``proximity_merge`` keeps the small ones for step 4.
   Owner classes are hard edges: the repaired Harris raster, its nodata read as unknown (0),
   as LETO does. Harris 1-2 stay their own classes, so they can be masked (LETO folds them into 0).
4. **Polygons**, one per parent stand. Under ``proximity_merge``, LETO's proximity groups
   (:mod:`pipeline.leto_proximity`): smallest parents first, each joins the candidate within
   60 m of the same owner and dominant forest type, capped at 300 ac combined, ranked by
   mean-age difference, then distance, then ID (limits from the region's ``small_stands``).
5. **The vector riparian split** (:mod:`pipeline.leto_riparian`) on the region's hydrography,
   then **LETO's final unit identity**: one unit per proximity group and compatibility class
   (owner, riparian, database group, management class), its pieces numbered as parts. Without
   ``proximity_merge`` every single-part piece is its own unit.
6. **Donor weights** (:mod:`pipeline.leto_donors`): each parent's pixel shares, combined by
   its pieces' acres within each unit (:func:`pipeline.leto_proximity.unit_weights`). The
   parent weights under the minimum weight are dropped for ``donor_weights.csv``, but each
   parent keeps its largest.
7. **The representative plot**: each unit's largest combined donor plot, ties to the lower
   TreeMap value; a unit of one parent stand runs on that parent's largest.
8. **Speckle merge** (:mod:`pipeline.leto_speckle`, ADR 0005): a unit under the region's
   minimum unit area, measured whole if multipart, joins a neighbour (same owner and class,
   then same class, then, if managed, riparian), chosen by ``force_merge``'s cost on its
   parent stands' traits weighted by acres (:func:`parent_traits`). It takes the receiver's
   row, so its representative plot and age; ``keep`` leaves LETO's units as they are.
9. **The plot layout's FVS tables** (:func:`plot_inventory`), one stand per distinct
   representative plot of a scheduled unit. An FIA plot is its extract rows, anchored to 2022
   as the FL5 golden run was. A young plot is its neighbour plot's StandInit row (the site)
   at its dated 2022 age, with its own live trees under LETO's sampling design (BAF -1, plot
   size 1, breakpoint 0, one plot, ``TREE_COUNT`` = TPA). A representative plot without
   StandInit or tree rows in the extract, or a young plot whose neighbour has no StandInit
   row, is :class:`MissingRepresentativePlots`; one with two StandInit rows is
   :class:`AmbiguousStandInit`; a young-plot ID without a live tree is
   :class:`UnknownYoungPlotIds`.

   **Or the LETO layout's** (``--layout leto``, :mod:`pipeline.leto_layout`): LETO stages 4-5
   per scheduled unit, ``STAND_CN`` its ``unit_id``. Its trees are its parent stand's donors'
   live trees from the extract's TREE table (a young plot's from ``young_plots/``),
   ``TREE_COUNT`` = TPA x weight, species through the FVS crosswalk, HT falling back to
   ACTUALHT; a unit with no usable live tree takes its nearest unit's
   (:class:`pipeline.leto_layout.UnitsWithoutTrees` if none can). Its ``AGE``, and its
   ``stand_age``, is the acre-weighted mean of its parent stands' mean ``STDAGE``
   (:func:`parent_ages`), rounded half to even. Its weights and age are those of its LETO unit,
   before the speckle merge.

Writes into ``<run_dir>/leto/<FIPS>/``, per county: ``parent_stands.tif``,
``donor_weights.csv`` (its filtered parent donor weights), ``units.gpkg``, ``stands.csv``,
``FVS_Data.db``, ``area_accounting.csv``, ``qa/`` (:mod:`pipeline.leto_qa`: its QA tables,
maps, step timings and peak memory), and under the LETO layout its stage 4 QA tables and,
when ``database.mode`` asks, ``fvs_groups/``. The run-level files are assembled from every
finished county (:func:`assemble`), in FIPS order:

- ``stands.csv``: the runner's columns (:data:`pipeline.run.STAND_COLUMNS`) for every unit not
  masked, plus ``county_fips`` (5-digit string), ``unknown_ownership``, ``origin``
  (:class:`Origin`), ``tm_id`` (the representative plot's TreeMap value) and ``parent_stand``.
  ``PLT_CN`` is the representative plot's control number, exact, or ``Y<ID>`` for a young plot.
  ``stand_age`` is a young plot's dated 2022 age, else the representative plot's FVS StandInit
  ``AGE`` (what FVS starts from), else blank: an age :func:`usable_stand_age` rejects is left
  to the harvest-eligibility policy, never guessed. Harris 0 is owner class ``unknown`` (the Private owner group) with
  ``unknown_ownership`` set; Harris 1-2 are masked: in the units layer, not in ``stands.csv``.
  The counties' files are joined as text, so control numbers never pass through a parser.
- ``FVS_Data.db``: the plot layout's ``FVS_StandInit_Plot`` and ``FVS_TreeInit_Plot``
  (:data:`pipeline.run.InventoryLayout.PLOT`), keyed by the exact ``STAND_CN``: a ``PLT_CN``
  or ``Y<ID>``. A plot in several counties is one stand (:func:`assemble_inventory`).
  Under the LETO layout, LETO's ``FVS_StandInit`` and ``FVS_TreeInit``: upstream's combined
  database, one stand per unit. ``stands.csv`` then has ``PLT_CN`` = ``unit_id``, the
  ``representative_plot`` and ``DB_GROUP`` (owner class, or ``Riparian``).
- ``fvs_groups/FVS_Inventory_<DB_GROUP>.db``: the LETO layout's per-group databases, written
  only under ``database.mode`` ``grouped`` or ``both`` (``config/leto.yaml``).
- ``qa/leto/imputation_diagnostics.csv``, ``imputation_summary.csv``, ``missing_species.csv``,
  ``missing_species_by_stand.csv``: the LETO layout's stage 4 QA, per county.
- ``management_units.gpkg``, layer ``units``: one feature per unit, masked ones too, with its
  ``Acres`` (geodesic, as LETO), ``owner_class``, ``unit_class``, ``BIO_MU_ID`` (its proximity
  group), ``SEG_ID`` (its first part's parent stand), ``SEG_IDS`` and ``PART_COUNT``. Unit IDs
  are ``<FIPS>-<n>``. Layer ``segments``: its single-part pieces, each with its ``unit_id``,
  ``leto_unit_id`` (LETO's unit, before the speckle merge), ``MU_PART``, ``BIO_MU_ID``,
  parent stand, owner, riparian flag and ``Acres``.
- ``qa/leto/area_accounting.csv``: per county, forest cells, the stands, cells and acres the
  ``delete`` small-stand action removed (zero under the other actions), parent stands,
  segments and units, the unit acres split into managed, unknown-flagged (managed, Harris 0), riparian and masked, the drift,
  the scheduled acres with no usable stand age, and the forest cells the Harris raster has no
  value for (read as unknown). A drift over
  :data:`MAX_DRIFT_ACRES` fails the county (:class:`AcreDrift`) before anything is written.
- ``qa/leto/proximity.csv``: per county, the small-stand action, LETO's proximity merges and
  their mean and largest distance, its multipart proximity groups and units (before the
  speckle merge), segments per multipart unit, upstream's chain QA counters and the parent
  stands still under the minimum stand size. Per county, ``qa/proximity_merges.csv`` (each
  merge) and ``qa/proximity_chain.csv`` (each unit's span).
- ``qa/leto/speckle.csv``: per county, units and speckle before and after, merges and their
  acres by step, the managed acres turned grow-only, the speckle kept, and the acre drift
  per owner class (``owner_drift_acres_<class>``) that step 2 moves. Under ``merge_delete``
  (the default) the speckle that could not merge is deleted: counted here and in
  ``area_accounting.csv`` (``deleted_speckle``, ``deleted_speckle_acres``), and in no layer.
- the rest of ``qa/leto/`` (:func:`pipeline.leto_qa.write_report`): acres by county × owner
  class × unit class, size distributions, donor QA, riparian acres by buffer source, timings
  and peak memory, maps per county and for the region, and an offline ``index.html``.
- ``leto.json``: the layout (``library`` checks it: :func:`pipeline.run.check_layout`), the
  raster run's ``manifest.json`` sha256, per region the resolved settings and the FIA extract
  and hydrography (path and sha256), and per county its status, input hash, unit and stand
  counts and seconds.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import shutil
import sqlite3
from collections.abc import Mapping, Sequence
from concurrent.futures import ProcessPoolExecutor, as_completed
import dataclasses
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import pyogrio
import rasterio
import yaml
from rasterio import features
from rasterio.windows import Window
from shapely.geometry import box, shape

from pipeline import leto_attributes as la
from pipeline import leto_ca, leto_donors, leto_layout, leto_proximity, leto_qa, leto_riparian
from pipeline.leto_database import DatabaseMode, SamplingDesign
from pipeline.leto_proximity import ProximityPlan, ProximitySettings
from pipeline.leto_speckle import MergeCost, SpeckleSettings, UnitClass, merge_speckle
from pipeline.harvest_eligibility import usable_stand_age
from pipeline.ids import as_id_series, read_id_csv
from pipeline.leto_riparian import SQUARE_METERS_PER_ACRE, BufferRules
from pipeline.run import FVS_DATA, LETO_RECORD, STAND_COLUMNS, STANDS, InventoryLayout
from pipeline.s1_initial_state.add_back_ids import (
    EXTENSION_ID_COLUMNS,
    NEW_ID_START,
    TREE_EXTENSION_CSV,
    VAT_EXTENSION_CSV,
)
from pipeline.s1_initial_state.add_back_ids import OUT_DIR as YOUNG_PLOTS
from pipeline.s1_initial_state.county_improvement import TreeMapProvenance
from pipeline.s1_initial_state.region_prep import (
    FIA_EXTRACT_DB,
    HYDROGRAPHY_GPKG,
    IMPROVED_TREEMAP,
    PROVENANCE,
    UnknownTreeMapValues,
)
from pipeline.s1_initial_state.run_counties import (
    RUN_MANIFEST,
    manifest_counties,
    read_counties,
)
from pipeline.s1_initial_state.young_stand_profiles import BA_FACTOR
from pipeline.s3_management.owner_classes import MASKED, classify_owner
from pipeline.s3_management.regime_assignment import forest_type_branch
from pipeline.s4_fvs.regime_templates import DEFAULT_INV_YEAR

REPO = Path(__file__).resolve().parents[1]
CONFIG_PATH = REPO / "config" / "leto.yaml"
OWNERSHIP = "nwos2022_improved.tif"          # the repaired Harris raster
UNITS_GPKG = "management_units.gpkg"
UNITS_LAYER = "units"
SEGMENTS_LAYER = "segments"
RECORD = LETO_RECORD
COUNTY_DIR = "leto"
PARENT_STANDS = "parent_stands.tif"
DONOR_WEIGHTS = leto_qa.DONOR_WEIGHTS
COUNTY_UNITS = "units.gpkg"
QA_DIR = Path("qa") / "leto"
AREA_ACCOUNTING = leto_qa.AREA_ACCOUNTING
SPECKLE = leto_qa.SPECKLE
PROXIMITY = "proximity.csv"                  # per county: LETO's proximity merge and its units
PROXIMITY_MERGES = "proximity_merges.csv"    # in <county>/qa/: upstream's merge rows
PROXIMITY_CHAIN = "proximity_chain.csv"      # in <county>/qa/: upstream's chain QA per unit
REPAIRED_RASTERS = (IMPROVED_TREEMAP, PROVENANCE, OWNERSHIP)
COUNTY_OUTPUTS = (PARENT_STANDS, DONOR_WEIGHTS, COUNTY_UNITS, STANDS, FVS_DATA, AREA_ACCOUNTING,
                  SPECKLE, PROXIMITY, *leto_qa.COUNTY_QA)
OUTPUT_VERSION = 6                            # bump when a county's outputs change shape: redoes every county
INV_YEAR = DEFAULT_INV_YEAR                   # every FVS stand starts in TreeMap's year, 2022
VARIANT = "SN"
FIA_STAND_INIT, FIA_TREE_INIT = "FVS_STANDINIT_PLOT", "FVS_TREEINIT_PLOT"   # in the extract
YOUNG_DESIGN = SamplingDesign()               # LETO's per-acre design: BAF -1, plot size 1, ...

CONTINUOUS = ("STDAGE", "BALIVE", "QMD", "TPA")
YOUNG_STAND_PREFIX = "Y"
UNKNOWN_OWNER = 0                             # Harris unknown forest
OUTSIDE = -1                                  # the CA's ownership sentinel off the forest
MAX_DRIFT_ACRES = 0.1                         # unit acres must equal parent-stand acres this closely

log = logging.getLogger("artemis.leto")


class Origin(StrEnum):
    """Where a unit's representative plot comes from."""

    TREEMAP = "treemap"
    YOUNG_PLOT = "young_plot"                 # an add-back young plot (ADR 0003)


class CountyStatus(StrEnum):
    """A county's line in ``leto.json``."""

    DONE = "done"
    FAILED = "failed"
    STALE = "stale"                           # done under inputs that have since changed: left out


class CountyOutcome(StrEnum):
    """What a run did with a county it was asked for."""

    RAN = "ran"
    SKIPPED = "skipped"                       # asked for, done under the same inputs: kept as it was
    KEPT = "kept"                             # not asked for, done earlier under the same inputs


class AddBackWithoutYoungPlots(ValueError):
    """Add-back pixels carry the neighbour plot's TM_ID in a run's own raster; their trees are in young_plots/."""


class NeighbourIdOnAddBack(ValueError):
    """A young_plots/ raster still holds a TreeMap ID on an add-back pixel."""


class UnknownYoungPlotIds(ValueError):
    """A young-plot ID the raster maps is not in the young-plot VAT extension."""


class MissingRepresentativePlots(ValueError):
    """A representative plot (or a young plot's neighbour site) has no FVS rows in the FIA extract."""


class AmbiguousStandInit(ValueError):
    """A plot with more than one StandInit row in the extract: its start age and FVS row could differ."""


class ConflictingPlotRows(ValueError):
    """One plot with different StandInit rows in two counties' FVS tables."""


class AcreDrift(ValueError):
    """A county's unit acres differ from its parent-stand acres by more than :data:`MAX_DRIFT_ACRES`."""


# ── settings ───────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class LetoSettings:
    """One region's LETO settings, resolved from ``config/leto.yaml``."""

    region: str
    state_fips: str
    inputs: Path
    segmentation: dict
    buffer_rules: BufferRules
    minimum_plot_weight: float
    speckle: SpeckleSettings
    database_mode: DatabaseMode = DatabaseMode.COMBINED   # the LETO layout's; FVS_Data.db is combined
    proximity: ProximitySettings = ProximitySettings()    # read only under proximity_merge

    @property
    def small_stand_action(self) -> leto_ca.SmallStandAction:
        return leto_ca.SmallStandAction(self.segmentation["remaining_small_stand_action"])

    def record(self) -> dict:
        """The settings as JSON, for ``leto.json``."""
        rules = {layer: {str(code): [label, feet] for code, (label, feet) in rules.items()}
                 for layer, rules in vars(self.buffer_rules).items()}
        return {"region": self.region, "state_fips": self.state_fips, "inputs": str(self.inputs),
                "segmentation": {k: str(v) if isinstance(v, StrEnum) else v
                                 for k, v in self.segmentation.items()},
                "riparian": rules, "minimum_plot_weight": self.minimum_plot_weight,
                "speckle": self.speckle.record(), "database": {"mode": self.database_mode.value},
                "proximity": self.proximity.record()}


def load_settings(county_fips: str, path: Path = CONFIG_PATH) -> LetoSettings:
    """The settings of the region whose ``state_fips`` the county's FIPS starts with."""
    config = yaml.safe_load(Path(path).read_text())
    state = county_fips[:2]
    for name, spec in config["regions"].items():
        if spec["state_fips"] == state:
            return _settings(name, spec)
    raise ValueError(f"no region in {path} for state FIPS {state!r} (county {county_fips})")


def _settings(name: str, spec: dict) -> LetoSettings:
    action = leto_ca.small_stand_action(spec.get("small_stands"), spec.get("segmentation"))
    rules = BufferRules(**{layer: {int(code): (str(label), float(feet))
                                   for code, (label, feet) in spec["riparian"][layer].items()}
                           for layer in ("flowline", "waterbody", "area")})
    segmentation = {**leto_ca.DEFAULT_CFG, **spec["segmentation"],
                    "remaining_small_stand_action": action}
    proximity = ProximitySettings.from_config(spec.get("small_stands"), spec["segmentation"],
                                              spec.get("proximity_chain_qa"))
    if action is leto_ca.SmallStandAction.PROXIMITY_MERGE:
        proximity.check(float(segmentation["minimum_stand_acres"]))
    return LetoSettings(
        region=name, state_fips=spec["state_fips"], inputs=REPO / spec["inputs"],
        segmentation=segmentation, buffer_rules=rules,
        minimum_plot_weight=float(spec["inventory"]["minimum_plot_weight"]),
        speckle=SpeckleSettings.from_config(spec["speckle"]),
        database_mode=DatabaseMode(spec["database"]["mode"]), proximity=proximity)


# ── step 1: the county's cells ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class County:
    fips: str
    name: str
    geometry: object                          # shapely, project CRS


def read_county(counties: Path, fips: str) -> County:
    """The county with ``ADMIN_FIPS == fips`` from the national county layer, in the project CRS."""
    row = read_counties(counties, [fips]).iloc[0]
    return County(fips, str(row["NAME"]), row.geometry)


@dataclass(frozen=True)
class CountyCells:
    """The repaired rasters over the county's window; cells outside the county are not ``valid``."""

    treemap: np.ndarray                       # int64 TreeMap value; a young-plot ID on add-back
    harris: np.ndarray                        # the repaired Harris raster, nodata read as unknown
    harris_nodata: np.ndarray                 # forest cells the Harris raster has no value for
    valid: np.ndarray                         # forest cells inside the county
    added_back: np.ndarray
    transform: rasterio.Affine
    crs: object

    @property
    def cell_acres(self) -> float:
        return abs(self.transform.a * self.transform.e) / SQUARE_METERS_PER_ACRE


def _window(src, geometry) -> Window:
    minx, miny, maxx, maxy = geometry.bounds
    t = src.transform
    col0, col1 = math.floor((minx - t.c) / t.a), math.ceil((maxx - t.c) / t.a)
    row0, row1 = math.floor((t.f - maxy) / -t.e), math.ceil((t.f - miny) / -t.e)
    col0, row0 = max(col0, 0), max(row0, 0)
    col1, row1 = min(col1, src.width), min(row1, src.height)
    return Window(col0, row0, max(col1 - col0, 0), max(row1 - row0, 0))


def _read(path: Path, window: Window) -> tuple[np.ndarray, object]:
    with rasterio.open(path) as src:
        return src.read(1, window=window), src.nodata


def clip_county(repair: Path, county: County) -> CountyCells:
    """The county's cells of a raster-repair run, add-back pixels read from ``young_plots/``."""
    with rasterio.open(repair / IMPROVED_TREEMAP) as src:
        window = _window(src, county.geometry)
        transform, crs = src.window_transform(window), src.crs
    if window.width == 0 or window.height == 0:
        raise ValueError(f"county {county.fips} lies outside {repair}")
    shape_ = (int(window.height), int(window.width))
    inside = features.rasterize([(county.geometry, 1)], out_shape=shape_, transform=transform,
                                fill=0, dtype="uint8").astype(bool)
    added = inside & TreeMapProvenance.added_back(_read(repair / PROVENANCE, window)[0])
    source = repair
    if added.any():
        source = repair / YOUNG_PLOTS
        if not source.is_dir():
            raise AddBackWithoutYoungPlots(
                f"{repair} has {int(added.sum())} add-back pixels (provenance 2 or 4) in county "
                f"{county.fips} but no {YOUNG_PLOTS}/: their TreeMap ID is the neighbour plot's, "
                "never their trees'. Run pipeline.s1_initial_state.add_back_ids first")
        added |= inside & TreeMapProvenance.added_back(_read(source / PROVENANCE, window)[0])
    treemap, nodata = _read(source / IMPROVED_TREEMAP, window)
    treemap = treemap.astype(np.int64)
    valid = inside & (treemap != nodata)
    if not valid.any():
        raise ValueError(f"county {county.fips} has no forest cell in {repair}")
    stale = added & valid & (treemap < NEW_ID_START)
    if stale.any():
        raise NeighbourIdOnAddBack(
            f"{source} holds TreeMap IDs {sorted(np.unique(treemap[stale]).tolist())[:10]} on "
            f"{int(stale.sum())} add-back pixels of county {county.fips}: a neighbour plot's ID, "
            "not a young plot's")
    harris, harris_nodata = _read(repair / OWNERSHIP, window)
    no_owner = harris == harris_nodata
    harris = np.where(no_owner, UNKNOWN_OWNER, harris).astype(np.int16)
    return CountyCells(treemap, harris, no_owner & valid, valid, added & valid, transform, crs)


# ── step 2: attributes ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Attributes:
    """Stage 1 records per TreeMap value, and what the stand rows need beside them."""

    records: pd.DataFrame                     # leto_attributes.value_records, indexed by Value
    young: pd.DataFrame                       # the young-plot VAT extension, indexed by Value
    start_age: pd.Series                      # FVS StandInit AGE by PLT_CN
    young_trees: pd.DataFrame                 # the young plots' live trees, per acre, by Value
    fia_plots: frozenset[str]                 # the plots with a COND row in the FIA extract

    def stand_cn(self, value: int) -> str:
        return f"{YOUNG_STAND_PREFIX}{value}" if value >= NEW_ID_START else self.records.at[value, "PLT_CN"]


def _plot_rows(con, table: str, columns: str, key: str, plots: list[str]) -> pd.DataFrame:
    con.execute("DROP TABLE IF EXISTS temp.wanted")
    con.execute("CREATE TEMP TABLE wanted (PLT_CN TEXT PRIMARY KEY)")
    con.executemany("INSERT INTO temp.wanted VALUES (?)", [(cn,) for cn in plots])
    frame = pd.read_sql(f'SELECT CAST("{key}" AS TEXT) AS _EXACT_KEY, {columns} FROM "{table}" '
                        f'WHERE CAST("{key}" AS TEXT) IN (SELECT PLT_CN FROM temp.wanted)', con)
    frame[key] = as_id_series(frame.pop("_EXACT_KEY"), column=key)
    return frame


def _young_records(young_dir: Path, values: np.ndarray
                   ) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    ext = read_id_csv(young_dir / VAT_EXTENSION_CSV, id_columns=EXTENSION_ID_COLUMNS)
    ext.index = pd.to_numeric(ext["TM_ID"]).astype(np.int64).to_numpy()
    ext.index.name = "Value"
    missing = sorted(set(values.tolist()) - set(ext.index))
    if missing:
        raise UnknownYoungPlotIds(f"young-plot IDs {missing[:10]} are not in "
                                  f"{young_dir / VAT_EXTENSION_CSV}")
    ext = ext.loc[values]
    trees = read_id_csv(young_dir / TREE_EXTENSION_CSV, id_columns=("TM_ID", "PLT_CN"))
    trees["Value"] = pd.to_numeric(trees["TM_ID"]).astype(np.int64)
    trees = trees[trees["Value"].isin(ext.index) & (trees["STATUSCD"] == la.LIVE_TREE_STATUS)
                  & (trees["TPA_UNADJ"] > 0)]
    treeless = sorted(set(ext.index) - set(trees["Value"]))
    if treeless:
        raise UnknownYoungPlotIds(f"young-plot IDs {treeless[:10]} have no live tree in "
                                  f"{young_dir / TREE_EXTENSION_CSV}")
    live = trees[trees["DIA"] > 0]
    live = live.assign(TPA_D2=live["TPA_UNADJ"] * live["DIA"] ** 2)
    per_id = live.groupby("Value").agg(TPA=("TPA_UNADJ", "sum"), TPA_D2=("TPA_D2", "sum"))
    records = pd.DataFrame({"PLT_CN": [f"{YOUNG_STAND_PREFIX}{v}" for v in ext.index],
                            "STDAGE": ext["STAND_AGE_2022"].astype(float),
                            "FORTYPCD": ext["FORTYPCD"].astype(float)}, index=ext.index)
    records["BALIVE"] = BA_FACTOR * per_id["TPA_D2"]
    records["TPA"] = per_id["TPA"]
    records["QMD"] = np.sqrt(per_id["TPA_D2"] / per_id["TPA"])
    for field in la.FIELDS:
        records[f"SOURCE_{field}"] = np.where(records[field].isna(), la.Provenance.MISSING,
                                              la.Provenance.FIA)
    return records, ext, trees


def attributes(values: np.ndarray, vat: pd.DataFrame, extract: Path, young_dir: Path) -> Attributes:
    """Stage 1 records for the county's TreeMap values and young-plot IDs."""
    treemap = values[values < NEW_ID_START]
    vat_values = pd.to_numeric(vat["Value"]).astype(np.int64)
    unknown = sorted(set(treemap.tolist()) - set(vat_values))
    if unknown:
        raise UnknownTreeMapValues(f"TreeMap values {unknown[:10]} have no VAT row")
    rows = vat[vat_values.isin(treemap).to_numpy()]
    plots = sorted(set(as_id_series(rows["PLT_CN"], column="PLT_CN")))
    con = sqlite3.connect(f"{extract.resolve().as_uri()}?mode=ro", uri=True)
    try:
        cond = _plot_rows(con, "COND", "CONDID, COND_STATUS_CD, CONDPROP_UNADJ, STDAGE, FORTYPCD, "
                          "BALIVE", "PLT_CN", plots)
        trees = _plot_rows(con, "TREE", "CONDID, STATUSCD, DIA, TPA_UNADJ", "PLT_CN", plots)
        start = _plot_rows(con, "FVS_STANDINIT_PLOT", "AGE", "STAND_CN", plots)
    finally:
        con.close()
    records = la.value_records(rows, la.plot_lookup(cond, trees))
    young_values = values[values >= NEW_ID_START]
    young, young_trees = pd.DataFrame(), pd.DataFrame()
    if len(young_values):
        young_records, young, young_trees = _young_records(young_dir, young_values)
        records = pd.concat([records, young_records[records.columns]])
    start_age = start.drop_duplicates("STAND_CN").set_index("STAND_CN")["AGE"]
    return Attributes(records, young, start_age, young_trees, frozenset(cond["PLT_CN"]))


# ── steps 3-7: parents, units, donors ──────────────────────────────────────────────────


def _forest_type(cells: CountyCells, grids: dict[str, np.ndarray]) -> np.ndarray:
    return np.where(cells.valid, np.nan_to_num(grids["FORTYPCD"], nan=0), 0).astype(np.int32)


def segment(cells: CountyCells, grids: dict[str, np.ndarray], settings: LetoSettings
            ) -> tuple[np.ndarray, leto_ca.DeletedSmallStands]:
    """LETO's CA and small-stand action, owner classes as hard edges.

    Returns the int32 parent labels and what the ``delete`` action removed (nothing otherwise).
    """
    forest_type = _forest_type(cells, grids)
    ownership = np.where(cells.valid, cells.harris, OUTSIDE).astype(np.int16)
    info: dict = {}
    seg = leto_ca.segment({n: grids[n] for n in CONTINUOUS}, forest_type, ownership, cells.valid,
                          cells.cell_acres, cfg=settings.segmentation,
                          similarity_raw={"STDAGE": np.nan_to_num(grids["STDAGE"], nan=0.0)},
                          info=info, log=log.debug)
    return seg, info["deleted_small_stands"]


def parent_polygons(seg: np.ndarray, cells: CountyCells) -> gpd.GeoDataFrame:
    records = [{"SEG_ID": int(v), "geometry": shape(g)}
               for g, v in features.shapes(seg, mask=seg > 0, transform=cells.transform)]
    parents = gpd.GeoDataFrame(records, crs=cells.crs).dissolve(by="SEG_ID", as_index=False)
    owner = leto_ca.uniform_label_lookup(seg, cells.harris)
    parents["OWN_CODE"] = owner[parents["SEG_ID"].to_numpy()]
    parents["Acres"] = leto_riparian.geodesic_acres(parents)
    return parents


def _hydrography(path: Path, rules: BufferRules, bounds) -> dict[str, gpd.GeoDataFrame]:
    """Each staged layer's features with a positive buffer rule, near ``bounds``."""
    widest = max((feet for layer in vars(rules).values() for _, feet in layer.values()), default=0)
    pad = widest * leto_riparian.FEET_TO_METERS + 1.0
    minx, miny, maxx, maxy = bounds
    present = set(pyogrio.list_layers(path)[:, 0])
    if "flowline" not in present:
        raise ValueError(f"{path} has no flowline layer: run region_prep hydrography")
    out = {}
    for layer, by_code in vars(rules).items():
        if layer not in present:
            continue
        frame = gpd.read_file(path, layer=layer, bbox=(minx - pad, miny - pad, maxx + pad, maxy + pad))
        buffered = [by_code.get(int(code), ("", 0.0))[1] > 0 for code in frame["fcode"]]
        out[layer] = frame[np.asarray(buffered, dtype=bool)]
    return out


def riparian_split(parents: gpd.GeoDataFrame, hydrography: Path, rules: BufferRules) -> gpd.GeoDataFrame:
    """LETO's vector split; parents no buffer reaches stay whole (single parts, upland)."""
    layers = _hydrography(hydrography, rules, parents.total_bounds)
    if any(len(frame) for frame in layers.values()):
        footprint = leto_riparian.riparian_footprint(
            rules, flowlines=layers.get("flowline"), waterbodies=layers.get("waterbody"),
            areas=layers.get("area"))
        if footprint.intersects(box(*parents.total_bounds)):
            return leto_riparian.split_stands(parents, footprint)
    pieces = parents.explode(ignore_index=True).assign(RIPARIAN=leto_riparian.UPLAND)
    pieces["Acres"] = leto_riparian.geodesic_acres(pieces)
    return pieces


@dataclass(frozen=True)
class DonorPlots:
    """Each parent stand's donor weights, before and after the minimum-weight filter."""

    weights: pd.DataFrame
    kept: pd.DataFrame


def parent_donors(seg: np.ndarray, cells: CountyCells, attrs: Attributes,
                  settings: LetoSettings) -> DonorPlots:
    """Donor weights per parent stand, and those the minimum weight keeps (its largest at least)."""
    value_to_plt = {int(v): attrs.stand_cn(int(v)) for v in attrs.records.index}
    weights = leto_donors.parent_weights(seg, cells.treemap, value_to_plt)
    return DonorPlots(weights, leto_donors.filter_weights(weights, settings.minimum_plot_weight,
                                                          key="SEG_ID"))


def parent_ages(seg: np.ndarray, grids: dict[str, np.ndarray]) -> pd.Series:
    """Each parent stand's mean ``STDAGE`` over its cells (equal-area, so acre-weighted), by ``SEG_ID``."""
    age = grids["STDAGE"]
    known = (seg > 0) & np.isfinite(age)
    return pd.Series(age[known]).groupby(seg[known]).mean()


def parent_traits(seg: np.ndarray, cells: CountyCells, grids: dict[str, np.ndarray],
                  settings: LetoSettings) -> pd.DataFrame:
    """Each parent stand's ``force_merge`` traits, indexed by ``SEG_ID``.

    The CA's robust-standardized means of :data:`CONTINUOUS` and the dominant ``FORTYPCD``,
    as :func:`pipeline.leto_ca.merge_small_segments` compares them.
    """
    clip = settings.segmentation["standardization_clip"]
    std = {name: np.where(cells.valid, np.nan_to_num(
               leto_ca.robust_standardize(grids[name], cells.valid, clip)[0], nan=0.0), 0.0)
           for name in CONTINUOUS}
    stats = leto_ca.segment_statistics_arrays(seg, std, _forest_type(cells, grids))
    ids = np.flatnonzero(stats["counts"] > 0)
    ids = ids[ids > 0]
    return pd.DataFrame({**{n: stats["means"][n][ids] for n in CONTINUOUS},
                         "FORTYPCD": stats["dominant_type"][ids]}, index=pd.Index(ids, name="SEG_ID"))


def merge_cost(cells: CountyCells, settings: LetoSettings) -> MergeCost:
    """``force_merge``'s cost under the region's segmentation settings; a cell edge per edge."""
    return MergeCost(weights=dict(settings.segmentation["variable_weights"]),
                     shared_edge_bonus=float(settings.segmentation["shared_edge_bonus"]),
                     edge_length=abs(cells.transform.a))


def proximity_plan(seg: np.ndarray, cells: CountyCells, parents: gpd.GeoDataFrame,
                   traits: pd.DataFrame, ages: pd.Series, settings: LetoSettings
                   ) -> ProximityPlan | None:
    """LETO's proximity groups of the parent stands, under ``proximity_merge`` only.

    ``traits``: :func:`parent_traits` (its dominant ``FORTYPCD``); ``ages``: :func:`parent_ages`.
    A parent's acres are its cells x cell acres, as upstream's segment summary has them.
    """
    if settings.small_stand_action is not leto_ca.SmallStandAction.PROXIMITY_MERGE:
        return None
    ids = parents["SEG_ID"].to_numpy()
    parents = parents.assign(Acres=np.bincount(seg.ravel())[ids] * cells.cell_acres)
    traits = pd.DataFrame({"FORTYPCD_DOM": traits["FORTYPCD"].reindex(ids).to_numpy(),
                           "STDAGE_MEAN": ages.reindex(ids).to_numpy()},
                          index=pd.Index(ids, name="SEG_ID"))
    return leto_proximity.plan_groups(parents, traits,
                                      minimum_acres=float(settings.segmentation["minimum_stand_acres"]),
                                      settings=settings.proximity)


def _classify(pieces: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Each piece's owner class, unknown flag and unit class."""
    owners = {code: classify_owner({"OWN_CODE": int(code)}) for code in pieces["OWN_CODE"].unique()}
    return pieces.assign(
        owner_class=pieces["OWN_CODE"].map(lambda c: owners[c].owner_class),
        unknown_ownership=pieces["OWN_CODE"].map(lambda c: owners[c].has("unknown_ownership")),
        unit_class=np.where(pieces["RIPARIAN"] == leto_riparian.RIPARIAN,
                            UnitClass.RIPARIAN.value, UnitClass.MANAGED.value))


def segment_layer(pieces: gpd.GeoDataFrame, plan: ProximityPlan | None,
                  county: County) -> gpd.GeoDataFrame:
    """Every single-part piece with its LETO unit identity (:func:`pipeline.leto_proximity.final_identity`)."""
    segments = leto_proximity.final_identity(_classify(pieces), county.fips,
                                             groups=plan.groups if plan else None)
    segments.insert(1, "county_fips", county.fips)
    segments.insert(2, "county", county.name)
    return segments


def unit_layer(segments: gpd.GeoDataFrame, top: pd.DataFrame, attrs: Attributes) -> gpd.GeoDataFrame:
    """One feature per unit ID: its pieces dissolved, with its representative plot (``top``, by
    ``unit_id``) and what follows from it."""
    units = leto_proximity.dissolve_units(segments).drop(columns=["MU_ID"])
    rep = units["unit_id"].map(top["TREEMAP_VALUE"]).astype(np.int64)
    young = rep >= NEW_ID_START
    units["tm_id"] = rep
    units["origin"] = np.where(young, Origin.YOUNG_PLOT.value, Origin.TREEMAP.value)
    units["PLT_CN"] = units["unit_id"].map(top["PLT_CN"])
    units["FORTYPCD"] = rep.map(attrs.records["FORTYPCD"])
    dated = rep.map(attrs.young["STAND_AGE_2022"]) if len(attrs.young) else np.nan
    age = pd.Series(np.where(young, dated, units["PLT_CN"].map(attrs.start_age)), index=units.index)
    units["stand_age"] = age.map(usable_stand_age).astype(float)   # unusable: blank
    return units


def after_speckle(segments: gpd.GeoDataFrame, receiver: pd.Series) -> gpd.GeoDataFrame:
    """The pieces under the units the speckle merge left: ``leto_unit_id`` keeps LETO's unit,
    ``unit_id`` is the unit holding it now; pieces of deleted speckle are dropped. Parts are
    renumbered within each unit: its own first, then those it absorbed, by parent stand."""
    out = segments.rename(columns={"unit_id": "leto_unit_id"}).drop(columns=["MU_ID"])
    out.insert(0, "unit_id", out["leto_unit_id"].map(receiver))
    out = out[out["unit_id"].notna()].copy()
    out["_absorbed"] = out["unit_id"] != out["leto_unit_id"]
    out = out.sort_values(["unit_id", "_absorbed", "SEG_ID", "MU_PART"], kind="stable")
    out["MU_PART"] = out.groupby("unit_id", sort=False).cumcount() + 1
    order = pd.Series(np.arange(len(receiver)), index=receiver.index)
    out = out.iloc[np.lexsort((out["MU_PART"].to_numpy(),
                               out["unit_id"].map(order).to_numpy()))]
    return out.drop(columns="_absorbed").reset_index(drop=True)


def proximity_qa(county: County, settings: LetoSettings, parents: gpd.GeoDataFrame,
                 plan: ProximityPlan | None, chain: dict, segments: gpd.GeoDataFrame) -> dict:
    """One county's line: LETO's proximity merges and distances, its multipart units (before the
    speckle merge) and the parent stands still under the minimum stand size."""
    minimum = float(settings.segmentation["minimum_stand_acres"])
    parts = segments.groupby("unit_id").size()
    multipart = parts[parts > 1]
    plan_qa = plan.qa if plan else {
        "proximity_merges": 0, "average_merge_distance_meters": 0.0,
        "maximum_merge_distance_meters": 0.0, "multipart_management_units": 0,
        "small_stands_remaining": int((parents["Acres"] < minimum).sum()),
        "total_acres_before": float(parents["Acres"].sum()),
        "total_acres_after": float(parents["Acres"].sum())}
    return {"county_fips": county.fips, "small_stand_action": settings.small_stand_action.value,
            "proximity_merges": plan_qa["proximity_merges"],
            "average_merge_distance_meters": round(plan_qa["average_merge_distance_meters"], 4),
            "maximum_merge_distance_meters": round(plan_qa["maximum_merge_distance_meters"], 4),
            "multipart_proximity_groups": plan_qa["multipart_management_units"],
            "multipart_management_units": chain["multipart_management_units"],
            "average_segments_per_multipart_unit": round(float(multipart.mean()), 4)
            if len(multipart) else 0.0,
            "chain_flagged_units": chain["chain_flagged_units"],
            "maximum_observed_span_meters": round(chain["maximum_observed_span_meters"], 4),
            "average_span_of_multipart_units_meters":
                round(chain["average_span_of_multipart_units_meters"], 4),
            "small_stands_remaining": plan_qa["small_stands_remaining"],
            "total_acres_before": round(plan_qa["total_acres_before"], 4),
            "total_acres_after": round(plan_qa["total_acres_after"], 4)}


def scheduled(units: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """The units ``stands.csv`` schedules: every one not masked."""
    return units[units["owner_class"] != MASKED]


def stand_rows(units: gpd.GeoDataFrame, layout: InventoryLayout = InventoryLayout.PLOT) -> pd.DataFrame:
    """``stands.csv``'s rows. Under the LETO layout ``PLT_CN`` is the unit's own FVS stand, its
    ``unit_id``; ``representative_plot`` keeps the plot it would run on under the plot layout,
    and ``DB_GROUP`` is its database group."""
    stands = scheduled(units).drop(columns="geometry")
    stands = stands.assign(acres=stands["Acres"], parent_stand=stands["SEG_ID"],
                           forest_branch=[forest_type_branch({"OWN_CODE": o, "FORTYPCD": f})
                                          for o, f in zip(stands["OWN_CODE"], stands["FORTYPCD"])])
    extra = ["county_fips", "unknown_ownership", "origin", "tm_id", "parent_stand"]
    if layout is InventoryLayout.LETO:
        stands = stands.assign(representative_plot=stands["PLT_CN"], PLT_CN=stands["unit_id"],
                               DB_GROUP=leto_layout.database_groups(stands))
        extra += ["representative_plot", "DB_GROUP"]
    return pd.DataFrame(stands[list(STAND_COLUMNS) + extra])


def area_accounting(cells: CountyCells, parents: gpd.GeoDataFrame, units: gpd.GeoDataFrame,
                    county: County, deleted: leto_ca.DeletedSmallStands,
                    speckle: gpd.GeoDataFrame, segments: int) -> dict:
    """One county's line: forest cells → parent stands → units, the unit acres split four ways.

    Forest cells the ``delete`` small-stand action removed are in no parent stand; they are
    their own line, at ``cell_acres`` each. ``speckle`` is what the speckle merge deleted: its
    own line, and in the drift, since its acres left the units but not the parent stands.
    """
    masked = units["owner_class"] == MASKED
    riparian = units["unit_class"] == UnitClass.RIPARIAN
    unknown = units["unknown_ownership"].astype(bool)

    def acres(mask) -> float:
        return round(float(units.loc[mask, "Acres"].sum()), 4)

    forest_cells = int(cells.valid.sum())
    parent_acres, unit_acres = float(parents["Acres"].sum()), float(units["Acres"].sum())
    speckle_acres = float(speckle["Acres"].sum())
    return {"county_fips": county.fips, "county": county.name, "forest_cells": forest_cells,
            "forest_cell_acres": round(forest_cells * cells.cell_acres, 4),
            "deleted_small_stands": deleted.segments,
            "deleted_small_stand_cells": deleted.cells,
            "deleted_small_stand_acres": round(deleted.acres(cells.cell_acres), 4),
            "parent_stands": len(parents), "parent_stand_acres": round(parent_acres, 4),
            "segments": segments, "units": len(units), "unit_acres": round(unit_acres, 4),
            "deleted_speckle": len(speckle), "deleted_speckle_acres": round(speckle_acres, 4),
            "managed_acres": acres(~masked & ~riparian & ~unknown),
            "unknown_flagged_acres": acres(~masked & ~riparian & unknown),
            "riparian_acres": acres(~masked & riparian),
            "masked_acres": acres(masked),
            "no_age_acres": acres(~masked & units["stand_age"].isna()),   # harvest eligibility decides
            "add_back_cells": int(cells.added_back.sum()),
            "harris_nodata_cells": int(cells.harris_nodata.sum()),   # read as unknown, as LETO does
            "drift_acres": round(unit_acres + speckle_acres - parent_acres, 4)}


def missing_plots(cells: CountyCells, attrs: Attributes, county: County) -> pd.DataFrame:
    """The county's TreeMap plots with no COND row in the FIA extract, and their cells."""
    values, counts = np.unique(cells.treemap[cells.valid], return_counts=True)
    treemap = values < NEW_ID_START
    frame = pd.DataFrame({"TREEMAP_VALUE": values[treemap], "cells": counts[treemap]})
    frame.insert(0, "PLT_CN", frame["TREEMAP_VALUE"].map(attrs.records["PLT_CN"]))
    frame = frame[~frame["PLT_CN"].isin(attrs.fia_plots)]
    frame.insert(0, "county_fips", county.fips)
    return frame.assign(acres=(frame["cells"] * cells.cell_acres).round(4)).reset_index(drop=True)


def parent_table(seg: np.ndarray, parents: gpd.GeoDataFrame, cells: CountyCells,
                 grids: dict[str, np.ndarray]) -> pd.DataFrame:
    """Each parent stand's cells, geodesic acres and owner, the mean and standard deviation of
    each of the CA's inputs over its cells, and its dominant forest type and that type's share."""
    ids = parents["SEG_ID"].to_numpy()
    flat = seg.ravel()
    size = int(flat.max()) + 1
    counts = np.bincount(flat, minlength=size)
    table = pd.DataFrame({"SEG_ID": ids, "OWN_CODE": parents["OWN_CODE"].to_numpy(),
                          "cells": counts[ids], "acres": parents["Acres"].to_numpy()})
    for name in CONTINUOUS:
        values = grids[name].ravel().astype(float)
        ok = (flat > 0) & np.isfinite(values)
        n = np.bincount(flat[ok], minlength=size)[ids]
        total = np.bincount(flat[ok], values[ok], minlength=size)[ids]
        squares = np.bincount(flat[ok], values[ok] ** 2, minlength=size)[ids]
        with np.errstate(invalid="ignore", divide="ignore"):
            mean = total / n
            table[f"{name}_mean"] = mean
            table[f"{name}_sd"] = np.sqrt(np.maximum(squares / n - mean ** 2, 0.0))
    types = pd.DataFrame({"SEG_ID": flat, "FORTYPCD": _forest_type(cells, grids).ravel()})
    types = types[types["SEG_ID"] > 0].value_counts().reset_index(name="n")
    top = types.sort_values(["SEG_ID", "n", "FORTYPCD"], ascending=[True, False, True]) \
        .drop_duplicates("SEG_ID").set_index("SEG_ID")
    table["dominant_forest_type"] = top["FORTYPCD"].reindex(ids).to_numpy()
    table["dominant_forest_type_share"] = top["n"].reindex(ids).to_numpy() / table["cells"]
    return table


# ── step 8: the plot layout's FVS tables ───────────────────────────────────────────────


@dataclass(frozen=True)
class PlotInventory:
    """``FVS_Data.db``'s plot-layout tables: one stand per distinct representative plot."""

    stand: pd.DataFrame
    tree: pd.DataFrame

    def write(self, path: Path) -> None:
        tables = InventoryLayout.PLOT.tables
        path.unlink(missing_ok=True)
        with sqlite3.connect(path) as con:
            self.stand.to_sql(tables.stand, con, index=False)
            self.tree.to_sql(tables.tree, con, index=False)
            con.execute(f"CREATE INDEX ix_stand ON {tables.stand}(STAND_CN)")
            con.execute(f"CREATE INDEX ix_tree ON {tables.tree}(STAND_CN)")
        con.close()


def _anchored(stand: pd.DataFrame) -> pd.DataFrame:
    return stand.assign(STAND_ID="S" + stand["STAND_CN"],
                        VARIANT=VARIANT, INV_YEAR=INV_YEAR)


def _young_stands(site: pd.DataFrame, young: pd.DataFrame) -> pd.DataFrame:
    """A young plot at its dated age, on the site (StandInit row) of the neighbour plot it replaces."""
    stand = site.loc[young["NEIGHBOUR_PLT_CN"]].reset_index()
    stand["STAND_CN"] = [f"{YOUNG_STAND_PREFIX}{v}" for v in young.index]
    stand["AGE"] = young["STAND_AGE_2022"].astype(float).to_numpy()
    design = YOUNG_DESIGN
    return stand.assign(BASAL_AREA_FACTOR=design.basal_area_factor,
                        INV_PLOT_SIZE=design.inv_plot_size, BRK_DBH=design.brk_dbh,
                        NUM_PLOTS=design.num_plots, NONSTK_PLOTS=design.nonstk_plots)


def _young_tree_rows(trees: pd.DataFrame) -> pd.DataFrame:
    """The young plots' per-acre trees under LETO's design: one plot, ``TREE_COUNT`` = TPA."""
    trees = trees.sort_values(["Value", "SPCD", "DIA"], kind="stable")
    height = trees["HT"] if "HT" in trees else pd.Series(np.nan, index=trees.index)
    if "ACTUALHT" in trees:
        height = height.where(height.notna(), trees["ACTUALHT"])
    stand_cn = YOUNG_STAND_PREFIX + trees["Value"].astype(str)
    return pd.DataFrame({
        "STAND_CN": stand_cn, "STAND_ID": "S" + stand_cn, "PLOT_ID": 1,
        "TREE_ID": trees.groupby("Value").cumcount() + 1, "TREE_COUNT": trees["TPA_UNADJ"],
        "HISTORY": 1, "SPECIES": trees["SPCD"].astype(np.int64).astype(str),
        "DIAMETER": trees["DIA"], "HT": height,
        "CRRATIO": trees["CR"] if "CR" in trees else np.nan}).reset_index(drop=True)


def plot_inventory(plots: pd.Series, attrs: Attributes, extract: Path) -> PlotInventory:
    """The FVS stand and trees of each distinct representative plot (a ``PLT_CN`` or ``Y<ID>``).

    An FIA plot is its extract rows, anchored to 2022. A young plot (ADR 0003) takes its
    neighbour plot's StandInit row (the site of the pixels it replaces) at its dated 2022 age,
    with its own per-acre trees under LETO's sampling design. A plot without a StandInit row
    or tree rows in the extract, or a young plot whose neighbour has no StandInit row, is
    :class:`MissingRepresentativePlots`.
    """
    plots = sorted(set(plots))
    young_cns = [cn for cn in plots if cn.startswith(YOUNG_STAND_PREFIX)]
    fia = [cn for cn in plots if not cn.startswith(YOUNG_STAND_PREFIX)]
    young = attrs.young.loc[[int(cn[len(YOUNG_STAND_PREFIX):]) for cn in young_cns]]
    neighbours = sorted(set(young["NEIGHBOUR_PLT_CN"]) if len(young) else set())
    con = sqlite3.connect(f"{extract.resolve().as_uri()}?mode=ro", uri=True)
    try:
        site = _plot_rows(con, FIA_STAND_INIT, "*", "STAND_CN", sorted(set(fia) | set(neighbours)))
        tree = _plot_rows(con, FIA_TREE_INIT, "*", "STAND_CN", fia)
    finally:
        con.close()
    repeated = sorted(set(site.loc[site["STAND_CN"].duplicated(), "STAND_CN"]))
    if repeated:
        raise AmbiguousStandInit(f"plots {repeated[:10]} have more than one {FIA_STAND_INIT} row "
                                 f"in {extract}")
    site = site.set_index("STAND_CN")
    missing = sorted((set(fia) - set(site.index)) | (set(fia) - set(tree["STAND_CN"])))
    if missing:
        raise MissingRepresentativePlots(
            f"representative plots {missing[:10]} ({len(missing)}) have no {FIA_STAND_INIT} or "
            f"{FIA_TREE_INIT} rows in {extract}: no unit may reach FVS without trees")
    no_site = sorted(set(neighbours) - set(site.index))
    if no_site:
        raise MissingRepresentativePlots(
            f"neighbour plots {no_site[:10]} of young plots have no {FIA_STAND_INIT} row in "
            f"{extract}: a young plot takes its neighbour's site")
    stand = site.loc[fia].reset_index()
    if len(young):
        stand = pd.concat([stand, _young_stands(site, young)], ignore_index=True)
        young_trees = _young_tree_rows(attrs.young_trees[attrs.young_trees["Value"].isin(young.index)])
        tree = pd.concat([tree, young_trees[[c for c in young_trees if c in tree.columns]]],
                         ignore_index=True)
    return PlotInventory(_anchored(stand), tree)


# ── step 9, LETO layout: each unit's own FVS stand ─────────────────────────────────────


def donor_trees(extract: Path, plots: pd.Series, attrs: Attributes) -> pd.DataFrame:
    """The donor plots' TREE rows (:data:`pipeline.leto_layout.TREE_FIELDS`): an FIA plot's from
    the extract, control numbers read as text; a young plot's own trees from ``young_plots/``."""
    fia = sorted(cn for cn in set(plots) if not cn.startswith(YOUNG_STAND_PREFIX))
    fields = ", ".join(f'"{f}"' for f in leto_layout.TREE_FIELDS if f not in ("CN", "PLT_CN"))
    con = sqlite3.connect(f"{extract.resolve().as_uri()}?mode=ro", uri=True)
    try:
        trees = _plot_rows(con, "TREE", f'CAST("CN" AS TEXT) AS CN, {fields}', "PLT_CN", fia)
    finally:
        con.close()
    trees["CN"] = as_id_series(trees["CN"], column="CN")
    young = leto_layout.young_donor_trees(attrs.young_trees, YOUNG_STAND_PREFIX)
    return pd.concat([trees[list(leto_layout.TREE_FIELDS)], young], ignore_index=True)


def unit_inventory(units: gpd.GeoDataFrame, weights: pd.DataFrame, ages: pd.Series,
                   donors: DonorPlots, attrs: Attributes, extract: Path,
                   species_lookup: Mapping[str, str], settings: LetoSettings
                   ) -> tuple[leto_layout.CountyInventory, gpd.GeoDataFrame]:
    """LETO stage 4 for the county's scheduled units; their ``stand_age`` is its ``AGE``.

    ``weights`` and ``ages`` are each LETO unit's, by ``unit_id``: a unit that took in speckle
    keeps its own, as it keeps its row.
    """
    inventory = leto_layout.build(scheduled(units), weights, ages,
                                  donor_trees(extract, donors.weights["PLT_CN"], attrs),
                                  species_lookup, settings.minimum_plot_weight)
    age = units["unit_id"].map(leto_layout.stand_ages(inventory))
    county = leto_layout.CountyInventory(inventory, str(units["county_fips"].iloc[0]),
                                         settings.database_mode, int(settings.state_fips))
    return county, units.assign(stand_age=age.map(usable_stand_age).astype(float))


# ── one county ─────────────────────────────────────────────────────────────────────────


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_parents(path: Path, seg: np.ndarray, cells: CountyCells) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(path, "w", driver="GTiff", height=seg.shape[0], width=seg.shape[1], count=1,
                       dtype="int32", crs=cells.crs, transform=cells.transform, nodata=0,
                       compress="deflate") as dst:
        dst.write(seg.astype(np.int32), 1)


@dataclass(frozen=True)
class RegionInputs:
    """A region's ``region_prep`` outputs, each with its sha256."""

    extract: Path
    hydrography: Path
    extract_sha256: str
    hydrography_sha256: str

    @classmethod
    def read(cls, region_dir: Path) -> RegionInputs:
        extract, hydrography = region_dir / FIA_EXTRACT_DB, region_dir / HYDROGRAPHY_GPKG
        for needed in (extract, hydrography):
            if not needed.exists():
                raise FileNotFoundError(f"{needed} is missing: run pipeline.s1_initial_state.region_prep")
        return cls(extract, hydrography, _sha256(extract), _sha256(hydrography))

    def record(self) -> dict:
        return {"fia_extract": {"path": str(self.extract.resolve()), "sha256": self.extract_sha256},
                "hydrography": {"path": str(self.hydrography.resolve()),
                                "sha256": self.hydrography_sha256}}


@dataclass(frozen=True)
class CountyJob:
    """Everything one county's run reads; ``input_sha256`` digests all of it."""

    county: County
    repair: Path
    out: Path                                 # <run_dir>/leto/<FIPS>
    vat: pd.DataFrame
    settings: LetoSettings
    region: RegionInputs
    input_sha256: str
    layout: InventoryLayout = InventoryLayout.PLOT
    species_lookup: Mapping[str, str] = dataclasses.field(default_factory=dict)  # LETO layout: FVS crosswalk
    memory_scope: leto_qa.MemoryScope = leto_qa.MemoryScope.PROCESS


@dataclass(frozen=True)
class CountyResult:
    fips: str
    accounting: dict
    units: int
    stands: int
    seconds: float


def run_county(job: CountyJob) -> CountyResult:
    """One county of the raster run into ``job.out``: its units, stands, FVS tables and accounting.

    Every input is checked before anything is written; ``job.out`` is replaced whole.
    """
    clock = leto_qa.Stopwatch()
    place, repair, settings = job.county, job.repair, job.settings
    extract = job.region.extract
    cells = clip_county(repair, place)
    clock.lap("clip")
    attrs = attributes(np.unique(cells.treemap[cells.valid]), job.vat, extract, repair / YOUNG_PLOTS)
    grids, _sources = la.attribute_rasters(cells.treemap, cells.valid, attrs.records)
    clock.lap("attributes")
    seg, deleted = segment(cells, grids, settings)
    if deleted.segments:
        log.info("leto: county %s: deleted %d undersized stands, %d forest cells (%.2f ac)",
                 place.fips, deleted.segments, deleted.cells, deleted.acres(cells.cell_acres))
    clock.lap("segment")
    parents = parent_polygons(seg, cells)
    traits, ages = parent_traits(seg, cells, grids, settings), parent_ages(seg, grids)
    plan = proximity_plan(seg, cells, parents, traits, ages, settings)
    clock.lap("polygons")
    pieces = riparian_split(parents, job.region.hydrography, settings.buffer_rules)
    clock.lap("riparian")
    segments = segment_layer(pieces, plan, place)
    chain, chain_summary = leto_proximity.chain_qa(segments, settings.proximity)
    donors = parent_donors(seg, cells, attrs, settings)
    weights = leto_proximity.unit_weights(donors.weights, segments)
    clock.lap("donors")
    speckle = merge_speckle(unit_layer(segments, leto_proximity.representative(weights), attrs),
                            leto_proximity.unit_traits(segments, traits), settings.speckle,
                            merge_cost(cells, settings), key="unit_id")
    units = speckle.units
    clock.lap("speckle")
    inventory: PlotInventory | leto_layout.CountyInventory
    if job.layout is InventoryLayout.LETO:
        inventory, units = unit_inventory(units, weights,
                                          leto_layout.unit_stdage_means(segments, ages), donors,
                                          attrs, extract, job.species_lookup, settings)
    else:
        inventory = plot_inventory(stand_rows(units)["PLT_CN"], attrs, extract)
    proximity = proximity_qa(place, settings, parents, plan, chain_summary, segments)
    segments = after_speckle(segments, speckle.receiver)
    stands = stand_rows(units, job.layout)
    clock.lap("inventory")
    accounting = area_accounting(cells, parents, units, place, deleted, speckle.deleted,
                                 len(segments))
    if abs(accounting["drift_acres"]) > MAX_DRIFT_ACRES:
        raise AcreDrift(f"county {place.fips}: units hold {accounting['unit_acres']} ac, parent "
                        f"stands {accounting['parent_stand_acres']} ac")
    layers = _hydrography(job.region.hydrography, settings.buffer_rules, parents.total_bounds)
    riparian_units = units[(units["unit_class"] == UnitClass.RIPARIAN) & (units["owner_class"] != MASKED)]
    riparian_sources = leto_qa.riparian_by_source(riparian_units, layers, settings.buffer_rules)
    clock.lap("qa_tables")

    shutil.rmtree(job.out, ignore_errors=True)
    _write_parents(job.out / PARENT_STANDS, seg, cells)
    donors.kept.to_csv(job.out / DONOR_WEIGHTS, index=False)
    units.to_file(job.out / COUNTY_UNITS, layer=UNITS_LAYER, driver="GPKG", mode="w")
    segments.to_file(job.out / COUNTY_UNITS, layer=SEGMENTS_LAYER, driver="GPKG", mode="w")
    stands.to_csv(job.out / STANDS, index=False)
    inventory.write(job.out / FVS_DATA)
    pd.DataFrame([accounting]).to_csv(job.out / AREA_ACCOUNTING, index=False)
    speckle.qa.to_csv(job.out / SPECKLE, index=False)
    pd.DataFrame([proximity]).to_csv(job.out / PROXIMITY, index=False)
    clock.lap("write")
    qa = job.out / leto_qa.QA_SUBDIR
    leto_qa.write_qa_cells(qa / leto_qa.QA_CELLS, _forest_type(cells, grids), cells.added_back,
                           cells.transform, cells.crs, {n: grids[n] for n in CONTINUOUS})
    leto_qa.write_county_qa(qa, fips=place.fips, name=place.name, units=units, weights=donors.kept,
                            riparian=riparian_sources, missing=missing_plots(cells, attrs, place),
                            parents=parent_table(seg, parents, cells, grids), parent_shapes=parents,
                            settings={k: v for k, v in settings.record().items() if k != "inputs"})
    clock.lap("maps")
    if plan is not None:
        plan.merges.to_csv(qa / PROXIMITY_MERGES, index=False)
    chain.insert(0, "county_fips", place.fips)
    chain.to_csv(qa / PROXIMITY_CHAIN, index=False)
    leto_qa.write_timings(qa / leto_qa.TIMINGS, place.fips, place.name, clock, job.memory_scope)
    return CountyResult(place.fips, accounting, len(units), len(stands), round(clock.total(), 1))


# ── many counties: the plan, resume, assembly ──────────────────────────────────────────


def _digest(payload: dict) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def _young_plots_sha256(repair: Path) -> str | None:
    young = repair / YOUNG_PLOTS
    if not young.is_dir():
        return None
    return _digest({str(f.relative_to(young)): _sha256(f)
                    for f in sorted(young.rglob("*")) if f.is_file()})


def _rasters_sha256(repair: Path) -> dict[str, str]:
    """The repaired rasters a county reads: the manifest records their paths, not their contents."""
    return {name: _sha256(repair / name) for name in REPAIRED_RASTERS}


def _vat_sha256(vat: pd.DataFrame) -> str:
    rows = pd.util.hash_pandas_object(vat.astype(str), index=False).to_numpy()
    return hashlib.sha256(rows.tobytes() + json.dumps(list(map(str, vat.columns))).encode()).hexdigest()


def county_outputs(layout: InventoryLayout) -> tuple[str, ...]:
    """The files a finished county holds."""
    if layout is InventoryLayout.LETO:
        return COUNTY_OUTPUTS + leto_layout.QA_TABLES
    return COUNTY_OUTPUTS


def input_sha256(county: County, settings: LetoSettings, region: RegionInputs, shared: dict,
                 layout: InventoryLayout = InventoryLayout.PLOT) -> str:
    """The digest of what a county's outputs depend on: a match means they can be kept.

    The county (FIPS, name, geometry), the raster run (its manifest, its repaired rasters and
    ``young_plots/``), the VAT, the region inputs, the resolved settings, the layout and
    :data:`OUTPUT_VERSION`; under the LETO layout the species crosswalk too (``shared``). The
    database mode only shapes the LETO layout's outputs, so the plot layout leaves it out.
    """
    leaves_out = {"inputs"} | ({"database"} if layout is InventoryLayout.PLOT else set())
    settings_record = {k: v for k, v in settings.record().items() if k not in leaves_out}
    return _digest({
        "output_version": OUTPUT_VERSION, "layout": layout.value,
        "county": {"fips": county.fips, "name": county.name,
                   "geometry": hashlib.sha256(county.geometry.wkb).hexdigest()},
        **shared, "settings": settings_record,
        "fia_extract": region.extract_sha256, "hydrography": region.hydrography_sha256})


class _Planner:
    """Builds each county's job, reading every region's inputs once."""

    def __init__(self, run_dir: Path, repair: Path, vat: pd.DataFrame, counties: Path,
                 region_dir: Path | None, settings: LetoSettings | None,
                 layout: InventoryLayout, species_lookup: Mapping[str, str] | None):
        self.run_dir, self.repair, self.vat, self.counties = run_dir, repair, vat, counties
        self.region_dir, self.settings = region_dir, settings
        self.layout, self.species_lookup = layout, species_lookup
        self.regions: dict[str, tuple[LetoSettings, RegionInputs]] = {}
        self.shared = {"repair_manifest": _sha256(repair / RUN_MANIFEST),
                       "rasters": _rasters_sha256(repair),
                       "young_plots": _young_plots_sha256(repair), "vat": _vat_sha256(vat)}
        if layout is InventoryLayout.LETO:
            if species_lookup is None:
                raise ValueError("the LETO layout needs the FVS species crosswalk (species_lookup)")
            self.shared["species_crosswalk"] = leto_layout.species_sha256(species_lookup)

    def region(self, fips: str) -> tuple[LetoSettings, RegionInputs]:
        settings = self.settings or load_settings(fips)
        if settings.region not in self.regions:
            inputs = RegionInputs.read(Path(self.region_dir or settings.inputs))
            self.regions[settings.region] = (settings, inputs)
        return self.regions[settings.region]

    def job(self, fips: str) -> CountyJob:
        settings, inputs = self.region(fips)
        place = read_county(self.counties, fips)
        return CountyJob(place, self.repair, self.run_dir / COUNTY_DIR / fips, self.vat, settings,
                         inputs, input_sha256(place, settings, inputs, self.shared, self.layout),
                         self.layout, self.species_lookup or {})


def _finished(entry: dict | None, job: CountyJob) -> bool:
    return (entry is not None and entry.get("status") == CountyStatus.DONE
            and entry.get("input_sha256") == job.input_sha256
            and all((job.out / name).exists() for name in county_outputs(job.layout)))


def _attempt(job: CountyJob) -> CountyResult | Exception:
    try:
        return run_county(job)
    except Exception as error:                # recorded, then re-raised once every county ran
        return error


def _execute(jobs: list[CountyJob], workers: int):
    """Yield ``(job, result or exception)`` as counties finish, ``workers`` at a time."""
    if workers <= 1 or len(jobs) <= 1:
        for job in jobs:
            yield job, _attempt(job)
        return
    # a fresh process per county, so its peak memory is its own
    with ProcessPoolExecutor(max_workers=min(workers, len(jobs)), max_tasks_per_child=1) as pool:
        futures = {pool.submit(_attempt, replace(job, memory_scope=leto_qa.MemoryScope.COUNTY)): job
                   for job in jobs}
        for future in as_completed(futures):
            try:
                result = future.result()
            except Exception as error:        # the worker died (BrokenProcessPool, e.g. out of memory)
                result = error
            yield futures[future], result


def _concat_csv(paths: list[Path], out: Path) -> None:
    """The counties' CSVs as one, byte for byte: control numbers never pass through a parser."""
    header, bodies = None, []
    for path in paths:
        first, _, body = path.read_text().partition("\n")
        if header is not None and first != header:
            raise ValueError(f"{path} has columns {first!r}, not {header!r}")
        header = first
        bodies.append(body)
    out.write_text(header + "\n" + "".join(bodies))


def _fvs_tables(path: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    tables = InventoryLayout.PLOT.tables
    with sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True) as con:
        frames = tuple(pd.read_sql(f'SELECT * FROM "{t}"', con) for t in (tables.stand, tables.tree))
    con.close()
    for frame in frames:
        frame["STAND_CN"] = as_id_series(frame["STAND_CN"], column="STAND_CN")
    return frames


def assemble_inventory(paths: list[Path]) -> PlotInventory:
    """One stand per distinct plot over the counties, its trees from the first county holding it.

    A plot in two counties is the same extract (or ``young_plots/``) rows; different StandInit
    rows for one plot are :class:`ConflictingPlotRows`.
    """
    stands, trees = zip(*(_fvs_tables(p) for p in paths))
    owner: dict[str, int] = {}
    for i, frame in enumerate(stands):
        for cn in frame["STAND_CN"]:
            owner.setdefault(cn, i)
    stand = pd.concat(stands, ignore_index=True).drop_duplicates(ignore_index=True)
    conflicting = sorted(set(stand.loc[stand["STAND_CN"].duplicated(), "STAND_CN"]))
    if conflicting:
        raise ConflictingPlotRows(f"plots {conflicting[:10]} have different StandInit rows in "
                                  "two counties")
    tree = pd.concat([t[t["STAND_CN"].map(owner) == i] for i, t in enumerate(trees)],
                     ignore_index=True)
    return PlotInventory(stand, tree)


def _remove_run_outputs(run_dir: Path) -> None:
    for name in (STANDS, FVS_DATA, UNITS_GPKG):
        (run_dir / name).unlink(missing_ok=True)
    leto_layout.remove_run_outputs(run_dir, run_dir / QA_DIR)
    shutil.rmtree(run_dir / QA_DIR, ignore_errors=True)


def assemble(run_dir: Path, counties: list[str],
             layout: InventoryLayout = InventoryLayout.PLOT) -> pd.DataFrame:
    """The run-level outputs from the finished ``counties``' own; return the area accounting."""
    dirs = [run_dir / COUNTY_DIR / fips for fips in counties]
    _concat_csv([d / STANDS for d in dirs], run_dir / STANDS)
    (run_dir / QA_DIR).mkdir(parents=True, exist_ok=True)
    _concat_csv([d / AREA_ACCOUNTING for d in dirs], run_dir / QA_DIR / AREA_ACCOUNTING)
    _concat_csv([d / SPECKLE for d in dirs], run_dir / QA_DIR / SPECKLE)
    _concat_csv([d / PROXIMITY for d in dirs], run_dir / QA_DIR / PROXIMITY)
    if layout is InventoryLayout.LETO:
        leto_layout.assemble(dirs, run_dir, run_dir / FVS_DATA, run_dir / QA_DIR)
    else:
        assemble_inventory([d / FVS_DATA for d in dirs]).write(run_dir / FVS_DATA)
    layers = {}
    for layer in (UNITS_LAYER, SEGMENTS_LAYER):
        frame = pd.concat([gpd.read_file(d / COUNTY_UNITS, layer=layer) for d in dirs],
                          ignore_index=True)
        layers[layer] = gpd.GeoDataFrame(frame, crs=frame.crs)
        layers[layer].to_file(run_dir / UNITS_GPKG, layer=layer, driver="GPKG", mode="w")
    units = layers[UNITS_LAYER]
    leto_qa.write_report(run_dir / QA_DIR, dict(zip(counties, dirs)), units)
    return pd.read_csv(run_dir / QA_DIR / AREA_ACCOUNTING, dtype={"county_fips": str})


# ── the stage ──────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class LetoRun:
    outcomes: dict[str, CountyOutcome]        # every finished county: ran, skipped or kept
    accounting: pd.DataFrame                  # every finished county's area-accounting line


def _write_record(run_dir: Path, record: dict) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    tmp = run_dir / f"{RECORD}.tmp"
    tmp.write_text(json.dumps(record, indent=2) + "\n")
    tmp.replace(run_dir / RECORD)


def _previous_counties(run_dir: Path) -> dict[str, dict]:
    path = run_dir / RECORD
    return json.loads(path.read_text()).get("counties", {}) if path.exists() else {}


def _restale(previous: dict[str, dict], requested: set[str], planner: _Planner) -> dict[str, dict]:
    """Earlier counties not asked for: kept if done under today's inputs, else marked stale."""
    kept = {}
    for fips, entry in sorted(previous.items()):
        if fips in requested:
            continue
        if entry.get("status") == CountyStatus.DONE:
            try:
                current = _finished(entry, planner.job(fips))
            except (ValueError, FileNotFoundError):   # its county, region or inputs are gone
                current = False
            if not current:
                entry = {**entry, "status": CountyStatus.STALE.value}
                log.warning("leto: county %s was finished under other inputs: left out", fips)
        kept[fips] = entry
    return kept


def run_leto(run_dir: Path, *, repair: Path, vat: pd.DataFrame, counties: Path,
             fips: Sequence[str] | None = None, region_dir: Path | None = None,
             settings: LetoSettings | None = None, workers: int = 1,
             layout: InventoryLayout = InventoryLayout.PLOT,
             species_lookup: Mapping[str, str] | None = None) -> LetoRun:
    """Run the counties ``fips`` of ``repair`` (default: every one its manifest lists) into ``run_dir``.

    ``vat`` is TreeMap's value table (:func:`add_back_ids.read_vat`) and ``counties`` the county
    layer. ``region_dir`` defaults to each region's configured inputs, ``settings`` to each
    county's region's. A county whose recorded input hash matches is skipped; the rest run,
    ``workers`` at a time. A county that fails is recorded as failed, the others still finish,
    and the first failure is raised with no run-level output written. The LETO ``layout`` needs
    ``species_lookup``, the FVS species crosswalk (FIA code -> SN species).
    """
    repair, run_dir = Path(repair), Path(run_dir)
    if not (repair / RUN_MANIFEST).exists():
        raise FileNotFoundError(f"{repair / RUN_MANIFEST} is missing: not a raster-repair run folder")
    requested = sorted(set(fips or manifest_counties(repair)))
    planner = _Planner(run_dir, repair, vat, counties, region_dir, settings, layout, species_lookup)
    jobs = [planner.job(f) for f in requested]          # every input checked before any work

    previous = _previous_counties(run_dir)
    entries = _restale(previous, set(requested), planner)
    outcomes = {f: CountyOutcome.KEPT for f, e in entries.items() if e.get("status") == CountyStatus.DONE}
    todo = []
    for job in jobs:
        county_fips = job.county.fips
        if _finished(previous.get(county_fips), job):
            outcomes[county_fips] = CountyOutcome.SKIPPED
            entries[county_fips] = previous[county_fips]
        else:
            todo.append(job)
    record = {
        "stage": "leto", "layout": layout.value,
        "repair": {"path": str(repair.resolve()), "young_plots": (repair / YOUNG_PLOTS).is_dir(),
                   "manifest_sha256": planner.shared["repair_manifest"]},
        "regions": {name: {"settings": s.record(), **inputs.record()}
                    for name, (s, inputs) in sorted(planner.regions.items())},
        "counties": entries,
    }
    _remove_run_outputs(run_dir)
    _write_record(run_dir, record)

    failures = []
    for job, result in _execute(todo, workers):
        base = {"name": job.county.name, "region": job.settings.region,
                "input_sha256": job.input_sha256}
        if isinstance(result, Exception):
            failures.append((job.county.fips, result))
            entries[job.county.fips] = {**base, "status": CountyStatus.FAILED.value,
                                        "error": f"{type(result).__name__}: {result}"}
        else:
            outcomes[job.county.fips] = CountyOutcome.RAN
            entries[job.county.fips] = {**base, "status": CountyStatus.DONE.value,
                                        "units": result.units, "stands": result.stands,
                                        "seconds": result.seconds}
        record["counties"] = dict(sorted(entries.items()))
        _write_record(run_dir, record)
    if failures:
        failures.sort(key=lambda f: f[0])
        log.error("leto: counties %s failed; see %s", [f for f, _ in failures], run_dir / RECORD)
        raise failures[0][1]

    done = [f for f, e in sorted(entries.items()) if e.get("status") == CountyStatus.DONE]
    return LetoRun(dict(sorted(outcomes.items())), assemble(run_dir, done, layout))
