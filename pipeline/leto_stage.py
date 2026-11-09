"""The ``leto`` stage: one county of a raster-repair run to management units and ``stands.csv``.

    uv run python main.py leto <run_dir> --repair <raster run> --county <FIPS> [--region-dir <dir>]

It composes the LETO v1 modules (ADR 0004) under the county's regional settings
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
3. **The cellular automata, then the small-stand action** (:func:`pipeline.leto_ca.segment`).
   Owner classes are hard edges: the repaired Harris raster, its nodata read as unknown (0),
   as LETO does. Harris 1-2 stay their own classes, so they can be masked (LETO folds them into 0).
4. **Polygons**, one per parent stand.
5. **The vector riparian split** (:mod:`pipeline.leto_riparian`) on the region's hydrography.
6. **Parent-stand donor weights** (:mod:`pipeline.leto_donors`): donors under the minimum
   weight are dropped, but each parent keeps its largest.
7. **The representative plot**: each parent's largest donor plot, ties to the lower TreeMap
   value. Every unit of a parent runs on it.
8. **The plot layout's FVS tables** (:func:`plot_inventory`), one stand per distinct
   representative plot of a scheduled unit. An FIA plot is its extract rows, anchored to 2022
   as the FL5 golden run was. A young plot is its neighbour plot's StandInit row (the site)
   at its dated 2022 age, with its own live trees under LETO's sampling design (BAF -1, plot
   size 1, breakpoint 0, one plot, ``TREE_COUNT`` = TPA). A representative plot without
   StandInit or tree rows in the extract, or a young plot whose neighbour has no StandInit
   row, is :class:`MissingRepresentativePlots`; one with two StandInit rows is
   :class:`AmbiguousStandInit`; a young-plot ID without a live tree is
   :class:`UnknownYoungPlotIds`.

Writes into ``<run_dir>``:

- ``stands.csv``: the runner's columns (:data:`pipeline.run.STAND_COLUMNS`) for every unit not
  masked, plus ``county_fips`` (5-digit string), ``unknown_ownership``, ``origin``
  (:class:`Origin`), ``tm_id`` (the representative plot's TreeMap value) and ``parent_stand``.
  ``PLT_CN`` is the representative plot's control number, exact, or ``Y<ID>`` for a young plot.
  ``stand_age`` is a young plot's dated 2022 age, else the representative plot's FVS StandInit
  ``AGE`` (what FVS starts from), else blank: an age :func:`usable_stand_age` rejects is left
  to the harvest-eligibility policy, never guessed. Harris 0 is owner class ``unknown`` (the Private owner group) with
  ``unknown_ownership`` set; Harris 1-2 are masked: in the units layer, not in ``stands.csv``.
- ``FVS_Data.db``: the plot layout's ``FVS_StandInit_Plot`` and ``FVS_TreeInit_Plot``
  (:data:`pipeline.run.InventoryLayout.PLOT`), keyed by the exact ``STAND_CN``: a ``PLT_CN``
  or ``Y<ID>``.
- ``management_units.gpkg``, layer ``units``: every unit, masked ones too, with its ``Acres``
  (geodesic, as LETO), ``owner_class`` and ``unit_class``.
- ``leto/<FIPS>/parent_stands.tif`` and ``donor_weights.csv``: the county's parent stands and
  their filtered donor weights.
- ``qa/leto/area_accounting.csv``: per county, forest cells, parent stands and units, the unit
  acres split into managed, unknown-flagged (managed, Harris 0), riparian and masked, the drift,
  the scheduled acres with no usable stand age, and the forest cells the Harris raster has no
  value for (read as unknown). A drift over
  :data:`MAX_DRIFT_ACRES` fails the county (:class:`AcreDrift`) before anything is written.
- ``leto.json``: the raster run's ``manifest.json`` sha256, the FIA extract and hydrography
  (path and sha256), the resolved settings, and the county's status.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import sqlite3
import time
from dataclasses import dataclass
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
from pipeline import leto_ca, leto_donors, leto_riparian
from pipeline.leto_database import SamplingDesign
from pipeline.harvest_eligibility import usable_stand_age
from pipeline.ids import as_id_series, read_id_csv
from pipeline.leto_riparian import SQUARE_METERS_PER_ACRE, BufferRules
from pipeline.run import FVS_DATA, STAND_COLUMNS, STANDS, InventoryLayout
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
    RUN_MANIFEST,
    UnknownTreeMapValues,
)
from pipeline.s1_initial_state.young_stand_profiles import BA_FACTOR
from pipeline.s3_management.owner_classes import MASKED, classify_owner
from pipeline.s3_management.regime_assignment import forest_type_branch
from pipeline.s4_fvs.regime_templates import DEFAULT_INV_YEAR
from pipeline.spatial_ref import to_project_crs

REPO = Path(__file__).resolve().parents[1]
CONFIG_PATH = REPO / "config" / "leto.yaml"
OWNERSHIP = "nwos2022_improved.tif"          # the repaired Harris raster
UNITS_GPKG = "management_units.gpkg"
UNITS_LAYER = "units"
RECORD = "leto.json"
COUNTY_DIR = "leto"
PARENT_STANDS = "parent_stands.tif"
DONOR_WEIGHTS = "donor_weights.csv"
QA_DIR = Path("qa") / "leto"
AREA_ACCOUNTING = "area_accounting.csv"
LAYOUT = InventoryLayout.PLOT                 # the plot layout (ADR 0004)
INV_YEAR = DEFAULT_INV_YEAR                   # every FVS stand starts in TreeMap's year, 2022
VARIANT = "SN"
FIA_STAND_INIT, FIA_TREE_INIT = "FVS_STANDINIT_PLOT", "FVS_TREEINIT_PLOT"   # in the extract
YOUNG_DESIGN = SamplingDesign()               # LETO's per-acre design: BAF -1, plot size 1, ...

CONTINUOUS = ("STDAGE", "BALIVE", "QMD", "TPA")
YOUNG_STAND_PREFIX = "Y"
UNKNOWN_OWNER = 0                             # Harris unknown forest
OUTSIDE = -1                                  # the CA's ownership sentinel off the forest
MAX_DRIFT_ACRES = 0.1                         # unit acres must equal parent-stand acres this closely


class Origin(StrEnum):
    """Where a unit's representative plot comes from."""

    TREEMAP = "treemap"
    YOUNG_PLOT = "young_plot"                 # an add-back young plot (ADR 0003)


