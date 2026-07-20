# Project Notes

Durable context for future agents and collaborators.

## Index

- [FVS restart fidelity findings](restart-fidelity-findings.md) — **measured**: in-process pause reproduces a continuous run exactly (0.0 delta incl. carbon), but `--restart` silently collapses `Forest_Shrub_Herb` to 0.02 and understates total stand carbon ~8% per barrier while BA/Tpa/SDI stay bit-identical. `putstd` omits the FFE commons; `COVTYP` is the likely culprit. Also: a negative FVS restart code is a *signal* — `fvsRun()` must be called again or the store file is empty.
- [Notebooks index](notebooks.md) — what every notebook + helper in `notebooks/` does, what each needs to run (GEE / `/mnt/d` drive / network), links to the per-group deep-dive notes, and the 2026-07-14 test results (incl. the broken FVS notebook and the stored-error prototype).
- [Management unit pilot workflow](management_units.md) — decisions, inputs, missing data, and first-notebook scope for Florida timber management units.
- [TreeMap-to-FVS workflow](treemap-fvs-workflow.md) — findings from `/mnt/d/TreeMap_Chaz`, including R script roles, duplicate status, FVS run mechanics, gotchas, and ARTEMIS integration next steps.
- [FVS 5-county smoke rerun implementation plan](fvs-smoke-rerun-plan.md) — concrete plan for generating 5–10 no-management keyfiles, running Southern variant `SN`, and summarizing `FVS_Summary2` output.
- [FVS five-county growth smoke notebook](fvs-5county-growth-smoke.md) — notebook, helper scripts, generated keyfile bundles, local `FVSsn.so` failure mode, and Windows GUI handoff instructions.
- [TreeMap COG county summary notebook](treemap-cog-county-summary.md) — remote COG/STAC raster clipping and county/state zonal summaries for Southeast states.
- [Management pipeline plan](management-pipeline-plan.md) — plan for moving from no-management FVS baseline to constrained harvest simulation using TPO targets, ownership/county constraints, and management unit scheduling.
- [Painting FVS outputs to TreeMap rasters](fvs-to-raster-painting.md) — `pipeline/s4_fvs/paint_fvs_to_raster.py`: swap TM_ID pixels for FVS values via TM_ID→PLT_CN→stand_cn; TreeMap 2022 vs 2020 version trap and snapshot-keying gotcha (initial=`years_since_start==0`, final=`calendar_year==2076`).
- [How TreeMap works](treemap-methodology.md) — TreeMap = imputed FIA plot-ID raster (modified Random Forest, one plot per pixel); stores per-acre densities not per-pixel totals; `Count` field enables area expansion; pixel sums ≠ FIA design-based estimates. Grounds the per-acre vs area-weighting question.
- [Clearcut vs agriculture discrimination](clearcut-vs-agriculture-embeddings.md) — AlphaEarth embedding separability + LANDFIRE EVT change testing whether three EVT ag/grass/shrub classes (7997/9823/9585) mislabel recent clearcuts; feature-engineering table for a grassland-vs-forest model; and an embedding-similarity AOI finder (pick reference clearcuts → vector layer of similar land). Documents the GEE single-EVT-vintage constraint and the forest-vs-not bridge.
