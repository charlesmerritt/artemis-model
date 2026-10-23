"""Maine v4-improved TreeMap -> LETO CA stands, county by county: shared paths and the grid.

The experiment's record lives in this docstring (``scripts/check_docs.py`` admits no new .md).

Task from the user (2026-10-19): take the latest methodology from every remote branch and build
LETO CA stands for all of Maine on a v4-improved raster, county by county if memory needs it.
The goal is the Maine stand layer on v4.

## Branch

``experiment/maine-v4-leto`` (worktree ``artemis-model-maine``) = main merged with
young-plot-library (holds add-back-stand-age), experiment/raster-set-schedules,
experiment/statewide-v1-v4, claude/gifted-ritchie-5zo1om and deck/fl5-open-menus, plus the
uncommitted LETO v1 work from the main checkout (``pipeline/leto_*.py``, CA, riparian, donors).

## v4 for Maine

No v4 raster existed for Maine (R2 gate-v4 is FL ``aoi_5county`` only). The FL5 v4 recipe
(reconstructed in ``experiments/2026-10-12_statewide-v1-v4/common.py``) is:

- rule ``bookends_or_obata_and_hansen`` (now :class:`ConsensusRule`);
- disturbance window 2012-2022 (Obata years, Hansen lossyear 12-22);
- EVT gate v3;
- the quarry patch rule (now :class:`evt_gate.PatchRule.QUARRY_TOUCH_V4`).

Obata covers only Landsat p17/r39 in Florida, so it is pending in Maine. That leaves
``obata & hansen`` empty, and Maine v4 is bookends only, the same decision the FL statewide
handoff took outside p17/r39. Hansen still runs: its bits are recorded, and it dates the
add-back pixels (ADR 0003).

Caveats a reader must carry:

- The AlphaEarth S3/S4 model is refit on Maine (``work/model/hole_model.json``): the FL model
  (FL5 clearcut exemplars) passes no Maine pixel. The FL-model run is kept as ``*_fl_model``.
- EVT gate v3's pasture block is 7997, *Eastern Warm Temperate* Pasture and Hayland. Maine's
  pasture classes are not on that list, so in Maine v3 blocks only crops and developed land.
- Young plots come from FIA plots in LETO Maine's states (ME, MA, NH, NY), not FL/GA/AL.

## Findings (2026-10-19 to 10-23)

- **The FL hole model does not transfer.** Cosine similarity of Maine S3/S4 to the FL clearcut
  exemplars peaks at ~0.69 against the 0.907 Stage A threshold, so it passes nothing. The
  Maine refit (``classify_holes`` on Maine strata after ``embed_holes`` stopped sampling the FL
  AOI): 4,500 points in 185 blocks, Stage A threshold 0.887, block-CV AUC 0.996 (shuffled
  labels 0.48), end-to-end balanced accuracy 0.943, precision 0.991.
- **v4 add-back (Maine model): 321,483 ac in 18,522 patches** (FL model: 254,669 ac). By
  stratum: S1 228,440, S2 45,465, S3 29,264, S4 18,306 ac. The rule accepted 652,752 ac
  before the MMU; the MMU or no donor dropped 331,270. The EVT gate rejected 81,476 ac, the
  quarry rule 635 ac.
- **Young plots are thin.** 58% of add-back acres (185,249 ac) have no young plot and keep
  their neighbour plot (``UnmatchedYoungPlotPolicy.KEEP_NEIGHBOUR``): they carry a young
  dated age with mature BA/QMD/TPA. Matched: 83,601 ac by type, 52,633 ac by group (the group
  map is southern: white pine and hemlock fall in ``other``).
- **Ownership speckle, cleaned after LETO.** Harris is 10 m; warped to 30 m nearest it leaves
  single-pixel owners, and LETO's force_merge needs the same owner, so LETO leaves 709,216
  parents (FL-model run: 473,609 under 5 ac). ``pipeline.stand_speckle`` then absorbs every
  parent under 5 ac that touches another stand into its longest-shared-edge neighbour:
  318,880 stands (201,794 ac) absorbed, 390,336 parents left, median 9.8 ac. LETO's labels stay
  in ``<fips>_seg_leto.tif``. 145,678 isolated forest islands under 5 ac remain (87,393 ac,
  0.5% of forest).
- **Stand layer:** 1,035,735 management units (483,999 riparian, 336,220 ac) over 17.05M ac;
  unknown owner 5.97M ac (32% of parents).

## Inputs

On this laptop ``/mnt/d`` is absent, so R2 ``artemis-r2/data`` was mirrored at ``MNT_D``
(set ``ARTEMIS_MNT_D=/mnt/d`` on the work machine). Public downloads R2 lacks, under
``DOWNLOADS``: Hansen GFC-2025-v1.13 tiles 50N_080W and 50N_070W, and NHDPlus HR beta HU4
GDBs 0101-0106 (LETO v1 Maine reads NHDPlus HR National Release 2: ``NetworkNHDFlowline``,
``NHDArea``, ``NHDWaterbody``; the HU4 GDBs have ``NHDFlowline`` in place of the network
subset, and only FCodes 460xx carry buffers).

## Steps

1. ``01_grid_inputs.py``: Maine grid, hole strata, Harris ownership and Hansen warped onto it.
2. Refit the hole model on Maine, then AlphaEarth S3/S4 scores over the Maine grid (Earth Engine, 72 tiles, ~90 min):
   ``uv run python -m pipeline.s1_initial_state.score_holes_statewide --model-json <HOLE_MODEL>
   --strata-tif work/inputs/treemap_strata_me.tif --out-tif work/inputs/hole_prob_similarity_me.tif``
3. ``03_improve.py``: county_improvement per county with the v4 recipe, stitch, stand age, young plots.
4. ``04_fia.py``: COND/TREE rows for every plot the improved raster references.
5. ``05_leto.py``: LETO stages 1-3 per county (CA, speckle cleanup, riparian split, parent
   donor weights).
6. ``06_merge.py``: the statewide stand layer.
7. ``07_deck.py``: the maps and ``docs/maine-v4-leto/presentation.html``.
"""

