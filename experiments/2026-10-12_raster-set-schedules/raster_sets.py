"""FL5 open-menu schedule on the repaired rasters: the landscapes, and the experiment's record.

The 2026-10-08 open-menu deck (`experiments/2026-10-08_permissive-menus`, artifact "ARTEMIS · FL5
open menus") scheduled the *published* TreeMap 2022. Its stands are 2026-08-24's carved pixel
classes (`TreeMap plot x county x Harris owner`), reused through 2026-09-14's
`carved_stands.csv`, so no add-back raster went into its numbers. Its cut maps paint the
published rasters out of the gate-v2 folder, and only their riparian mask (the LETO units) is v2.
This experiment reruns that plan on each repaired raster set on R2
(`data/raster_improvement_data/processed/improved-rasters-evt2022-*`), holding everything else
fixed: stand recipe, menu, FVS, annealer, TPO targets, cooling, restarts and seeds. Its finding is
the deck slide `raster-sets` (after `capacity`).

Landscapes (`ALL`):

    published       published TreeMap; Harris warped onto the grid as 2026-08-10 did
    ownership-only  published TreeMap; v4's repaired ownership (separates the owner repair)
    gated (v1)      improved pair; union rule, EVT gate v1
    gate-v2         improved pair; union rule, EVT gate v2
    gate-v3         improved pair; union rule, EVT gate v3 (adds pasture/hay)
    gate-v4         improved pair; bookends or (Obata and Hansen) 2012-2022, EVT gate v4

Each set's folder holds, under `aoi_5county/`, the published TreeMap and Harris rasters and its
own improved pair on the same 30 m grid. `PUBLISHED` reads the published TreeMap out of the
gate-v2 folder (byte-identical in all four) and warps the Harris raster itself: the folders'
`nwos2022_published.tif` was resampled by another path and differs on 582 forest pixels
(129 ac), so only the direct warp reproduces what 2026-10-08 scheduled.

Steps, run from the repo root (`work/` is gitignored; inputs staged under `data/` as in
2026-08-24's README, plus the four raster folders' `aoi_5county/` and `HARRIS_TIF`):

    00_establishment_lists.py   (PYTHONPATH=../improved-rasters) work/<set>/establishment_tree_lists.csv
    01_landscape.py --raster-set <set>                           work/<set>/carved_stands.csv
    02_fvs_inputs.py                                             work/FVS_Data.db
    03_library.py                                                work/<set>/library_*.csv, run cache
    04_schedule.py                                               outputs/<set>/
    05_compare.py                                                outputs/{comparison,suwannee}.csv
    fia_by_county.py                                             outputs/fia_by_county.csv
    06_slide.py                                                  the deck slide `raster-sets`

How each piece was checked:

* The published landscape equals 2026-09-14's `carved_landscape()` for all 11,831 stands (ids,
  plots, owner classes, branches, unit classes, ages; acres within 2e-12). Counties, buffers and
  ownership are rasterised on the 5-county TreeMap grid and cropped, as 2026-08-24 did:
  rasterising on the sets' own aligned grid flips buffer-edge pixels and renumbers corridors.
* Add-back pixels never read the donor's trees. A provenance-4 pixel is its own class,
  `YTM<tm_id>`, reading the FVS stand `Y<tm_id>`: the donor's site row, the list's
  establishment age (5), and the `scaled_young` rows of `establishment_tree_lists.csv` (AGENTS.md,
  ADR 0002). Step 0 regenerates the lists for all four sets; the regenerated v3 and v4 equal the
  shipped files. FVS reads a young stand back at 380.8 TPA and 18 ft2/ac at age 5, v4's figure.
* The menu is the deck's, rebuilt. The deck's open-menu config was never committed (on 67204d8
  `config/management_regimes.yaml` is byte-identical to its `baseline_management_regimes.yaml`
  and 25 of its own tests fail). This experiment reconstructs it into
  `config/management_regimes.yaml` from the deck's menu slide and those tests; it reproduces
  the deck's `library_size.csv` exactly for all six variants. Replace it with the original if
  that differs.
* FVS is the deck's FVS: `fvs/bin/FVSsn` built by 2026-09-14's recipe (open-main 1cdc1412, NVEL
  4fa39a0, `-ffpe-trap=invalid,zero,overflow`). It is byte-identical to `/usr/local/bin/FVSsn`
  wherever that survives (338 of 400 sampled); that build keeps the underflow traps and dies on
  ~15% of runs. One of 35,945 runs fails: plot 473803917489998 `hardwood_clearcut_regen@+0`, the
  same run 2026-09-14's committed `fvs_failures.csv` records.
* On `published` every deterministic number equals the deck's `outputs/deck/summary.json`
  (options per stand 17.8281633, greedy objective 338.9060399342391, Suwannee maximum 75.8%).
  The best plan is 7.335 against 7.362, inside both runs' seed spread: the search draws options
  by index and the original config's option order is not recoverable.
* Tests: `tests/test_raster_set_schedules.py`.

Results, 2026-10-12 (`outputs/comparison.csv`; Suwannee as % of its 50-year TPO total):

    landscape        forest ac  young ac  Suwannee ac (% FIA)  5-county  even-flow  Suwannee plan/max
    published           925k        -       143k (59%)          -8.0%      1.27        70 / 76
    ownership-only    1,078k        -       180k (75%)          -3.7%      0.26        86 / 97
    gated (v1)        1,264k      185k      232k (96%)          -0.3%      0.00        99 / 124
    gate-v2           1,237k      158k      228k (95%)          -0.5%      0.00        98 / 121
    gate-v3           1,197k      118k      211k (88%)          -1.4%      0.04        95 / 112
    gate-v4           1,157k       78k      200k (83%)          -2.2%      0.09        91 / 106

The owner repair alone adds 153k ac of existing forest (38k in Suwannee) and lifts Suwannee's
starting merchantable volume from 201M to 254M ft3 (FIA 2022: 247M). Add-back stands cut nothing
in the first period; they let the annealer cut mature Suwannee stands harder early (112M vs 92M
ft3 in period 1 on v1). TPO's Suwannee average for 2013-2024, 22.5M ft3/yr, doubled from 2011 to
2013 and is 1.8x FIA's net growth (12.3M +-21%; other counties 0.8-1.1x). On the repaired
landscapes the plan cuts ~1.1B ft3 there in 50 years against 0.86B of stock plus growth at
FIA's current rate, so it leans on FVS regrowing managed stands faster than FIA measures today.

Not done: stands are pixel classes, not LETO units; add-back stands start at age 5 (ADR 0002),
not at their dated age (ADR 0003); the minimum harvestable percentage is not enforced.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

EXP = Path(__file__).resolve().parent
REPO = EXP.parents[1]
PROCESSED = REPO / "data/processed"
WORK = EXP / "work"
OUTPUTS = EXP / "outputs"


class RasterLayer(StrEnum):
    PUBLISHED = "published"
    IMPROVED = "improved"
    OWNERSHIP_ONLY = "ownership_only"   # published TreeMap, repaired ownership: no add-back


@dataclass(frozen=True)
class RasterSet:
    name: str
    folder: str
    layer: RasterLayer
    label: str

    @property
    def aoi(self) -> Path:
        return PROCESSED / self.folder / "aoi_5county"

    @property
    def treemap(self) -> Path:
        layer = RasterLayer.IMPROVED if self.layer is RasterLayer.IMPROVED else RasterLayer.PUBLISHED
        return self.aoi / f"treemap2022_{layer}.tif"

    @property
    def ownership(self) -> Path:
        """The Harris class raster on this set's grid; `PUBLISHED` warps `HARRIS_TIF` instead."""
        layer = RasterLayer.PUBLISHED if self.layer is RasterLayer.PUBLISHED else RasterLayer.IMPROVED
        return self.aoi / f"nwos2022_{layer}.tif"

    @property
    def provenance(self) -> Path | None:
        return self.aoi / "treemap2022_provenance.tif" if self.layer is RasterLayer.IMPROVED else None


