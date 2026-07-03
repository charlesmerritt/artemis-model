# How TreeMap Works (and how it handles per-acre attributes)

Research summary grounding the ARTEMIS↔TreeMap linkage. Primary source: the
on-disk TreeMap 2022 data publication (FGDC metadata + data dictionary,
`/mnt/d/TreeMap-2022/`, `/mnt/d/TreeMap-2022_Metadata_Fileindex/`). Methodology
detail from Riley et al. 2021 (Scientific Data, TreeMap 2014).

## Product

Houtman, R.M.; Leatherman, L.S.T.; Zimmer, S.N.; Housman, I.W.; Shrestha, A.;
Shaw, J.D.; Riley, K.L. 2025. **TreeMap 2022 CONUS** (circa 2022).
Forest Service Research Data Archive, RDS-2025-0032. doi:10.2737/RDS-2025-0032
(orig. 2025-06-25; updated 2026-05-11 — STANDHT/CANOPYPCT changed from binned to
continuous).

## What TreeMap actually is

A 30 m raster where each forested pixel holds an **imputed FIA plot identifier**
(`TM_ID`, linkable to `PLT_CN`). It is a map of *which plot best matches here*,
not a map of measured values. Everything else (basal area, volume, carbon, the
tree list) is carried over from the FIA plot that was imputed.

## How the imputation is built

- Predictors ("target rasters"), 30 m: LANDFIRE Existing Vegetation Cover /
  Height / Type, disturbance (fire, insect/disease) + time since; topography
  (slope, aspect, elevation); location (lat/lon); Daymet 1981–2010 climate
  normals (tmin, tmax, prcp, vp, srad, swe) + VPD.
- Reference: single-condition forested FIA plots (FIADB v1.9.1; **true**
  unfuzzed coordinates obtained via Memorandum of Cooperation), 1997–2022.
- Algorithm: **modified Random Forest imputation** via R `yaImpute` (Crookston &
  Finco lineage; Riley et al. 2016/2021). ~249 trees per LANDFIRE zone (83 per
  response var). Each pixel gets the **single** plot that co-occurs with it most
  often in the RF terminal nodes — k=1, one whole plot per pixel.
- 2022-specific changes vs 2016: Daymet climate suite; plots eligible for
  imputation in a LANDFIRE zone restricted to species present in that zone or
  neighbors (removes out-of-range species).

## The per-acre answer (the point of this note)

TreeMap stores **per-acre densities**, never per-pixel totals — exactly the same
pattern as the FVS values we paint. From the data dictionary
(`_variable_descriptions.csv`), VAT attribute units:

| attribute | units |
|---|---|
| BALIVE | **square feet/acre** |
| TPA_LIVE / TPA_DEAD | **count/acre** |
| VOLCFNET_L/_D | **cubic feet/acre** |
| VOLBFNET_L | **board feet/acre** |
| DRYBIO_L/_D, CARBON_L/_D/_DWN | **tons/acre** |
| QMD, STANDHT, CANOPYPCT, ALSTK, GSSTK | intensive (avg/percent, not per-area) |

These come from FIA's plot design: each tree record carries `TPA_UNADJ` = "trees
per acre that the sample tree theoretically represents based on the sample
design" (e.g. 6.018 for a standard fixed-radius subplot tree). Plot per-acre
values are `Σ over trees (tree quantity × TPA_UNADJ)`. A density is independent
of pixel size, so the same per-acre value applies to a 30 m pixel as to an acre.

## How TreeMap supports area totals — and the caveat

The raster VAT includes a **`Count`** field = number of pixels a plot was
imputed to. That is the hook for area expansion: `acres ≈ Count × 0.2224`
(a 30 m pixel = 900 m² = 0.2224 ac), and a regional total = `Σ per-acre × acres`.

Caveat (important for ARTEMIS aggregation): **pixel-count area expansion is NOT
the FIA design-based population estimator.** FIA's official totals/areas use plot
expansion factors (`EXPNS`, acres represented per plot, from POP_STRATUM) and
come with variance. TreeMap pixel sums can diverge from FIA design-based
estimates via (a) area misallocation — imputation may paint a plot to more/fewer
pixels than its design weight — and (b) within-forest-type attribute bias.
TreeMap is validated for **mapping/landscape modeling**, not as a substitute for
design-based estimates. This is exactly what the repo QA script
`1_FL_FIA_TreeMap_comparison.R` checks (see [[treemap-fvs-workflow]]): TreeMap
pixel-weighted vs rFIA design-based, decomposed into area vs attribute bias, with
effective sample size to detect over-concentrated imputed plots.

## Validation (from the circa-2014 methods paper, Riley et al. 2021)

Method-family accuracy, "≥1 pixel within plot radius matches": EVH height 85.7%,
EVC cover 44.0%, top-2 basal-area species 76.7%; pixel-to-target agreement EVC
97.2% / EVH 99.2% / EVG 93.0%; disturbance overall 90.3% but fire user's acc
~64% and insect/disease ~1% (few disturbed training plots). *Needs verification*
whether TreeMap 2022 was independently re-validated; its metadata cites prior
versions' validation plus the species-range fix.

## Implication for our FVS→raster painting

We are doing the same thing TreeMap does: carry a plot's per-acre attributes to
every pixel imputed to that plot. So the same rules apply — per-pixel values are
fine for maps, quantiles, and pixel-wise change; **totals require × pixel acres**;
and pixel-based totals are map approximations, not FIA design-based estimates.
See [[fvs-to-raster-painting]].

## Key references (real, from metadata citation block)

- Riley, K.L.; Grenfell, I.C.; Finney, M.A.; Wiener, J.M. 2021. TreeMap, a
  tree-level model of conterminous US forests circa 2014… *Scientific Data*.
  doi:10.1038/s41597-020-00782-x
- Riley, K.L.; et al. 2021. TreeMap 2016. research.fs.usda.gov/treesearch/65597
- Riley, K.L.; Grenfell, I.C.; et al. 2016. Mapping forest vegetation for the
  western US using modified Random Forest imputation of FIA plots.
- Riley, K.L.; et al. 2022. *Journal of Forestry*. doi:10.1093/jofore/fvac022
- Burrill et al. 2024 (FIADB v9.3 user guide); Thornton et al. 2022 (Daymet v4 R1).
