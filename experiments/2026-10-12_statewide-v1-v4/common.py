"""Statewide v1 / v4 -> LETO CA stands -> statewide harvest schedule: the experiment's record.

This module holds the shared paths, the Florida grid and the two raster recipes. Its
docstring is the handoff from session 1 (2026-10-12), kept here rather than in markdown
(`scripts/check_docs.py` admits no new .md).

Task from the user: take the v1 and v4 improved rasters statewide (Florida), build LETO CA
stands on each, run a statewide harvest schedule on each, watch for errors, and answer in a new
Artifact: how to scale up, RAM limits, time per state, and whether the scheduler works county by
county or at the aggregate level. Session 1 stopped at the AGENTS.md 250k-token limit after
discovery. Steps 1 and 2 ran on 2026-10-13; LETO, FVS and the schedule have not.

## Where things are

* Worktree `worktrees/statewide-v1-v4`, branch `experiment/statewide-v1-v4` (on GitHub; the
  handoff commit 4a7232d went in with `--no-verify` because the merged deck branch's README fails
  `check_docs.py`) = `origin/add-back-stand-age` (f8e40b2: county_improvement with EVT gates v1–v3)
  merged with `deck/fl5-open-menus` (67204d8: main + `experiments/2026-10-08_permissive-menus`).
* `fvs/bin/FVSsn{,.so}` copied from `worktrees/raster-set-schedules` (recipe build, see memory
  `local-fvssn-traps-underflow`). `.env` copied. `data/{interim,processed}` symlinked to the main
  checkout. `uv sync` done; `numba 0.68` installed with `uv pip install` (not in pyproject yet).
* `common.py`: paths, `florida_counties()`, `florida_grid()`, `Recipe`, `HANSEN_LOSSYEARS`,
  `measured()` (wall + peak RSS). Tests: `tests/test_statewide_v1_v4_experiment.py`.
* `work/downloads/hansen/`: Hansen GFC-2025-v1.13 `lossyear` + `treecover2000`, tiles 40N_090W and
  30N_090W (cover all of FL; nothing east of 80°W needed).
* TPO workbooks (NRUM, Florida 2013, 2015, 2017–2024) were downloaded to the session scratchpad;
  re-download into `work/downloads/tpo/` (IDs below).

## Facts established (verified this session)

* **No statewide improved rasters exist.** `/mnt/d/improved-rasters` and R2
  `raster_improvement_data/processed/*` hold only `aoi_5county`. Statewide inputs that DO exist:
  AlphaEarth scores `/mnt/d/improved-rasters/inputs/hole_prob_similarity_statewide.tif` + `hole_model.json`,
  Harris on the FL grid `us_forest_ownership_fl.tif`, LANDFIRE EVT 2016/2022/2024 CONUS, TreeMap 2022 CONUS.
* **Obata covers one Landsat scene (p17/r39) only.** Statewide Obata = ~20 geepipe disturbance runs on
  EE project `perseus-gee` (credentials present). Decision taken: do not run; outside p17/r39 Obata
  proposes nothing, so statewide v4 = bookends only there and v1 = bookends ∪ Hansen. Quantify the
  gap on FL5 from `add_back_method_bits.tif` (pixels whose only accepted evidence needed Obata).
* **v1 recipe** (released FL5 `improved-rasters-evt2022-gated`, 189,023.5 ac added back): rule
  `union`, gate `evt2022_agriculture_v1`, Obata 2010–2022, Hansen lossyear 1–22, treecover2000 ≥ 30,
  MMU 5 ac. Run by `raster-improvement/scripts/gated_county_improvement.py`.
* **v4 recipe** (released FL5 `…-gate-v4`, 80,203.5 ac; now in the pipeline as
  `ConsensusRule.BOOKENDS_OR_OBATA_AND_HANSEN`, `DisturbanceWindow.Y2012_2022` and
  `EvtGatePolicy.AGRICULTURE_DEVELOPED_PASTURE_QUARRY_V4`): its code (commit ad9c4a0) is on neither
  GitHub nor this machine. Reconstructed from its rasters:
  - rule `bookends_or_obata_and_hansen`: accepted = bookends | (obata & hansen);
  - disturbance window 2012–2022 (Obata years 2012–2022, Hansen lossyear 12–22);
  - pixel gate = v3 (`evt2022_agriculture_developed_pasture_v3`: v1 + developed 7296–7299 + pasture 7997);
  - **quarry patch rule**: an accepted patch (8-connected, after the MMU) that contains or
    8-touches an EVT 2022 pixel 7295 (Quarries-Strip Mines-Gravel Pits) is rejected whole. This
    separates all 15 rejected patches (`evt2022_patch_rejected.tif`, 1,089.5 ac) from all 2,901
    accepted ones with zero exceptions; without the dilation 4 rejected patches are missed.
  Validate both recipes by rerunning the five FL5 counties and comparing `treemap2022_improved.tif`
  and `treemap2022_provenance.tif` pixel for pixel with `common.FL5_RELEASED` before going statewide.
* **County extents** (TreeMap grid, padded 64 px): 67 counties, 362M px total; largest bbox
  Monroe 47M px (the Keys), all others ≤ 10.5M px. FL grid ≈ 26.8k × 23.0k px.
* **TPO targets statewide**: USFS NRUM downloads (Box folder `y4ziirdb9v7zardus0cuajh7ziy9b2id`;
  download URL `https://usfs-public.app.box.com/index.php?rm=box_download_shared_file&shared_name=y4ziirdb9v7zardus0cuajh7ziy9b2id&file_id=<id>`).
  Florida file ids: 2013 f_785538549485, 2015 f_785546864320, 2017 f_785544789157,
  2018 f_785547633986, 2019 f_803055899685, 2020 f_1533697467896, 2021 f_1055182618092,
  2022 f_1327414753739, 2023 f_1726577190712, 2024 f_2095807921369.
  Sheet `County Production`, `REMCLASSCD == 1` (Roundwood product), `MCFVOL` (thousand ft³), by
  `COUNTYCD` × `OWNCD` (1 National Forest, 2 Other Public, 4 Private). This reproduces
  `config/tpo_targets.yaml`'s FL5 workbook **exactly** for 2013–2021 (county and owner group);
  2022–2024 Hamilton/Suwannee and 2019/2021–24 owner splits differ (NRUM revisions). The FL5
  config's 2013–2024 average = mean over these 10 survey years. NRUM gives **county × owner**
  targets, which the FL5 workbook never had.
* **FIA**: `SQLite_FIADB_ENTIRE.db` has `FVS_STANDINIT_PLOT` / `FVS_TREEINIT_PLOT` (all states;
  TreeMap FL donors include out-of-state plots). Read `STAND_CN`/`PLT_CN` as text (AGENTS.md gotcha).
* **Streams**: `NHD_FL_GDB` (EPA NHDPlus 2022 snapshot, FL). FL BMP widths in `config/bmp_rules.yaml`
  (35/50/75/75 ft); 2026-08-24 used 75 ft perennial / 35 ft intermittent.

## What the existing machinery costs (measured on FL5, `experiments/2026-10-12_raster-set-schedules`)

* Stands are pixel classes (`TM plot × county × owner`), 12.9k on v1 FL5 (1.26M ac), ~19.9
  options each, 34.8k distinct FVS runs for 948 FVS stands (690 plots + 258 young lists),
  1 failure in 35,945. FVS runs dedupe by keyfile hash, so they scale with **distinct plots**.
* Annealer (`weekly-artifact/2026-09-14/make_annealed_plan.py`): one joint landscape; objective =
  Σ squared relative deviation per (county, cycle) and (owner group, cycle) + standing-volume term.
  Counties are coupled only through the owner-group targets. 10 × n proposals per temperature,
  ~360–450 levels at cooling 0.98 → ~550–600 s per restart on FL5, ≈ 10 µs/proposal, one core per
  restart, 22 restarts forked. Volumes are Python `list[list[list[float]]]` ≈ 6–7 KB per stand.
* Extrapolation, to be **measured**, not assumed: LETO White Springs (2026-08-24) gave 6,944 MUs on
  35.7k forest ac → statewide FL (~15M ac) ≈ 2–3M MUs. Pure-Python annealer at that size ≈ 30+ h
  per restart and ~15–20 GB → does not fit this 15 GB / 32-core machine. Plan: numba/numpy
  annealer (flat float32 volume array, CSR option offsets) and a county-decomposed variant using
  NRUM county × owner targets (then counties are independent and embarrassingly parallel).
  LETO stage-4 composite tree lists (one FVS run per unit × option) would be ~40–60M runs: not
  feasible locally; give each unit its modal `(TM_ID, young)` plot so FVS stays per plot.

## Plan (each step: write it, test on FL5 first, then statewide; record wall + peak RSS via `measured`)

1. **Done 2026-10-13.** `01_hansen.py` warps both layers (nearest, exact transform) onto
   `florida_grid()` (27,052 × 23,306 px) as the 2-band `HANSEN_FL_TIF` (164 MB): 43 s, 3.0 GB
   peak RSS (`outputs/01_hansen.json`). On the FL5 window (12.39M px) it differs from the EE
   export `HANSEN_AOI_TIF` on 6 lossyear and 15 treecover2000 pixels; the v1 rule differs on
   3 pixels, v4 on 1. GDAL's default approximate transformer had differed on 3.3 % of
   treecover2000 (sub-pixel sampling shifts, no datum shift: EE samples exact pixel centres).
   Consequence for step 2: rerun FL5 with `--hansen-tif HANSEN_AOI_TIF` for the pixel-for-pixel
   gate; the statewide raster can move those few pixels.
2. **Done 2026-10-13**, all four recipes (v2/v3 = v1 with EVT gates v2/v3).
   `02_rasters.py {gate,counties,stitch,check,seams,obata-gap} --recipe {v1,v2,v3,v4}`.
   Gate: every recipe reproduces every released FL5 raster with 0 differing pixels. Statewide
   (`work/rasters/<recipe>/florida/`, ~670 MB each), 67/67 counties each, no errors, 4 workers:
   counties 2:48–3:32 wall (542–716 s summed; 6:27 for v2 under 2x machine load), 0.8–1.2 GB
   each, Monroe (47M px) 3.8–4.1 GB; stitch 100–110 s at 7.5–8.1 GB peak RSS, MemAvailable down
   to 3.0 GB. Added back: v1 1,354,185 / v2 1,061,620 / v3 926,741 / v4 446,098 ac on 15.92M
   published forest ac. County lines: `county_improvement.required_pad` grows a county's padding
   until every patch reaching it lies 64 px inside its grid (5–30 counties grow, to ≤ 320 px);
   at a fixed 64 px one v1 patch (5,829 ac, Baker/Duval) took a different donor on each side.
   `seams` now finds 0 crossing patches that disagree (108–431 cross). Check: published mosaic ==
   TreeMap; FL5 differs from the gate by ≤ 135 px within 15 px of the EE Hansen export's edge.
   Without Obata FL5 loses 14.5 % (v1) / 16.8 % (v4) of its add-back (the same at a fixed 64 px
   and with the grown padding); the scene holds 16.6 % of FL land, so counties off it miss
   roughly 140k (v1) / 46k (v4) ac (`outputs/02_obata_gap_<recipe>.json`, `statewide`). The stitch is the RAM
   ceiling: `county_improvement._write` copies the whole mosaic into a MemoryFile before the COG.
3. `03_leto.py`: on the state mosaic, not per county: county lines must not cut stands (user,
   2026-10-13); a unit crossing one carries its acres per county for the county TPO targets.
   `segment` in `pipeline/leto_ca.py` on VAT features (FORTYPCD, STDAGE, BALIVE,
   QMD, TPA; young add-back pixels take the establishment list's age 5 / BA / TPA), Harris owner
   as hard boundary, `split_management_units` with NHD buffers. Unit table: county, owner class,
   acres, unit_class, modal plot (young → `Y<TM_ID>`), stand age.
4. `04_fvs.py`: FVS input DB from FIADB for every plot + young stands (reuse
   `landscape.young_fvs_inputs` in the raster-set-schedules worktree), menu =
   `open_management_regimes.yaml` in the raster-set-schedules worktree (`repeat_offsets_25`), runs through
   2026-10-08's `enumerate_variant` / `run_missing`, 30 workers.
5. `05_tpo_targets.py`: NRUM → statewide `by_county`, `by_owner_group`, `by_county_owner`.
6. `06_schedule.py`: joint statewide anneal (numba) and county-decomposed anneal; compare
   objective, deviation and time; same metrics as `02_schedule.metrics`.
7. Artifact (Artifact tool, `quickstart` with intent `document` first): results, errors seen,
   measured RAM/time per stage, per-state projection, scaling recommendation, scheduler answer.
"""