class UnitClass(StrEnum):
    MANAGED = "managed"
    RIPARIAN = "riparian"                     # grow only


class CountyStatus(StrEnum):
    DONE = "done"


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


class UnknownCounty(ValueError):
    """A FIPS that names no county in the county layer."""


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

    def record(self) -> dict:
        """The settings as JSON, for ``leto.json``."""
        rules = {layer: {str(code): [label, feet] for code, (label, feet) in rules.items()}
                 for layer, rules in vars(self.buffer_rules).items()}
        return {"region": self.region, "state_fips": self.state_fips, "inputs": str(self.inputs),
                "segmentation": {k: str(v) if isinstance(v, StrEnum) else v
                                 for k, v in self.segmentation.items()},
                "riparian": rules, "minimum_plot_weight": self.minimum_plot_weight}


def load_settings(county_fips: str, path: Path = CONFIG_PATH) -> LetoSettings:
    """The settings of the region whose ``state_fips`` the county's FIPS starts with."""
    config = yaml.safe_load(Path(path).read_text())
    state = county_fips[:2]
    for name, spec in config["regions"].items():
        if spec["state_fips"] == state:
            return _settings(name, spec)
    raise ValueError(f"no region in {path} for state FIPS {state!r} (county {county_fips})")


def _settings(name: str, spec: dict) -> LetoSettings:
    action = leto_ca.SmallStandAction(spec["small_stands"]["remaining_small_stand_action"])
    rules = BufferRules(**{layer: {int(code): (str(label), float(feet))
                                   for code, (label, feet) in spec["riparian"][layer].items()}
                           for layer in ("flowline", "waterbody", "area")})
    return LetoSettings(
        region=name, state_fips=spec["state_fips"], inputs=REPO / spec["inputs"],
        segmentation={**leto_ca.DEFAULT_CFG, **spec["segmentation"],
                      "remaining_small_stand_action": action},
        buffer_rules=rules, minimum_plot_weight=float(spec["inventory"]["minimum_plot_weight"]))


# ── step 1: the county's cells ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class County:
    fips: str
    name: str
    geometry: object                          # shapely, project CRS


def read_county(counties: Path, fips: str) -> County:
    """The county with ``ADMIN_FIPS == fips`` from the national county layer, in the project CRS."""
    if not (len(fips) == 5 and fips.isdigit()):
        raise UnknownCounty(f"a county FIPS is 5 digits, not {fips!r}")
    rows = gpd.read_file(counties, where=f"ADMIN_FIPS = '{fips}'")
    if rows.empty:
        raise UnknownCounty(f"county {fips} is not in {counties}")
    rows = to_project_crs(rows)
    return County(fips, str(rows["NAME"].iloc[0]), rows.geometry.union_all())


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
    return Attributes(records, young, start_age, young_trees)


# ── steps 3-7: parents, units, donors ──────────────────────────────────────────────────


