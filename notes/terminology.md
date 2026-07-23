# ARTEMIS Terminology — plots, units, stands, and how they relate

Durable definitions so the codebase, docs, and figures use these words consistently.
The recurring confusion is calling the **693 FIA plots** "stands." They are not the same
thing. Read this before labeling a count or writing "stand" anywhere.

## The three core concepts

### FIA plot
A **physical forest-inventory sample point** measured by the USFS Forest Inventory and
Analysis program, identified by a control number `PLT_CN`. It carries a measured tree list
(species, diameter, height, per-acre expansion factors). It is *data*, not a place on our
map — the source of growth information.

- TreeMap imputes **one FIA plot per 30 m pixel** (nearest-neighbor imputation).
- Across the five-county pilot there are **693 unique FIA plots** (`PLT_CN`), each imputed
  to many pixels. Mean footprint ~1,580 ac, **median ~107 ac** (heavily skewed — a few
  dominant pine plots cover 30,000+ ac each). ~4.9–5.4M pixels ≈ **~1.1M forested acres**.
- Plots may come from outside Florida — TreeMap draws donors from a multi-state region.
- Code: `PLT_CN` (keep as **string**, 15 digits, never round). Crosswalk `Value` (TM_ID) →
  `PLT_CN` in `FL_5county_TreeMap_TMIDs.csv`.

### Management unit
A **delineated forest polygon** — the operational **decision unit** the harvest scheduler
acts on. Built by `pipeline/s3_management/sketch_management_units.py` (parcels ∩ forest −
road/water/BMP), then cleaned to runnable size by `sliver_merge.py`.

- This is what we call a **"stand" for modeling purposes.**
- There are **thousands per county** (Union County alone: 17,020 raw draft polygons →
  **2,442** clean units after sliver-merge). Far finer than the 693 plots.
- Each management unit is initialized from an **area-weighted mix of the FIA plots** its
  pixels fall on (see "area-weighting" below), not from a single plot.
- Code id: `MU_ID` / `unit_id`; FVS `STAND_ID = "MU_<MU_ID>"` in `build_fvs_inputs.py`.

### FVS stand
The **simulation unit FVS runs on** — one growth trajectory per stand. "Stand" is FVS's own
term for a simulation unit and is agnostic to what it physically represents.

- **Target design:** one FVS stand **= one management unit**, initialized from the
  area-weighted plot mix.
- **Current no-management baseline:** FVS stand **= one FIA plot** (`stand_cn == PLT_CN`,
  693 of them — a plot-level baseline, `notes/fvs-to-raster-painting.md`). This is a
  simplification, not the target. When the management pipeline lands, the FVS-stand count
  moves from **693 (plots)** to **thousands (management units)**.
- The restart-fidelity spikes (`notes/restart-fidelity-findings.md`) used **plot-sized FVS
  stands** as fixtures; "5 stands" there means 5 FVS simulation units, not 5 management units.

## How they relate (data flow)

```
30 m pixel ──imputed by TreeMap──▶ 1 FIA plot (PLT_CN)     [693 unique across pilot]
                                        │
 management unit polygon ── covers many pixels ── each pixel carries its plot ──┐
                                        │                                        │
                                        ▼                                        │
             area-weighted mix of the FIA plots inside the unit  ◀──────────────┘
                                        │
                                        ▼
                              one FVS stand (target)  ──▶ one growth trajectory
```

## Area-weighting (plots → unit)

A management unit spanning several FIA plots takes each plot's per-acre values weighted by
the plot's **area share** of the unit (equivalently, its pixel-count share, since 30 m
pixels are equal-area in EPSG:5070). Example: a 40-ac unit = plot A (15 ac) + plot B (25 ac)
→ `unit_TPA = 0.375·TPA_A + 0.625·TPA_B`.

- Weights: `assign_plt_cn.py` → `WEIGHT = CELL_COUNT / TOTAL_CELLS` (LETO `assign_plt_cn`).
- Applied: `build_fvs_inputs.py::build_tree_init` scales each donor tree's TPA by its plot
  weight (with a LETO 5% floor + renormalize). This is the LETO procedure; see the
  validation trace in this file's companion discussion and the two modules' docstrings.
- **Per-acre vs totals:** per-acre densities (BA, TPA) carry to any pixel unchanged;
  **totals** require `× pixel acres` (30 m pixel = 900 m² = 0.2224 ac). Never sum raw pixel
  values for a total. Pixel-count means *are* already area-weighted. See
  `notes/treemap-methodology.md`.

## Supporting terms

- **TreeMap / TM_ID** — a raster where each pixel's value (`TM_ID`, crosswalk `Value`) is the
  ID of the FIA plot imputed to it. `notes/treemap-methodology.md`.
- **Sliver** — a management-unit polygon below the minimum operational size (**5 ac ≈ 2 ha**);
  87% of raw Union County polygons. **Sliver-merge** dissolves each into its best neighbor
  (`sliver_merge.py`), conserving area.
- **BMP erase** — cutting the required no-harvest buffers (Florida BMP: 35–75 ft around
  streams/water/roads) out of the units; this fragmentation is what *creates* slivers.
- **State-zero** — the clean management-unit map at year 0, before any simulated harvest.
- **Size classes** (maps): sliver `<2 ha`, candidate/unit `2–40 ha`, large `>40 ha`.
- **Stand metrics** — **BA** basal area (trunk cross-section per acre, sq ft/ac); **TPA**
  trees per acre; **SDI** stand density index; **QMD** quadratic mean diameter. These are the
  "stand values" the restart-fidelity work proved exact across pause/restart.
- **EPSG:5070** — CONUS Albers Equal-Area, the project CRS; equal-area so acres/hectares are
  correct.

## Usage rules (do this going forward)

1. **Never label the 693 as "stands" or "management units."** They are **FIA plots**.
   Say "693 unique FIA plots" (or "693 FVS runs" for the baseline workload).
2. Use **"management unit"** (or "stand" only in the explicit modeling sense) for the
   delineated polygons — thousands per county.
3. Use **"FVS stand"** for the simulation unit, and state which it maps to (plot in the
   baseline, management unit in the target) when it matters.
4. Keep `PLT_CN`, `CN`, `Stand_CN`, `STAND_ID`, `MU_ID` as **strings**.
5. Per-acre values paint directly; **totals need × pixel acres**.

## See also

- `notes/treemap-methodology.md` — imputation, per-acre vs area totals.
- `notes/fvs-to-raster-painting.md` — `stand_cn == PLT_CN` (plot-level baseline).
- `notes/management-pipeline-plan.md` — units, crosswalk, area-weighted aggregation.
- `notes/restart-fidelity-findings.md` — FVS-stand = simulation unit (plot-sized fixtures).
- Code: `pipeline/s3_management/assign_plt_cn.py`, `pipeline/s4_fvs/build_fvs_inputs.py`
  (area-weighting); `pipeline/s4_fvs/paint_fvs_to_raster.py` (plot-level painting).
