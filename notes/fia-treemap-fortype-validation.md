# Per-forest-type validation: FIA vs TreeMap, and why mean-balancing doesn't fix it

**Run 2026-08-03.** First execution of `r/07_FL_FIA_TreeMap_comparison.R`. Full tables,
outputs, and console transcript: `research/fia_treemap_fortype/`.

## The finding

Area-scaling (mean-balancing to FIA area per FORTYPCD, after Iles 2009) fixes the *area*
term and leaves the *attribute* term alone — it does not make TreeMap agree with FIA on
per-acre values, and for BAA and carbon it makes the state-level gap slightly worse.

| Metric | FIA | TreeMap raw | TreeMap scaled |
|---|---|---|---|
| Forest acres | 16,758,136 | 15,965,986 (−4.73%) | 16,687,523 (−0.42%) |
| BAA sq ft/ac | 83.56 | 77.80 (−6.89%) | 77.42 (**−7.35%**) |
| TPA trees/ac | 481.31 | 519.48 (+7.93%) | 507.14 (+5.37%) |
| Carbon AG live tons/ac | 19.98 | 17.22 (−13.81%) | 16.88 (**−15.51%**) |

Mechanism: the types TreeMap under-allocates area to (hardwood, cypress, palm) are the
same types whose imputed per-acre values are biased low, so re-weighting toward FIA area
puts *more* weight on the worst-biased types. Scaling is not a correction for imputation
error — it only makes the area margin match.

## Supporting numbers

- **Gross area misallocation is 21.3% of FIA forest area** (3.55M acres) even though the
  net error is 4.7%. Compensating errors hide it at the state total — this is why the hole
  work's total-area validation passed while per-type attributes were never checked.
- **Nonstocked (999) is effectively missing from the FL raster**: 531 TreeMap acres vs
  454,808 FIA acres (−99.9%). TreeMap's imputation has no plot to place there. Other pine/
  hardwood (−82.9%), Sweetgum/yellow-poplar (−56.7%), Palms (−53.8%) follow.
- **Imputation leverage is high**: median ESS 3.0 across 41 matched types (median 17 plots);
  24 of 41 types have ESS < 5. Those per-type means are a handful of plots each.
- **12 of 41 types have |z| > 2 for BAA** (14 TPA, 16 carbon) — FIA's design-based estimate
  sits outside the spread of the plots TreeMap actually imputed into that type, so the
  disagreement is not explainable as within-type sampling spread.

## The script did not run as committed

`r/07_FL_FIA_TreeMap_comparison.R` had 5 defects; four are version-independent, so
Sections 4.4/4.5 (the decomposition) can never have produced output. In short: rFIA 1.1.4
removed the `variance` argument; per-type estimators return one row **per inventory year**
(17 for FL) so the `FORTYPCD`-only joins explode 17×; `library(terra)` masks `rFIA::area`;
`terra::freq(bylayer = FALSE)` returns 2 columns not 3; and rFIA returns `BA_TOTAL`/
`TREE_TOTAL`, not `BAA_TOTAL`/`TPA_TOTAL`. All five are fixed in the same commit as this
note; the defect table is in `research/fia_treemap_fortype/README.md`.

Reproducibility against the Feb 2026 partial outputs is exact on the FIA side and within
122 pixels of 71.8M on the TreeMap side (FL boundary mask redownload), so the new numbers
are comparable to what was presented earlier.

## Running it

R is not part of the Python pipeline and needs a one-time setup on this box: `units` was
built against a locally compiled udunits2 in `~/.local`, so R must run with
`LD_LIBRARY_PATH=$HOME/.local/lib`. Inputs live on `/mnt/d` (TreeMap CONUS raster, FL FIA
CSVs) and are symlinked into `data/interim/fl_fia_treemap_comparison/`; the script uses
relative paths and must be run from that directory. ~4 min end to end.

## Open

- Should the per-type decomposition drive a correction (e.g. per-type attribute
  adjustment) or only a documented uncertainty bound? Scaling alone is ruled out.
- Nonstocked and Palms are structural imputation gaps, not noise — worth deciding whether
  ARTEMIS masks them or accepts the bias.
- Same analysis has not been run for the 5-county pilot AOI, only statewide FL.