def segment(cells: CountyCells, grids: dict[str, np.ndarray], settings: LetoSettings) -> np.ndarray:
    """LETO's CA and small-stand action, owner classes as hard edges; int32 parent labels."""
    forest_type = np.where(cells.valid, np.nan_to_num(grids["FORTYPCD"], nan=0), 0).astype(np.int32)
    ownership = np.where(cells.valid, cells.harris, OUTSIDE).astype(np.int16)
    return leto_ca.segment({n: grids[n] for n in CONTINUOUS}, forest_type, ownership, cells.valid,
                           cells.cell_acres, cfg=settings.segmentation,
                           similarity_raw={"STDAGE": np.nan_to_num(grids["STDAGE"], nan=0.0)},
                           log=logging.getLogger("artemis.leto").debug)


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


def representative_plots(seg: np.ndarray, cells: CountyCells, attrs: Attributes,
                         settings: LetoSettings) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Filtered donor weights per parent, and each parent's representative plot (indexed by SEG_ID)."""
    value_to_plt = {int(v): attrs.stand_cn(int(v)) for v in attrs.records.index}
    weights = leto_donors.parent_weights(seg, cells.treemap, value_to_plt)
    kept = leto_donors.filter_weights(weights, settings.minimum_plot_weight, key="SEG_ID")
    top = (kept.sort_values(["SEG_ID", "WEIGHT", "TREEMAP_VALUE"], ascending=[True, False, True])
           .drop_duplicates("SEG_ID").set_index("SEG_ID"))
    return kept, top[["TREEMAP_VALUE", "PLT_CN", "WEIGHT"]]


def unit_layer(pieces: gpd.GeoDataFrame, top: pd.DataFrame, attrs: Attributes,
               county: County) -> gpd.GeoDataFrame:
    """Every piece as a unit: its ID, owner, class and representative plot."""
    units = pieces.sort_values(["SEG_ID", "RIPARIAN"], kind="stable").reset_index(drop=True)
    units.insert(0, "unit_id", [f"{county.fips}-{i}" for i in range(1, len(units) + 1)])
    units.insert(1, "county_fips", county.fips)
    units.insert(2, "county", county.name)
    owners = {code: classify_owner({"OWN_CODE": int(code)}) for code in units["OWN_CODE"].unique()}
    units["owner_class"] = units["OWN_CODE"].map(lambda c: owners[c].owner_class)
    units["unknown_ownership"] = units["OWN_CODE"].map(lambda c: owners[c].has("unknown_ownership"))
    units["unit_class"] = np.where(units["RIPARIAN"] == leto_riparian.RIPARIAN,
                                   UnitClass.RIPARIAN.value, UnitClass.MANAGED.value)
    rep = units["SEG_ID"].map(top["TREEMAP_VALUE"]).astype(np.int64)
    young = rep >= NEW_ID_START
    units["tm_id"] = rep
    units["origin"] = np.where(young, Origin.YOUNG_PLOT.value, Origin.TREEMAP.value)
    units["PLT_CN"] = units["SEG_ID"].map(top["PLT_CN"])
    units["FORTYPCD"] = rep.map(attrs.records["FORTYPCD"])
    dated = rep.map(attrs.young["STAND_AGE_2022"]) if len(attrs.young) else np.nan
    age = pd.Series(np.where(young, dated, units["PLT_CN"].map(attrs.start_age)), index=units.index)
    units["stand_age"] = age.map(usable_stand_age).astype(float)   # unusable: blank
    return units


def stand_rows(units: gpd.GeoDataFrame) -> pd.DataFrame:
    """The units ``stands.csv`` schedules: every one not masked."""
    stands = units[units["owner_class"] != MASKED].drop(columns="geometry")
    stands = stands.assign(acres=stands["Acres"], parent_stand=stands["SEG_ID"],
                           forest_branch=[forest_type_branch({"OWN_CODE": o, "FORTYPCD": f})
                                          for o, f in zip(stands["OWN_CODE"], stands["FORTYPCD"])])
    extra = ["county_fips", "unknown_ownership", "origin", "tm_id", "parent_stand"]
    return pd.DataFrame(stands[list(STAND_COLUMNS) + extra])


def area_accounting(cells: CountyCells, parents: gpd.GeoDataFrame, units: gpd.GeoDataFrame,
                    county: County) -> dict:
    """One county's line: forest cells → parent stands → units, the unit acres split four ways."""
    masked = units["owner_class"] == MASKED
    riparian = units["unit_class"] == UnitClass.RIPARIAN
    unknown = units["unknown_ownership"].astype(bool)

    def acres(mask) -> float:
        return round(float(units.loc[mask, "Acres"].sum()), 4)

    forest_cells = int(cells.valid.sum())
    parent_acres, unit_acres = float(parents["Acres"].sum()), float(units["Acres"].sum())
    return {"county_fips": county.fips, "county": county.name, "forest_cells": forest_cells,
            "forest_cell_acres": round(forest_cells * cells.cell_acres, 4),
            "parent_stands": len(parents), "parent_stand_acres": round(parent_acres, 4),
            "units": len(units), "unit_acres": round(unit_acres, 4),
            "managed_acres": acres(~masked & ~riparian & ~unknown),
            "unknown_flagged_acres": acres(~masked & ~riparian & unknown),
            "riparian_acres": acres(~masked & riparian),
            "masked_acres": acres(masked),
            "no_age_acres": acres(~masked & units["stand_age"].isna()),   # harvest eligibility decides
            "add_back_cells": int(cells.added_back.sum()),
            "harris_nodata_cells": int(cells.harris_nodata.sum()),   # read as unknown, as LETO does
            "drift_acres": round(unit_acres - parent_acres, 4)}