from __future__ import annotations

import json
import math
import os
import resource
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

EXP = Path(__file__).resolve().parent
REPO = EXP.parents[1]
WORK = EXP / "work"
INPUTS = WORK / "inputs"
OUTPUTS = EXP / "outputs"

DATA = Path(os.environ.get("ARTEMIS_DATA", "/home/chazm/Projects/work/artemis-data"))
MNT_D = Path(os.environ.get("ARTEMIS_MNT_D", DATA / "mnt_d"))
DOWNLOADS = DATA / "downloads"

TREEMAP_TIF = MNT_D / "TreeMap-2022/Data/TreeMap2022_CONUS.tif"
TREE_TABLE = MNT_D / "TreeMap-2022/Data/TreeMap2022_CONUS_Tree_Table.csv"
LANDFIRE = MNT_D / "landfire"
COUNTIES_SHP = MNT_D / "county_p010g.shp_nt00934/countyp010g.shp"
OWNERSHIP_CONUS = MNT_D / "RDS-2025-0045/Data/US_forest_ownership.tif"
# Refit on Maine (classify_holes over Maine strata); the FL model passes no Maine pixel.
HOLE_MODEL = WORK / "model/hole_model.json"
FIADB = MNT_D / "SQLite_FIADB_ENTIRE/SQLite_FIADB_ENTIRE.db"
HANSEN_DIR = DOWNLOADS / "hansen"
HANSEN_TILES = ("50N_080W", "50N_070W")
NHD_DIR = DOWNLOADS / "nhdplus_hr"
NHD_HU4 = ("0101", "0102", "0103", "0104", "0105", "0106")

STRATA_TIF = INPUTS / "treemap_strata_me.tif"
OWNERSHIP_TIF = INPUTS / "us_forest_ownership_me.tif"
HANSEN_VRT = INPUTS / "hansen_me.vrt"
SCORED_TIF = INPUTS / "hole_prob_similarity_me.tif"
HYDRO_GPKG = INPUTS / "hydro_me_5070.gpkg"
RUN_ROOT = WORK / "rasters/v4"
RUN_NAME = "maine"
RUN_DIR = RUN_ROOT / RUN_NAME

PIXEL = 30.0
STATE_FIPS = "23"
FIA_STATES = (23, 25, 33, 36)   # LETO v1 Maine's FIA files: ME, MA, NH, NY
V4_OBATA_YEARS = (2012, 2022)
V4_HANSEN_LOSSYEAR = (12, 22)


def maine_counties():
    """Maine's 16 counties (EPSG:5070), one row each, sorted by FIPS."""
    import geopandas as gpd

    c = gpd.read_file(COUNTIES_SHP, where=f"STATE_FIPS = '{STATE_FIPS}'").to_crs(5070)
    c = c.dissolve("ADMIN_FIPS", as_index=False, aggfunc="first")
    return c[["ADMIN_FIPS", "NAME", "geometry"]].sort_values("ADMIN_FIPS").reset_index(drop=True)


@dataclass(frozen=True)
class Grid:
    west: float
    north: float
    width: int
    height: int

    @property
    def transform(self):
        from rasterio.transform import Affine
        return Affine(PIXEL, 0, self.west, 0, -PIXEL, self.north)

    @property
    def bounds(self) -> tuple[float, float, float, float]:
        return (self.west, self.north - self.height * PIXEL, self.west + self.width * PIXEL,
                self.north)


def maine_grid(pad_px: int = 128) -> Grid:
    """Maine's bounding box snapped to TreeMap 2022's lattice, padded by ``pad_px``."""
    import rasterio

    with rasterio.open(TREEMAP_TIF) as src:
        t = src.transform
    minx, miny, maxx, maxy = maine_counties().total_bounds
    c0 = math.floor((minx - t.c) / PIXEL) - pad_px
    c1 = math.ceil((maxx - t.c) / PIXEL) + pad_px
    r0 = math.floor((t.f - maxy) / PIXEL) - pad_px
    r1 = math.ceil((t.f - miny) / PIXEL) + pad_px
    return Grid(t.c + c0 * PIXEL, t.f - r0 * PIXEL, c1 - c0, r1 - r0)


def improvement_inputs():
    """county_improvement's inputs for Maine: Obata pending, Hansen on the Maine grid."""
    from pipeline.s1_initial_state import county_improvement as ci

    return ci.Inputs(treemap=TREEMAP_TIF, tree_table=TREE_TABLE, landfire=LANDFIRE,
                     scored=SCORED_TIF, model=HOLE_MODEL, ownership=OWNERSHIP_TIF,
                     counties=COUNTIES_SHP, obata=None, hansen=HANSEN_VRT)


@contextmanager
def measured(label: str, record: dict):
    """Wall time and the process's peak RSS (GB) for a step, written into ``record``."""
    t = time.perf_counter()
    yield
    record[label] = {"seconds": round(time.perf_counter() - t, 1),
                     "peak_rss_gb": round(peak_rss_gb(), 3)}


def peak_rss_gb() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024 ** 2


def write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, default=float))
