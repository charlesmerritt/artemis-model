# Project Notes

Durable context for future agents and collaborators.

## Start here

- [Trajectory libraries and simulated-annealing scheduling](trajectory-library-and-annealing.md)
  — **the architecture of record (adopted 2026-08-06).** One library of candidate
  trajectories per stand, contents determined by ownership class; FVS runs once per
  `(stand, prescription)` offline with no restart barriers; the harvest scheduler then
  selects one trajectory per stand by simulated annealing. Covers the ownership → eligible
  prescription mapping, library schema, the SA formulation and its required quality
  reporting, and what it supersedes. Documentation is ahead of implementation: nothing in
  `pipeline/` implements the annealer yet.
- [Guiding references](../docs/references/README.md) — the two papers this work follows,
  **`LAMPS`** (Bettinger & Lennette et al.) and **`CLIMATE-FVS`** (GMUG 2015). Neither PDF
  is committed yet; the README records why and where they go.

## Index

- [Terminology](terminology.md) — **read first for vocabulary.** Distinguishes the three concepts that get conflated: **FIA plot** (inventory sample, one imputed per pixel, 693 unique in the pilot), **management unit** (delineated decision polygon, our modeling "stand", thousands per county), and **FVS stand** (simulation unit — = plot in the baseline, = management unit in the target). Plus area-weighting rules and the "693 is plots, not stands" convention.
- [FVS restart fidelity findings](restart-fidelity-findings.md) — **measured**: in-process pause reproduces a continuous run exactly (0.0 delta incl. carbon), but `--restart` silently collapses `Forest_Shrub_Herb` to 0.02 and understates total stand carbon ~8% per barrier while BA/Tpa/SDI stay bit-identical. `putstd` omits the FFE commons; `COVTYP` is the likely culprit. Also: a negative FVS restart code is a *signal* — `fvsRun()` must be called again or the store file is empty.
- [Notebooks index](notebooks.md) — what every notebook + helper in `notebooks/` does, what each needs to run (GEE / `/mnt/d` drive / network), links to the per-group deep-dive notes, and the 2026-07-14 test results (incl. the broken FVS notebook and the stored-error prototype).
- [Management unit pilot workflow](management_units.md) — decisions, inputs, missing data, and first-notebook scope for Florida timber management units.
- [TreeMap-to-FVS workflow](treemap-fvs-workflow.md) — findings from `/mnt/d/TreeMap_Chaz`, including R script roles, duplicate status, FVS run mechanics, gotchas, and ARTEMIS integration next steps.
- [FVS 5-county smoke rerun implementation plan](fvs-smoke-rerun-plan.md) — concrete plan for generating 5–10 no-management keyfiles, running Southern variant `SN`, and summarizing `FVS_Summary2` output.
- [FVS five-county growth smoke notebook](fvs-5county-growth-smoke.md) — notebook, helper scripts, generated keyfile bundles, local `FVSsn.so` failure mode, and Windows GUI handoff instructions.
- [TreeMap COG county summary notebook](treemap-cog-county-summary.md) — remote COG/STAC raster clipping and county/state zonal summaries for Southeast states.
- [Management pipeline plan](management-pipeline-plan.md) — the phase-by-phase build plan from the no-management FVS baseline to a scheduled landscape: TPO targets and ownership/county constraints (Phases 1–2, unchanged), then per-stand trajectory libraries and the simulated-annealing scheduler (Phases 3–5, rewritten around the adopted architecture).
- [Painting FVS outputs to TreeMap rasters](fvs-to-raster-painting.md) — `pipeline/s4_fvs/paint_fvs_to_raster.py`: swap TM_ID pixels for FVS values via TM_ID→PLT_CN→stand_cn; TreeMap 2022 vs 2020 version trap and snapshot-keying gotcha (initial=`years_since_start==0`, final=`calendar_year==2076`).
- [How TreeMap works](treemap-methodology.md) — TreeMap = imputed FIA plot-ID raster (modified Random Forest, one plot per pixel); stores per-acre densities not per-pixel totals; `Count` field enables area expansion; pixel sums ≠ FIA design-based estimates. Grounds the per-acre vs area-weighting question.
- [Claude Code on the web session setup](claude-code-web-environment.md) — the two provisioning layers (`.claude/environment-setup.sh` at image build vs `.claude/hooks/session-start.sh` per session) and the egress denials behind both. The environment setup script died at line 1 because `rclone.org` is blocked (and `sudo` strips the proxy, and uv was already in the image); `INSTALL sqlite` cannot reach `extensions.duckdb.org`, failing all nine `tests/test_restart_fidelity.py` tests in the container but not locally. **Open blocker: `r2.cloudflarestorage.com` is denied by the egress policy, so R2 data pulls cannot work until an admin allowlists it.**
- [DuckDB iterative-coupling cells](duckdb-iterative-coupling-cells.md) — the DuckDB view vocabulary over raw FVS output (`fvs_cycle_change`, `fvs_removals`, `fvs_removal_summary`, `fvs_cycle_ledger`, `fvs_spatial_crosswalk`, `fvs_raster_ready`). The views carry over as how `trajectory_cycles` gets built; only the iterative coupling loop they were written to serve has been replaced.
- [Clearcut vs agriculture discrimination](clearcut-vs-agriculture-embeddings.md) — AlphaEarth embedding separability + LANDFIRE EVT change testing whether three EVT ag/grass/shrub classes (7997/9823/9585) mislabel recent clearcuts; feature-engineering table for a grassland-vs-forest model; and an embedding-similarity AOI finder (pick reference clearcuts → vector layer of similar land). Documents the GEE single-EVT-vintage constraint and the forest-vs-not bridge.
