# S1 Initial State and Segmentation Design

**Date:** 2026-07-20
**Status:** Approved

## Goal

Make management-unit creation the first ARTEMIS stage and carry those units
through TreeMap/FIA attribution into FVS-ready initial-state tables without
ArcPy. Preserve two scientifically meaningful segmentation methods:

1. a behaviorally faithful port of `scripts/LETO.V1.1.txt`; and
2. the parcel/LANDFIRE boundary-overlay method previously located in
   `pipeline/s3_management`.

Both methods must produce the same management-unit contract so their effects
can be compared independently of downstream TreeMap/FIA logic.

## Non-negotiable behavior

- Management-unit creation is S1, not S3.
- The LETO method is the baseline and preserves the legacy stage order,
  thresholds, formulas, and output fields.
- The boundary-overlay method remains available as a research alternative.
- A mixed-plot unit retains a modal `PLT_CN` for identity and QA, while its FVS
  tree list uses every retained donor plot with normalized TreeMap weights.
- Production data access is a hard preflight. If `data/raw` does not resolve to
  the mounted data drive or a required source is unreadable, stop with a message
  asking for the `D:` drive to be mounted or the Cloudflare R2 replica restored.
  Do not silently substitute synthetic data.
- Synthetic fixtures remain appropriate for automated unit and parity tests;
  they are not a substitute for the production smoke test.

## Verified production sources

The repository path `data/raw` resolves to `/mnt/d`. S1 uses these sources:

| Source | Project-relative path | Required content |
| --- | --- | --- |
| TreeMap raster | `data/raw/TreeMap-2022/Data/TreeMap2022_CONUS.tif` | EPSG:5070, 30 m, TreeMap value cells |
| TreeMap lookup | `data/raw/TreeMap-2022/Data/TreeMap2022_CONUS.tif.vat.dbf` | `Value`, `PLT_CN` |
| FIA database | `data/raw/SQLite_FIADB_ENTIRE/SQLite_FIADB_ENTIRE.db` | `TREE` rows for AL, FL, GA, SC |
| FVS species crosswalk | `data/raw/FVS_SpeciesCrosswalk.xls` | `EasternSpeciesTranslator` sheet |
| Ownership raster | `data/raw/RDS-2025-0045/Data/US_forest_ownership.tif` | ownership classes 0 through 8 |
| Five-county parcels | `data/raw/FL_5_Co_Parcels.gdb` | `FL_5_Co_Parcels` layer |
| Legacy streams | `data/raw/FL_5_Co_Streams.zip` | `FL_5_Co_Streams.shp` |

The file-based API may accept caller-selected equivalents, but the documented
production defaults above are the primary runnable path.

## Package organization

```text
pipeline/s1_initial_state/
├── segmentation/
│   ├── __init__.py
│   ├── leto.py
│   ├── boundary_overlay.py
│   └── comparison.py
├── data_sources.py
├── weights.py
└── leto_initial_state.py
```

`segmentation/leto.py` owns only LETO management-unit geometry and attributes.
`segmentation/boundary_overlay.py` becomes the canonical home of the existing
S3 experiment. `segmentation/comparison.py` compares any two outputs that meet
the shared contract. `data_sources.py` owns production paths, preflight, and
read-only TreeMap VAT/FIADB loading.

The old `pipeline/s3_management/sketch_management_units.py` remains as a thin
compatibility wrapper so existing commands and imports do not break. It must
contain no independent segmentation implementation.

## Shared management-unit contract

Both strategies return a projected `GeoDataFrame` containing at least:

- `MU_ID`: unique string identifier;
- `Acres`: polygon area in acres;
- `SEGMENTATION_METHOD`: `leto` or `boundary_overlay`;
- geometry.

After shared attribution, both contain:

- `PLT_CN`: modal TreeMap/FIA plot identifier;
- `TM_VALUE`: modal TreeMap raster value;
- `OWN_CODE` and `OWN_TYPE`: majority ownership;
- `SMZ_Pct`: percent of unit area inside the configured stream buffer.

Method-specific diagnostic columns may remain, but downstream initial-state
code consumes only this shared contract plus the generated plot-weight table.

## Strategy A: faithful LETO segmentation

The pure-Python implementation follows `LETO.V1.1.txt` in this order:

1. Polygonize the valid TreeMap raster using ArcPy `RasterDomain` cell-center
   boundary semantics, then clip it to the five-county parcel extent. Windowed
   reads include a one-cell halo so the AOI window does not create a false
   raster-domain edge.
2. Calculate acreage and `Points = max(2, ceil(Acres / 100))`.
3. For every polygon above 200 acres, generate constrained random points with
   a 1,000-foot minimum separation.
4. Construct Thiessen/Voronoi cells, clip them to the parent polygon, merge
   them with units at or below 200 acres, and repeat until no unit is above the
   threshold or no further valid split is possible.
5. Explode multipart polygons and delete pieces below 5 acres.
6. Clip to parcel boundaries again and assign stable `MU_ID` values.
7. Build raw TreeMap cell weights and assign the modal `PLT_CN`/`TM_VALUE`.
8. Assign majority ownership from the ownership raster.
9. Buffer the legacy stream layer by 35 feet and calculate `SMZ_Pct` from the
   intersected area.