# ── step 8: the plot layout's FVS tables ───────────────────────────────────────────────


@dataclass(frozen=True)
class PlotInventory:
    """``FVS_Data.db``'s plot-layout tables: one stand per distinct representative plot."""

    stand: pd.DataFrame
    tree: pd.DataFrame

    def write(self, path: Path) -> None:
        tables = LAYOUT.tables
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


# ── the stage ──────────────────────────────────────────────────────────────────────────


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


def run_leto(run_dir: Path, *, repair: Path, county: str, vat: pd.DataFrame, counties: Path,
             region_dir: Path | None = None, settings: LetoSettings | None = None) -> dict:
    """Run one county of ``repair`` into ``run_dir``; return its area-accounting line.

    ``vat`` is TreeMap's value table (:func:`add_back_ids.read_vat`). ``region_dir`` defaults
    to the region's configured inputs. Every input is checked before anything is written.
    """
    started = time.perf_counter()
    settings = settings or load_settings(county)
    region_dir = Path(region_dir or settings.inputs)
    repair, run_dir = Path(repair), Path(run_dir)
    extract, hydrography = region_dir / FIA_EXTRACT_DB, region_dir / HYDROGRAPHY_GPKG
    for needed in (extract, hydrography):
        if not needed.exists():
            raise FileNotFoundError(f"{needed} is missing: run pipeline.s1_initial_state.region_prep")

    manifest = repair / RUN_MANIFEST
    if not manifest.exists():
        raise FileNotFoundError(f"{manifest} is missing: not a raster-repair run folder")
    place = read_county(counties, county)
    cells = clip_county(repair, place)
    attrs = attributes(np.unique(cells.treemap[cells.valid]), vat, extract, repair / YOUNG_PLOTS)
    grids, _sources = la.attribute_rasters(cells.treemap, cells.valid, attrs.records)
    seg = segment(cells, grids, settings)
    parents = parent_polygons(seg, cells)
    pieces = riparian_split(parents, hydrography, settings.buffer_rules)
    weights, top = representative_plots(seg, cells, attrs, settings)
    units = unit_layer(pieces, top, attrs, place)
    stands = stand_rows(units)
    inventory = plot_inventory(stands["PLT_CN"], attrs, extract)
    accounting = area_accounting(cells, parents, units, place)
    if abs(accounting["drift_acres"]) > MAX_DRIFT_ACRES:
        raise AcreDrift(f"county {place.fips}: units hold {accounting['unit_acres']} ac, parent "
                        f"stands {accounting['parent_stand_acres']} ac")

    county_dir = run_dir / COUNTY_DIR / place.fips
    _write_parents(county_dir / PARENT_STANDS, seg, cells)
    weights.to_csv(county_dir / DONOR_WEIGHTS, index=False)
    units.to_file(run_dir / UNITS_GPKG, layer=UNITS_LAYER, driver="GPKG", mode="w")
    stands.to_csv(run_dir / STANDS, index=False)
    inventory.write(run_dir / FVS_DATA)
    (run_dir / QA_DIR).mkdir(parents=True, exist_ok=True)
    pd.DataFrame([accounting]).to_csv(run_dir / QA_DIR / AREA_ACCOUNTING, index=False)
    record = {
        "stage": "leto", "layout": LAYOUT.value,
        "repair": {"path": str(repair.resolve()), "young_plots": (repair / YOUNG_PLOTS).is_dir(),
                   "manifest_sha256": _sha256(manifest)},
        "fia_extract": {"path": str(extract.resolve()), "sha256": _sha256(extract)},
        "hydrography": {"path": str(hydrography.resolve()), "sha256": _sha256(hydrography)},
        "settings": settings.record(),
        "counties": {place.fips: {"name": place.name, "status": CountyStatus.DONE.value,
                                  "units": len(units), "stands": len(stands),
                                  "seconds": round(time.perf_counter() - started, 1)}},
    }
    (run_dir / RECORD).write_text(json.dumps(record, indent=2) + "\n")
    return accounting
