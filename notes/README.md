# Project Notes

Durable context for future agents and collaborators.

## Index

- [Management unit pilot workflow](management_units.md) — decisions, inputs, missing data, and first-notebook scope for Florida timber management units.
- [TreeMap-to-FVS workflow](treemap-fvs-workflow.md) — findings from `/mnt/d/TreeMap_Chaz`, including R script roles, duplicate status, FVS run mechanics, gotchas, and ARTEMIS integration next steps.
- [FVS 5-county smoke rerun implementation plan](fvs-smoke-rerun-plan.md) — concrete plan for generating 5–10 no-management keyfiles, running Southern variant `SN`, and summarizing `FVS_Summary2` output.
- [FVS five-county growth smoke notebook](fvs-5county-growth-smoke.md) — notebook, helper scripts, generated keyfile bundles, local `FVSsn.so` failure mode, and Windows GUI handoff instructions.
- [TreeMap COG county summary notebook](treemap-cog-county-summary.md) — remote COG/STAC raster clipping and county/state zonal summaries for Southeast states.
- [Management pipeline plan](management-pipeline-plan.md) — plan for moving from no-management FVS baseline to constrained harvest simulation using TPO targets, ownership/county constraints, and management unit scheduling.
- [Clearcut vs agriculture discrimination](clearcut-vs-agriculture-embeddings.md) — AlphaEarth embedding separability + LANDFIRE EVT change testing whether three EVT ag/grass/shrub classes (7997/9823/9585) mislabel recent clearcuts; feature-engineering table for a grassland-vs-forest model; and an embedding-similarity AOI finder (pick reference clearcuts → vector layer of similar land). Documents the GEE single-EVT-vintage constraint and the forest-vs-not bridge.