The original ArcPy script does not set its random generator. Therefore an old
ArcPy run cannot be expected to have coordinate-identical polygons. The Python
port accepts a seed and guarantees repeatability for a fixed input and seed.
Faithfulness means matching the algorithm, thresholds, field semantics, and
invariants. Actual ArcPy/Python outputs are compared statistically and
topologically rather than asserted byte-for-byte.

The splitter must fail clearly when point-separation constraints make a split
impossible; it must not loop forever or silently leave an oversized polygon.

## Strategy B: boundary-overlay segmentation

Move the existing S3 method into S1 without changing its scientific intent:

1. intersect parcels with the LANDFIRE EVT forest mask;
2. erase Florida BMP stream buffers, waterbodies, and the small road-artifact
   buffer;
3. retain and classify slivers for QA;
4. optionally split large units with the current fixed fishnet method; and
5. emit the shared management-unit contract.

Its existing CLI behavior remains available through the compatibility wrapper.
Unimplemented or approximate behavior already present in the method must be
identified in the side-by-side review rather than presented as equivalent to
LETO.

## Shared TreeMap/FIA initial-state construction

Both segmentation strategies feed the same downstream stages:

1. rasterize `MU_ID` on the TreeMap grid;
2. count valid `(MU_ID, TM_VALUE)` cells;
3. join `TM_VALUE` to `PLT_CN` from the TreeMap VAT;
4. retain the modal plot on each unit;
5. drop donor plots below a 0.05 raw weight and renormalize retained weights;
6. query live FIA trees from the read-only SQLite database for relevant
   `PLT_CN` values and the AL/FL/GA/SC state codes;
7. map FIA species through `EasternSpeciesTranslator` to FVS Southern codes;
8. multiply `TPA_UNADJ` by normalized plot weight;
9. copy the nearest runnable unit's tree list when a unit has no live mapped
   trees; and
10. write the LETO-compatible management-unit, stand, and tree tables.

The modal plot does not replace the weighted donor set. It is the representative
plot used on the management-unit layer and crosswalk.

## Comparison deliverables

### ArcPy LETO versus pure-Python LETO

Provide both automated and file-based comparison:

- source-stage mapping between ArcPy functions and Python functions;
- deterministic unit tests for acreage, subdivision rules, cleanup, TreeMap
  weights, modal selection, ownership, and SMZ percentage;
- a comparator that accepts exported ArcPy and Python GeoPackages/feature
  layers and reports coverage, overlap, symmetric difference, unit counts,
  area distributions, oversized/sliver counts, plot weights, modal plot
  agreement, ownership agreement, and SMZ differences; and
- an optional ArcPy-marked integration test or fixture hook that skips cleanly
  when ArcPy/reference output is unavailable.

### LETO versus boundary overlay

Run both methods on the same AOI and report:

- conceptual differences and common stages;
- unit count and acreage distribution;
- gaps, overlaps, and slivers;
- boundary fragmentation;
- TreeMap donor plots per unit and mixed-plot frequency;
- percent of units with usable FIA trees before and after imputation; and
- implications for management realism, reproducibility, and FVS cost.

The review belongs in a durable Markdown document and is supported by machine-
readable metrics rather than qualitative claims alone.

## Synthesis specification

Write a separate research specification after the two baselines are runnable.
It must preserve both baseline methods and define experiments for combinations
such as LETO's TreeMap-domain/FIA attribution with boundary-overlay exclusions,
alternative large-unit splitters, and alternative sliver policies. It must
define hypotheses, controlled factors, metrics, and keep/reject criteria. It
does not select or implement a hybrid before baseline evidence exists.

## Outputs

Each run writes its method and parameters to a small JSON manifest and may write
the following inspectable artifacts:

- `ManagementUnits.gpkg`
- `MU_PLT_CN_Weights.csv`
- `MU_FVS_Crosswalk.csv`
- `FVS_StandInit.csv`
- `FVS_TreeInit.csv`
- `MU_FVS_Stands_No_Live_Trees.csv`
- comparison metrics CSV/JSON when two segmentation outputs are supplied.

## Notebook

Extend `notebooks/LETO_Initial_State_Walkthrough.ipynb` so management-unit
creation is its first analytical stage. The notebook selects a segmentation
method, displays its parameters and diagnostics, then invokes the shared
TreeMap/FIA/FVS functions. It must import production functions and keep output
writing disabled by default.

## Verification

- Use test-driven development for every behavior change.
- Keep a portable synthetic parity suite that does not require ArcPy or `/mnt/d`.
- Add marked production smoke tests that use a deliberately small AOI and the
  verified local data sources.
- Run the S1 tests, existing S3 compatibility tests, notebook structural tests,
  full repository suite, Ruff lint, and Ruff formatting.
- Request independent code review before completion.
- If a production source is unavailable, stop; do not claim completion from
  synthetic tests alone.