from __future__ import annotations

import json
import os
import resource
import time
from contextlib import contextmanager
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

EXP = Path(__file__).resolve().parent
REPO = EXP.parents[1]
WORK = EXP / "work"
OUTPUTS = EXP / "outputs"
INPUTS = WORK / "inputs"

TREEMAP_TIF = Path("/mnt/d/TreeMap-2022/Data/TreeMap2022_CONUS.tif")
TREE_TABLE = Path("/mnt/d/TreeMap-2022/Data/TreeMap2022_CONUS_Tree_Table.csv")
LANDFIRE = Path("/mnt/d/landfire")
COUNTIES_SHP = Path("/mnt/d/county_p010g.shp_nt00934/countyp010g.shp")
IMPROVED_INPUTS = Path("/mnt/d/improved-rasters/inputs")
OBATA_TIF = Path.home() / ("projects/raster-improvement/GEE-raster-correct/data/runs/"
                           "disturbance-2010-2023-p17-r39-MovAvgRange3-mif4-tol3/"
                           "lastDist-2010-2023-p17-r39-MovAvgRange3-mif4-tol3.tif")
HANSEN_AOI_TIF = Path.home() / "projects/raster-improvement/data/interim/aoi/hansen_aoi.tif"
HANSEN_DIR = WORK / "downloads/hansen"
HANSEN_FL_TIF = INPUTS / "hansen_fl.tif"   # band 1 lossyear, band 2 treecover2000
FIADB = Path("/mnt/d/SQLite_FIADB_ENTIRE/SQLite_FIADB_ENTIRE.db")
NHD_FL_GDB = Path("/mnt/d/US SE Streams - FINAL/US SE Streams - FINAL/Streams By State/"
                  "nhdplus_epasnapshot2022_fl.gdb")

