# Management Units Research - Current State

## Completed (v1 partial)

### 1. Reconstructed naive delineation engine ✓
- **File**: `/home/chazm/projects/artemis-model/.claude/worktrees/mgmt-units-research/pipeline/s3_management/sketch_management_units.py`
- **Status**: PASSING ALL TESTS
- **Test command**: `uv run pytest tests/test_s3_sketch_management_units.py -q` → 14/14 passed
- **Functions restored**:
  - `feet_to_meters`
  - `classify_stream_fcode`
  - `classify_unit_size`
  - `target_grid_cell_size_m`
  - `clean_geometries` (fixed to preserve LineStrings)
  - `split_large_geometry`
  - `process_county` (full pipeline)
- **Verified**: Signature-compatible with existing test suite; logic matches notes/management_units.md description

### 2. Raster segmentation implementation ✓ (code ready, not yet run)
- **File**: `/home/chazm/projects/artemis-model/.claude/worktrees/mgmt-units-research/research/mgmt_units/segmentation_delineation.py`
- **Strategies implemented**:
  - Felzenszwalb graph-based segmentation (scale=100, sigma=0.5, min_size=50)
  - SLIC superpixel segmentation (n_segments=1000, compactness=10.0)
- **Features**:
  - Loads raster stack (EVT, forest mask, placeholder for TreeMap/ownership)
  - Vectorizes segments to polygons
  - Applies same BMP/water/road erase as naive approach (fair comparison)
  - Computes area, size_class, and **Polsby-Popper compactness** per unit
  - Cross-strategy comparison function with 4-panel visualization (ECDF, compactness, size class, histogram)
- **Dependencies added**: `scikit-image`, `matplotlib`, `seaborn`
- **NOT YET RUN**: needs `uv run python research/mgmt_units/segmentation_delineation.py --county-fips 125`

### 3. Naive baseline results (persisted, Union County)
- **Path**: `data/interim/management_units_smoke_union/12125_union/candidate_management_units.gpkg`
- **Polygons**: 17,020
- **Size distribution** (from BRIEF.md):

| size_class | polygons | area (ha) | median (ha) |
|---|---|---|---|
| candidate (2–40 ha) | 2,077 | 18,124 | 5.7 |
| large (>40 ha) | 91 | 5,728 | 55.8 |
| sliver (<2 ha) | 14,852 | 3,764 | 0.09 |

- **Key finding**: 87% slivers — the core problem

## Remaining deliverables (v1)

### 3. Quantitative comparison (NOT STARTED)
- Run: `uv run python research/mgmt_units/segmentation_delineation.py --county-fips 125`
- Will produce:
  - `research/mgmt_units/outputs/strategy_comparison.csv` (table of metrics)
  - `research/mgmt_units/outputs/strategy_comparison.png` (4-panel figure)
  - `research/mgmt_units/outputs/12125_felzenszwalb/management_units.gpkg`
  - `research/mgmt_units/outputs/12125_slic/management_units.gpkg`
- **Metrics**: polygon count, sliver fraction, median/mean area, median/mean compactness, total forest-area retention

### 4. Downstream FVS implication analysis (NOT STARTED)
- Compute per strategy:
  - Number of unique units → simulation workload proxy
  - Mean forest area per unit → output resolution
  - (Optional) Number of unique `(TreeMap plot-ID × forest-type × site-index-bin)` combos if TreeMap/FIA integration is feasible
- Save as `research/mgmt_units/outputs/fvs_workload_comparison.csv`

### 5. PAPER_SKELETON.md (NOT STARTED)
- Target venue: *Forest Science* or *Computers and Electronics in Agriculture* (justify based on methods vs application focus)
- Abstract + intro + methods + results (w/ real numbers from comparison) + discussion + limitations
- No invented citations — mark literature as `needs verification`

### 6. This STATE.md ✓ (this file)

## Known gaps & open questions

1. **Raster stack**: Current segmentation uses EVT + binary forest mask + constant. Production version should add:
   - TreeMap plot-ID (requires `/mnt/d` access; path: `data/raw/TreeMap-2022/Data/TreeMap2022_CONUS.tif`)
   - Ownership (path: `data/raw/RDS-2025-0045/Data/US_forest_ownership.tif`)
   - Terrain derivatives (DEM-derived slope, if accessible)

2. **Segmentation parameter tuning**: Current params (Felzenszwalb scale=100, SLIC n_segments=1000) are guesses. Should sensitivity-test to find params that produce median unit size ~5-10 ha.

3. **Hybrid/constrained variant** (BRIEF.md suggests as "ideally"): e.g., segmentation clipped to parcels, or sliver merge-to-best-neighbor. Time-permitting third strategy.

4. **Validation/reference**: BRIEF.md §6 rubric calls for "defensible reference / validation or sensitivity axis". Current plan uses parameter sensitivity + downstream FVS workload. Could add:
   - Compare unit-size distribution against FIA condition polygons (if accessible)
   - Hand-digitize a few reference stands for spatial agreement check

5. **Scale-up path**: Current AOI = Union County (tractable). BRIEF.md says document how it scales statewide. Need runtime estimate and chunking strategy discussion.

## File inventory

```
pipeline/s3_management/
  sketch_management_units.py          # naive engine (reconstructed, tested)

research/mgmt_units/
  BRIEF.md                             # grounding (read-only)
  STATE.md                             # this file
  segmentation_delineation.py          # raster segmentation (ready to run)
  outputs/                             # (not yet populated)

tests/
  test_s3_sketch_management_units.py   # 14 tests, all passing

data/interim/management_units_smoke_union/12125_union/
  candidate_management_units.gpkg      # naive baseline (17,020 polygons)
  summary.csv                          # size-class breakdown
  qa_layers.gpkg                       # buffers, masks, etc.
```

## Session paused — READY TO EXECUTE

**Status:** Infrastructure complete and tested. Segmentation script ready but NEVER RUN.

**Next action (immediate):**
```bash
cd /home/chazm/projects/artemis-model/.claude/worktrees/mgmt-units-research
uv run python research/mgmt_units/segmentation_delineation.py --county-fips 125
```

**Expect first-run failures.** Debug systematically per `NEXT_STEPS.md`.

**See `research/mgmt_units/NEXT_STEPS.md`** for complete execution plan, deliverable checklist, and templates for:
- Parameter sensitivity script
- FVS workload script  
- PAPER_SKELETON.md structure

**Estimated time to v1 completion: 2-3 hours wall time**
