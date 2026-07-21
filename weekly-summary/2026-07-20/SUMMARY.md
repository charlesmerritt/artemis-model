# ARTEMIS — Weekly Sync Summary (Jul 13–20, 2026)

Prepared for the Monday 9:00 AM sync. Covers all branches, not just `main`
(`main`, `claude-code/parallel-fvs-runs`, `mgmt-units-research`; PRs #2, #3, #5).

A styled, self-contained briefing (with the maps) is in `briefing.html` in this
folder. This markdown is the plain-text record.

> **Two dictated terms, interpreted (please confirm at the meeting):**
> - **"Leto scripts"** — no literal match in the repo. Read as the **management-unit
>   delineation code**: `pipeline/s3_management/sketch_management_units.py` +
>   `research/mgmt_units/segmentation_delineation.py`.
> - **"lamps" = a PDF reference (LAMPS)** — a document, not the in-repo comparison. It is
>   NOT reachable in this build sandbox: the external data drive (`/mnt/d`) is not mounted
>   here and no `LAMPS` string exists anywhere in the tree, so its content is not folded in
>   yet. **Need the path/filename to integrate it.** Likely LAMPS = *Landscape Management
>   Policy Simulator*, the peer system for the harvest-scheduling comparison.

## At a glance

- **Flagship output shipped (PR #5):** FVS-projected basal-area rasters for the full
  five-county pilot. Mean basal area **83 → 189 sq ft/ac** over a ~50-yr no-management
  horizon; 100% stand and pixel coverage.
- **Parallel-FVS architecture decided:** the restart-fidelity spike settled how to run FVS
  at scale. In-process pause reproduces a continuous run **exactly**; stop/restart is exact
  on stand values but corrupts carbon — carbon set out of scope, orchestrator unblocked.
- **Management-unit engine ("Leto") rebuilt & tested** (14/14). The segmentation comparison
  that fixes the sliver problem is **coded but not yet run**.
- **Infra hardened:** DuckDB adopted as the data layer; CI + ruff added (17 lint errors
  cleared); docs refreshed (PR #2).

## Workstreams

### 1. FVS restart-fidelity spike & parallel orchestration — `parallel-fvs-runs` (PR #3) — COMPLETE
Four-arm experiment against real FVS runs (continuous / in-process pause / stop-restart /
tree-list rebuild).
- In-process pause **== continuous, exactly** — both candidate architectures survive.
- Stop/restart **exact on stand values**, but **corrupts carbon** (FFE `FLIVE` reset on
  restore — not the originally-hypothesized common-block gap). `config: carbon_extension:false`.
- **Management-injection gate PASSED** — per-stand selective cut faithful and restart-safe.
- Orchestrator sketched: **bundle-per-ownership, even-flow**. DuckDB adopted as data layer.

### 2. Management units — the "Leto" delineation scripts — ENGINE DONE, MERGE + COMPARISON OWED
- Naive engine (parcel ∩ LANDFIRE-forest, minus BMP stream buffers / waterbodies / road
  artifacts) reconstructed from a deleted state — **14/14 tests pass**.
- **Union County baseline persisted:** 17,020 candidate polygons.
- **Owed #1 — run the segmentation comparison** (Felzenszwalb + SLIC, Polsby-Popper) — coded,
  **never run**.
- **Owed #2 — merge / eliminate the slivers.** Segmentation reduces fragmentation but does
  not remove it; the 14,852 sub-2 ha slivers must be dissolved or merged to best neighbour so
  each unit is a single runnable stand. Not yet built — on the harvest-scheduling critical path.

### 3. FVS → raster painting — flagship product — `s4_fvs` (PR #5, merged) — SHIPPED
- FVS trajectory: 9,259 rows · 693 stands · 1997–2076.
- Auto-selected `treemap2022` pairing: 693/693 stands (100%), 5.41M/5.41M pixels matched.
- Mean basal area 83 → 189 sq ft/ac. Deterministic, tested (4/4).

### 4. Data layer, CI & docs — `main` (PR #2) — LANDED
- DuckDB as data/aggregation layer; CI + ruff; data-drive tests skip when drive absent;
  README/notes refreshed; large-file pre-commit guard.

## The management layer — five-county maps

- `basal_area_5county.jpg` — FVS basal-area projection, year 0 vs. 2076 (from
  `weekly-artifact/2026-07-19/`). Rendered from the committed rasters.
- `mgmt_units_sizedist.png` — **the sliver explosion**: naive intersection shatters Union
  County into 17,020 polygons; **87% are <2 ha slivers holding only 14% of forest area**.
  This is exactly what segmentation is meant to fix.

Note: the unit *polygons* live on the external data drive and are not in the repo, so the
spatial map shown is the FVS raster the units feed, paired with the persisted size
distribution.

## The comparison — naive vs. segmentation (the "lamps" comparison)

| Strategy | Approach | Polygons | Sliver frac. | Median unit | Status |
|---|---|---:|---:|---:|---|
| Naive | parcel ∩ forest, erase buffers | 17,020 | 87% | by class † | ✓ run (Union Co.) |
| Felzenszwalb | graph segmentation (scale 100) | pending | pending | target 5–10 ha | coded, not run |
| SLIC | superpixels (n=1000) | pending | pending | target 5–10 ha | coded, not run |
| Hybrid | segmentation ∩ parcels / sliver merge | — | — | — | proposed |

† Naive median by class: slivers 0.09 ha · candidate (2–40 ha) 5.7 ha · large (>40 ha)
55.8 ha. Planned axes: polygon count, sliver fraction, median/mean area, Polsby-Popper
compactness, forest-area retention, and downstream **FVS workload** (# unique unit × regime
runs). Spine of the intended methods paper (`research/mgmt_units/BRIEF.md`).

## The destination — harvest scheduling (the priority)

Everything above serves a spatially explicit, **constrained harvest schedule** for the five
counties: FVS no-management baseline as standing inventory, TPO reports as volume caps,
ownership/county as constraint dimensions, then managed FVS per unit. Roadmap:
`notes/management-pipeline-plan.md`.

**Already in hand:** FVS baseline (693 stands, done) · TreeMap↔FVS linkage (688 rows) ·
TPO caps by county (Baker 11.8M, Columbia 17.8M, Hamilton 15.3M, Suwannee 18.5M, Union 8.7M
cuft/yr) and owner (Private 66.3M, Public 5.7M, All 72.0M) · ownership raster (Harris 2025).

**Phases:**
1. Inventory + TPO constraints — baseline done; TPO parse + inventory-by-dimension owed. *(no new FVS run)*
2. Management units + ownership + crosswalk — **gating**; includes sliver resolution + unit×FVS-stand crosswalk.
3. Regime library — 4–6 FVS keyword templates + default assignment by ownership × forest type. *(no new FVS run)*
4. Constrained scheduler + managed FVS — **core deliverable**: per 5-yr cycle pick units by
   regime + oldest-age priority, hold within TPO caps, render managed keyfiles, run managed FVS.
5. Iteration + scaling — constraint sensitivity; trajectory-library approach for statewide.

Critical path to a first end-to-end schedule is **Phase 2 → 4**, and Phase 2 can't close
until the slivers are resolved.

## Open questions to raise

1. **Does segmentation beat the sliver problem, and at what parameters?** Comparison never
   executed; params are guesses aimed at ~5–10 ha median. (`research/mgmt_units/`)
2. **Sliver policy — merge-to-best-neighbour or dissolve? (critical path).** 14,852 sub-2 ha
   fragments can't each be an FVS stand, and Phase 2 can't close until they're resolved.
   Segmentation alone won't do it — need an explicit merge rule (adjacency / ownership /
   forest type?). Also: which silvicultural regimes and TPO constraint mode (independent vs.
   joint caps)?
3. **Do we accept dropping carbon?** Restart corrupts FFE carbon; stand values stay exact.
   Fine for growth/volume — carbon deliverable would need continuous runs.
   (`notes/restart-fidelity-findings.md`)
4. **Statewide FVS workload & runtime?** Unit count bounds FVS runs; need per-strategy
   estimate + chunking plan before leaving the pilot. (`BRIEF.md §7`)
5. **Which validation reference?** FIA condition sizes, BIGMAP cross-check, or FIA-
   remeasurement hindcast — rubric needs one committed axis. (`PLAN.md`, `BRIEF.md §6`)
6. **Enrich the segmentation stack?** Currently EVT + binary forest mask; production wants
   TreeMap plot-ID, ownership, terrain — gated on external-drive access.
7. **Python 3.14 pin vs. environment reality.** Repo requires 3.14 (couldn't install in
   every sandbox); painter ran fine on 3.13. Keep the pin or relax it?