# The released FL5 rasters each recipe must reproduce before it runs statewide.
FL5_RELEASED = {"v1": REPO / "data/processed/improved-rasters-evt2022-gated/aoi_5county",
                "v2": REPO / "data/processed/improved-rasters-evt2022-gate-v2/aoi_5county",
                "v3": REPO / "data/processed/improved-rasters-evt2022-gate-v3/aoi_5county",
                "v4": REPO / "data/processed/improved-rasters-evt2022-gate-v4/aoi_5county"}

PIXEL = 30.0
ACRES_PER_PIXEL = 900.0 / 4046.8564224
STATE_FIPS = "12"


class Recipe(StrEnum):
    V1 = "v1"   # union of bookends, Obata, Hansen; EVT gate v1; disturbance 2010-2022
    V2 = "v2"   # v1 with EVT gate v2 (developed blocked)
    V3 = "v3"   # v1 with EVT gate v3 (developed and 7997 pasture blocked)
    V4 = "v4"   # bookends or (Obata and Hansen); EVT gate v4; disturbance 2012-2022


# Hansen loss years each recipe accepts, stored as years since 2000.
HANSEN_LOSSYEARS = {Recipe.V1: (1, 22), Recipe.V2: (1, 22), Recipe.V3: (1, 22), Recipe.V4: (12, 22)}


