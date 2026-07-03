# Next Steps to Complete v1

**Current status:** Infrastructure complete and tested. Ready to execute comparison and produce deliverables.

## Immediate next action (5 minutes)

Run the segmentation comparison:

```bash
cd /home/chazm/projects/artemis-model/.claude/worktrees/mgmt-units-research
uv run python research/mgmt_units/segmentation_delineation.py --county-fips 125
```

**Expected:** This will likely fail on first run (it's never been executed). Debug systematically:
- If memory error → reduce AOI to bbox subset, document it
- If missing dependency → install via `uv add <package>`
- If path error → verify data mount at `data/raw`
- If raster read error → check EVT/TreeMap/ownership paths and CRS compatibility

## Deliverables checklist

Once the script runs successfully:

### 1. Cross-strategy comparison ✓ (script ready)
- [ ] `research/mgmt_units/outputs/strategy_comparison.csv` — metrics table
- [ ] `research/mgmt_units/outputs/strategy_comparison.png` — 4-panel figure
- [ ] `research/mgmt_units/outputs/12125_felzenszwalb/management_units.gpkg`
- [ ] `research/mgmt_units/outputs/12125_slic/management_units.gpkg`

**Metrics to verify:** polygon_count, sliver_fraction (<2 ha), median_area_ha, mean_area_ha, total_forest_ha, median_compactness, mean_compactness

### 2. Parameter sensitivity (NEW CODE NEEDED)
Create `research/mgmt_units/param_sensitivity.py`:

```python
# Sweep Felzenszwalb scale: [50, 100, 200, 400] 
# OR SLIC n_segments: [500, 1000, 2000, 4000]
# Record: scale/n_segments, median_area_ha, sliver_fraction, n_units
# Goal: find params that yield median ~5-10 ha
```

Save:
- [ ] `research/mgmt_units/outputs/param_sensitivity.csv`
- [ ] `research/mgmt_units/outputs/param_sensitivity.png` (line plots)

### 3. FVS workload analysis (NEW CODE NEEDED)
Create `research/mgmt_units/fvs_workload.py`:

```python
# Per strategy, compute:
# - n_units (upper bound on FVS runs)
# - mean/median forest_area_ha per unit (output resolution)
# - (Optional) TreeMap plot-ID intersection: 
#   count unique (unit × dominant-plot-ID) combos as tighter FVS estimate
```

Save:
- [ ] `research/mgmt_units/outputs/fvs_workload_comparison.csv`

**Note:** TreeMap intersection may be slow. If >5 min, approximate and document.

### 4. Paper skeleton (WRITE MARKDOWN)
- [ ] `research/mgmt_units/PAPER_SKELETON.md`

Template:

```markdown
# Operationally-realistic forest management unit delineation for regional process-based simulation

**Target venue:** Forest Science (OR Computers and Electronics in Agriculture) — JUSTIFY

## Abstract
[150 words: problem, approach, key finding, implication]

## Introduction
- FVS requires stand-level units, not pixels
- Prior work: [mark as `needs verification`]
- Contribution: reproducible, open-data method + quantitative comparison

## Methods
### AOI
Union County, FL (FIPS 12125). [X] forest ha.

### Strategies
1. Naive: parcels ∩ forest, erase BMP/water/roads
2. Felzenszwalb: raster segmentation on EVT stack (scale=X)
3. SLIC: superpixel (n_segments=X)

### Metrics
- Area distribution, sliver fraction, compactness (Polsby-Popper)
- FVS workload proxy: n_units, forest-area per unit

### Reproducibility
```bash
uv run python research/mgmt_units/segmentation_delineation.py --county-fips 125
```

## Results
[INSERT REAL NUMBERS FROM strategy_comparison.csv]

| Strategy | n_units | sliver_frac | median_ha | mean_compactness |
|---|---|---|---|---|
| Naive | 17020 | 0.87 | 0.09 | [X] |
| Felzenszwalb | [X] | [X] | [X] | [X] |
| SLIC | [X] | [X] | [X] | [X] |

**Key finding:** [State which strategy reduces slivers, achieves operational size]

[REFERENCE strategy_comparison.png]

Parameter sensitivity: [INSERT param_sensitivity.csv findings]

FVS implications: [INSERT fvs_workload_comparison.csv]

## Discussion
- Raster segmentation reduces sliver explosion by [X]%
- Median unit size [closer to / farther from] SE operational norms (~5-10 ha)
- FVS workload: [X]k runs (naive) → [X]k (segmentation)

## Limitations
- Single county (Union, FL) — not statewide
- Raster stack simplified (EVT + forest mask; TreeMap/ownership for production)
- No field validation against hand-digitized stands
- Parameter sensitivity tested but not optimized

## Scale-up path
Statewide: chunk by county, run in parallel. Estimated runtime: [X] hrs for FL.

## Conclusion
[1 para: contribution, actionable takeaway]
```

### 5. Update STATE.md (ALWAYS)
When done, replace STATE.md content with:

```markdown
# Management Units Research - v1 COMPLETE

## Completed deliverables

### 1. Reconstructed naive engine ✓
[keep existing content]

### 2. Cross-strategy comparison ✓
- Command: `uv run python research/mgmt_units/segmentation_delineation.py --county-fips 125`
- Output: [list actual files in outputs/]
- Key finding: [1 sentence from real numbers]

### 3. Parameter sensitivity ✓
- [summarize which params hit target median area]

### 4. FVS workload comparison ✓
- [n_units for each strategy]

### 5. PAPER_SKELETON.md ✓
- Target venue: [X]
- [link to file]

## Test status
- `uv run pytest tests/test_s3_sketch_management_units.py -q` → 14/14 passed

## Files produced
```
research/mgmt_units/outputs/
  strategy_comparison.csv
  strategy_comparison.png
  param_sensitivity.csv
  param_sensitivity.png
  fvs_workload_comparison.csv
  12125_felzenszwalb/management_units.gpkg
  12125_slic/management_units.gpkg
```

## Open questions for critic
1. [Real question from actual results]
2. [Real question from actual results]
3. [Real question from actual results]
```

## Expected runtime
- Segmentation run: 10-30 min (Union County only)
- Parameter sweep: 30-60 min (3-4 param values)
- FVS workload: 5-10 min
- Paper skeleton: 20 min writing

**Total: ~2-3 hours wall time**

## Key principles
1. RUN everything. No invented numbers.
2. If it fails, DEBUG systematically (root cause, not paper-over).
3. Every number in skeleton traces to a CSV/PNG in outputs/.
4. Fixed seeds for reproducibility.
5. Document actual commands run.
