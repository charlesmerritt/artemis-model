# FIA vs TreeMap per-forest-type validation (Florida, 2026-08-03)

First execution of `r/07_FL_FIA_TreeMap_comparison.R`. The hole work validated *total*
forest area; this validates *per-FORTYPCD attributes* (BALIVE, TPA_LIVE, CARBON_L) and
tests whether area-scaling (mean-balancing, Iles 2009) removes the resulting bias.

**Headline: it does not.** Mean-balancing fixes the area term (−4.73% → −0.42% forest
acres) but leaves the per-acre attribute bias intact and slightly worse for BAA
(−6.89% → −7.35%) and carbon (−13.81% → −15.51%).

## How to run it

The script did not execute as previously committed; the five defects below are fixed in
the same commit that adds this directory.

```bash
cd data/interim/fl_fia_treemap_comparison   # holds symlinks to the input data
LD_LIBRARY_PATH=$HOME/.local/lib Rscript ../../../r/07_FL_FIA_TreeMap_comparison.R
```

The script uses relative paths, so it must run from a directory laid out like this:

| Script path | Real path |
|---|---|
| `RDS-2025-0032/Data/TreeMap2022_CONUS.tif` | `/mnt/d/TreeMap-2022/Data/` (via `/mnt/d/TreeMap_Chaz/RDS-2025-0032`) |
| `output/fia_FL/` | `/mnt/d/TreeMap_Chaz/output/fia_FL` (FL FIA CSVs) |

Environment: R 4.5.2, rFIA 1.1.4, terra 1.9.34, sf 1.1.2, geodata 0.6.9, dplyr 1.2.1.
`units` was built against a local udunits2 2.2.28 in `~/.local`, hence `LD_LIBRARY_PATH`.
Runtime ~4 min end to end. `geodata::gadm()` downloads the FL boundary at run time.

## Defects fixed

| # | Symptom | Cause | Fix |
|---|---|---|---|
| 1 | `unused argument (variance = FALSE)` | rFIA 1.1.4 removed `variance` from every estimator | dropped the argument (5 call sites) |
| 2 | 17× row explosion in Section 4.3/4.4 | `grpBy = FORTYPCD` returns one row per inventory year (17 for FL); the joins key on `FORTYPCD` alone | `filter(YEAR == max(YEAR))` on the three per-type tables, matching Section 3.1 |
| 3 | `unable to find an inherited method for 'area'` | `library(terra)` after `library(rFIA)` masks `rFIA::area` | qualified as `rFIA::area()` |
| 4 | `'names' attribute [3] must be the same length as the vector [2]` | `terra::freq(bylayer = FALSE)` returns 2 columns, not 3 | renamed to `c("Value", "pixel_count")` |
| 5 | `Column 'BAA_TOTAL' doesn't exist` | rFIA returns `BA_TOTAL` / `TREE_TOTAL` | renamed in Section 3.1 |

Defects 2–5 are independent of the rFIA version, so Sections 4.4/4.5 (the decomposition)
could never have produced output as committed. Defect 1 is version drift.

## Results

### Reproducibility vs the Feb 2026 partial run (`baseline_2026-02/`)

| Check | Result |
|---|---|
| FIA state estimates (2023) | identical to the digit |
| FIA per-type `FIA_BAA` | max diff 0.0 |
| TreeMap pixel count | 71,791,171 vs 71,791,293 (−122 px, 1.7e−6) |
| TreeMap per-type `TM_BAA_mean` | max diff 0.00039 sq ft/ac |
| Forest types / plot counts | 44 / identical |

The 122-pixel delta is the FL boundary mask (`geodata::gadm` re-download + terra
version), not a change in the estimates.

### State level (`outputs/FL_state_level_scaling_comparison.csv`)

| Metric | FIA | TreeMap raw | TreeMap scaled | raw %Δ | scaled %Δ | improved |
|---|---|---|---|---|---|---|
| Forest acres | 16,758,136 | 15,965,986 | 16,687,523 | −4.73 | −0.42 | yes |
| BAA (sq ft/ac) | 83.56 | 77.80 | 77.42 | −6.89 | −7.35 | **no** |
| TPA (trees/ac) | 481.31 | 519.48 | 507.14 | +7.93 | +5.37 | yes |
| Carbon AG live (tons/ac) | 19.98 | 17.22 | 16.88 | −13.81 | −15.51 | **no** |

The script's own decomposition (Section 4.5, TM-area weights → FIA-area weights) agrees:
BAA −5.40 → −6.12, TPA +25.72 → +26.22, carbon −3.11 → −3.09 sq ft/trees/tons per acre.
Note the two baselines differ — Section 4.5 weights *per-type* FIA means, the table above
compares against FIA's aggregate design-based estimate — so read direction, not magnitude.

Why scaling can make it worse: the types TreeMap under-allocates area to (hardwood,
cypress, palm) are the same types where its imputed per-acre values are biased low.
Re-weighting toward FIA area therefore increases the weight on the worst-biased types.

### Per-type area misallocation

Gross misallocation is 3,552,011 acres = **21.3% of FIA forest area**, even though the net
area error is only 4.7%. Worst offenders (≥100k FIA acres):

| FORTYPCD | Type | TM acres | FIA acres | %Δ |
|---|---|---|---|---|
| 999 | Nonstocked | 531 | 454,808 | −99.9 |
| 409 | Other pine / hardwood | 30,375 | 177,135 | −82.9 |
| 508 | Sweetgum / yellow-poplar | 54,614 | 126,026 | −56.7 |
| 983 | Palms | 252,805 | 546,749 | −53.8 |
| 164 | Sand pine | 310,617 | 451,445 | −31.2 |

Nonstocked is effectively absent from the FL raster (531 acres against FIA's 454,808) —
TreeMap's imputation has nowhere to put non-stocked forest land.

### Attribute bias and imputation leverage

- 12 of 41 matched types have |z| > 2 for BAA (14 for TPA, 16 for carbon): FIA's estimate
  falls outside the spread of the plots TreeMap actually imputed into that type.
- Median ESS is 3.0 (median 17 plots per type); 24 of 41 types have ESS < 5, covering
  8.2% of FIA forest area. Those per-type values are effectively a handful of plots.
- Largest residual (post-scaling) BAA bias contributions: Sweetbay/swamp tupelo (−25.6
  sq ft/ac), Slash pine (+8.9, opposite sign, largest area), Mixed upland hardwoods
  (−20.7), Palms (−46.2).
- 3 TreeMap types have no FIA area match (White oak, Swamp chestnut oak/cherrybark oak,
  Mesquite woodland) — 1,211 pixels total, negligible.

## Files

- `outputs/` — the 9 CSVs the script writes, plus `FL_state_level_scaling_comparison.csv`
  from `compare.R`.
- `baseline_2026-02/` — the pre-existing partial outputs from `/mnt/d/TreeMap_Chaz/output`
  (Sections 1–3 and an older 4.3 without the area join). Left untouched at the source.
- `analyze.R`, `compare.R` — post-hoc comparison producing the tables above. Run them from
  the same working directory as the script.
- `run_2026-08-03.console.txt` — full console transcript of the run.
- Run directory with input symlinks: `data/interim/fl_fia_treemap_comparison/` (gitignored).