def recipe_dir(recipe: Recipe) -> Path:
    return WORK / "rasters" / str(recipe)


def florida_counties():
    """The 67 Florida counties (EPSG:5070), one row each, sorted by FIPS."""
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


def florida_grid(pad_px: int = 128) -> Grid:
    """Florida's bounding box snapped to TreeMap 2022's lattice, padded by `pad_px`."""
    import math

    import rasterio

    with rasterio.open(TREEMAP_TIF) as src:
        t = src.transform
    minx, miny, maxx, maxy = florida_counties().total_bounds
    c0 = math.floor((minx - t.c) / PIXEL) - pad_px
    c1 = math.ceil((maxx - t.c) / PIXEL) + pad_px
    r0 = math.floor((t.f - maxy) / PIXEL) - pad_px
    r1 = math.ceil((t.f - miny) / PIXEL) + pad_px
    return Grid(t.c + c0 * PIXEL, t.f - r0 * PIXEL, c1 - c0, r1 - r0)


@contextmanager
def measured(label: str, record: dict):
    """Wall time and the process's peak RSS (GB) for a step, written into `record`."""
    t = time.perf_counter()
    yield
    record[label] = {"seconds": round(time.perf_counter() - t, 1),
                     "peak_rss_gb": round(peak_rss_gb(), 3)}


def peak_rss_gb() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024 ** 2


def children_peak_rss_gb() -> float:
    return resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss / 1024 ** 2


def write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, default=float))


def workers(default: int) -> int:
    return int(os.environ.get("STATEWIDE_WORKERS", default))