# The Harris et al. 2025 raster (RDS-2025-0045), local copy of R2's
# `data/RDS-2025-0045/Data/US_forest_ownership.tif`.
HARRIS_TIF = Path(os.environ.get("HARRIS_TIF", "/mnt/d/RDS-2025-0045/Data/US_forest_ownership.tif"))

PUBLISHED = RasterSet("published", "improved-rasters-evt2022-gate-v2", RasterLayer.PUBLISHED,
                      "Published TreeMap")
IMPROVED = (
    RasterSet("gated", "improved-rasters-evt2022-gated", RasterLayer.IMPROVED, "v1 gated"),
    RasterSet("gate-v2", "improved-rasters-evt2022-gate-v2", RasterLayer.IMPROVED, "v2"),
    RasterSet("gate-v3", "improved-rasters-evt2022-gate-v3", RasterLayer.IMPROVED, "v3"),
    RasterSet("gate-v4", "improved-rasters-evt2022-gate-v4", RasterLayer.IMPROVED, "v4"),
)
# The decomposition row: v4's repaired ownership over the published TreeMap. The ownership
# repair on published forest is the same in every set (to within 60 ac per county), so this
# separates "more owned forest" from "added-back young forest".
OWNERSHIP_ONLY = RasterSet("ownership-only", "improved-rasters-evt2022-gate-v4",
                           RasterLayer.OWNERSHIP_ONLY, "Published + owner repair")
ALL = (PUBLISHED, OWNERSHIP_ONLY, *IMPROVED)
BY_NAME = {rs.name: rs for rs in ALL}


def work_dir(rs: RasterSet) -> Path:
    return WORK / rs.name


def output_dir(rs: RasterSet) -> Path:
    return OUTPUTS / rs.name
