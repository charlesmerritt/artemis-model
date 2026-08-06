# Methodology Directions (advisor meeting, 2026-07-27)

Four methodological questions raised with the adviser. These are **directions and
open decisions**, not measured results and not yet implemented. Each item records
the question, the options, the trade-offs, and what in the current code would have
to change.

> **Resolved by the adopted architecture (2026-08-06).** Items 1 and 4 are settled by
> [`trajectory-library-and-annealing.md`](trajectory-library-and-annealing.md): stands get a
> library of candidate trajectories determined by ownership class, and the harvest scheduler
> selects among them by simulated annealing. Item 2 (riparian no-entry) survives unchanged
> and is *strengthened* — it is now enforced structurally rather than as a rule the
> scheduler could weigh. Item 3 (hex-bin cartography) is untouched. The resolutions are
> marked inline below; the original reasoning is kept because it is what justifies them.

---

## Pipeline sketch from the meeting

Transcribed from the whiteboard-style sketch on the 7/27/26 notepad page. Some
handwriting is ambiguous; uncertain readings are marked.

```text
  [Get Veg data (FIA)] ──┐
                         ├──> [Decide Sim Units] ──┐
  [Repair TreeMap] ──┐   │                         │
                     ├──────> (Repaired TreeMap  <─┘
  [Repair NWOS       │           + Repaired LO)
   LO db] ───────────┘            │        │
        │                         v        │
        │                  (Simulate Mgt) ──> CC
        └──────────────────> (Riparian) ────> outputs / summaries
```

Readings and expansions:

- **"Get Veg data"** — the box is circled, i.e. the starting/blocking step. The
  subscript reads `FIA` (possibly `RIA`); FIA is the sensible expansion.
- **"Decide Sim Units"** — deciding the simulation unit is drawn as its own upstream
  gate, which is exactly item 1 below. It feeds the repaired-data stage rather than
  the other way around, so the tree-list aggregation question blocks work downstream
  of it.
- **"Repair NWOS LO db"** — NWOS = National Woodland Owner Survey; LO = landowner.
  Distinct from the Harris et al. ownership raster already in `config/data_paths.yaml`,
  which is a pixel-level ownership *class* product, not a landowner database.
- **"Riparian"** — drawn as a branch off the repaired-data stage that runs **parallel
  to `Simulate Mgt`, never through it**, and still lands in the outputs. That is the
  same-shape statement as item 2: grown, never managed, separately reported.
- **"CC"** — trailing off `Simulate Mgt`, most likely clearcut as the first regime to
  simulate. Not certain.

The upper half of the page is an unrelated personal to-do list; not transcribed. The
photo itself is not committed — say the word if you want it in `notes/assets/`.

---

## 1. Tree-list aggregation: one averaged list per unit, or weighted per-plot lists?

**Question.** When a management unit spans many TreeMap pixels imputed to different
FIA plots, do we (A) collapse them into a single averaged tree list per unit and run
FVS once per unit, or (B) keep each underlying plot's tree list intact, run FVS per
plot, and weight the results by the area each plot occupies inside the unit?

**Where this bites today.** The 5-county pilot has ~5.41M TreeMap pixels resolving to
**693 unique plots** (see [[fvs-to-raster-painting]]). Management units come from
`sketch_management_units.py` and are parcel-derived, so a single unit routinely
overlaps several plots. [[management-pipeline-plan]] Step 2.3 already specifies the
crosswalk this needs: `unit_id → stand_cn, stand_id, pixel_acres_in_unit`, and
decision 3 there already uses area weighting for unit stand age
(`unit_age = Σ(age_i × acres_i) / Σ acres_i`).

**Option A — average to one list per unit.**
- One FVS run per unit; unit-level output needs no re-aggregation.
- Loses the within-unit diameter distribution and species mixture. Growth, mortality,
  and volume/carbon equations are non-linear in DBH, so growing the mean tree is not
  the same as the mean of grown trees — an averaged list is biased, not just coarser.
- An averaged list is no longer a real FIA tree list: `TPA_UNADJ` expansion, species
  records, and per-acre semantics ([[treemap-methodology]]) all have to be
  reconstructed by hand, and the result cannot be checked against FIA.
- Run count scales with the number of units, which is large (Union County alone
  produced 17,020 candidate polygons before splitting).

**Option B — keep plots separate, weight by area (adviser's spatial-accuracy point).**
- Preserves within-unit heterogeneity; every FVS run is initialized from a real,
  unmodified FIA tree list.
- Run count scales with **unique `(plot, regime, site-index bin)`**, not with unit
  count — which is the trajectory-library design already in `PLAN.md` §4c and the
  only thing that scales past the pilot.
- Unit-level results become area-weighted sums/means over constituent plots. Per-acre
  densities average with acre weights; totals multiply by pixel acres first.
- Cost: a prescription written for a unit has to be translated onto each constituent
  plot. Whole-unit treatments (clearcut, plantation rotation) translate cleanly.
  Treatments defined by a **unit-level residual target** (e.g. "thin to 60 sq ft/ac
  across the unit") do not have a unique translation — we need a stated rule for
  distributing removals across constituent plots.

**RESOLVED (2026-08-06): weighted union — neither option as originally framed.**

The dichotomy above was false. `build_fvs_inputs.py::build_tree_init` already builds a
**weighted union**: every donor tree record from every constituent plot is kept intact and
its `TPA` expansion factor is scaled by that plot's area share of the unit (LETO 5% floor,
then renormalize). That has Option B's biophysics — no trees are averaged, so the diameter
distribution and species mixture survive, and the "growing the mean tree ≠ mean of grown
trees" bias does not apply — with Option A's run structure, one FVS stand per unit and no
re-aggregation of results.

This is what makes "one trajectory library per stand" well-defined: the library is keyed by
`(management unit, prescription)`, and the unit *is* the FVS stand.

Two consequences:

- **The distribution rule dissolves.** The open question below — how to spread a unit-level
  residual target across separate per-plot runs — has no content once there is one
  composite list per unit. The `ThinDBH` proportion applies to the unit's own list.
- **The composite is one competitive arena.** FVS computes density-dependent mortality and
  diameter growth over the pooled list, so a tree donated by plot A competes with a tree
  donated by plot B as though co-located. For a delineated management unit that is the
  intended reading, but it is a modeling assumption, not neutral bookkeeping. State it in
  the methods writeup.

**Still to decide.**
- Whether unit-level reporting is per forested acre in the unit or per unit acre.
- Whether tiny plot slivers inside a unit (a plot contributing a handful of pixels) get
  dropped below some area threshold. Note this is now a *fidelity* question about the
  composite tree list, not a run-count question — run count is `units × prescriptions` and
  does not depend on how many plots a unit draws from.

---

## 2. Riparian buffers must be their own stands — excluded from management, still grown

**The regime, as stated:**

> These lands grow freely and are never harvested, but should be included in our
> growth outputs and summaries as unique buffer polygons.

Three requirements fall out of that sentence, and all three have to hold together:

1. **Grow freely** — buffers are projected through FVS on the same cycles as
   everything else. They are not frozen, not held at year-0 condition, and not
   approximated by a growth curve outside FVS.
2. **Never harvested** — no entry of any kind. Not a light thin, not a salvage
   entry, no buffer class exempted. The regime is unconditional, so it is assigned
   by geometry (does this land fall in a buffer?) rather than by any
   ownership/forest-type rule that could override it.
3. **Reported as unique buffer polygons** — they appear in growth outputs and
   summaries with their **own polygon identity**: their own unit IDs, their own rows
   in summary tables, their own painted pixels. They are not dissolved into
   neighboring managed units, not merged into a single landscape-wide "buffer"
   aggregate, and not reported only as a residual.

Requirement 3 is the one that is easy to half-satisfy. Buffers that are grown but
folded into unit-level totals technically appear in the outputs, yet you can no
longer answer "how much volume/carbon is sitting in riparian buffers, and where?" —
which is the question the buffers exist to support. Keep them addressable.

**Gap in current code.** `pipeline/s3_management/sketch_management_units.py`
currently *erases* buffers: stream BMP buffers, NHD waterbodies, and the small road
buffer are unioned into one erase layer and differenced out of the forested parcels
(`gpd.overlay(..., how="difference")`). The buffer area is discarded, so today those
acres would be neither managed nor grown — they are simply absent from the projected
landscape. That is an under-count of standing volume and carbon, not a conservative
choice.

**Change needed.**
- Keep stream/BMP buffer polygons as a retained layer instead of erase-only, and emit
  them as units with `unit_class = "riparian"` (managed units keep
  `unit_class = "managed"`).
- Apply the same LANDFIRE EVT forest mask to buffer polygons — only the forested part
  of a buffer becomes a growing stand.
- Erase-then-add, so managed and riparian units **partition** the *eligible* forest
  area with no overlap. Add an area-accounting check, stated against the
  post-exclusion area:

      Σ managed + Σ riparian == (forest mask ∩ parcels) − (waterbodies ∪ road buffer)

  The raw forested AOI is the wrong right-hand side. `sketch_management_units.py`
  builds the forested AOI *before* subtracting the erase layers, and the road-artifact
  buffer routinely overlaps forested pixels (roads run through forest); NHD waterbody
  polygons can clip forest-mask pixels too at 30 m. Those acres are permanently
  excluded from both classes, so an equality against the pre-exclusion area would fail
  by construction. Report the permanently-excluded area as its own line in the summary
  so the drop stays visible instead of silently absorbing a bug.
- Attribute riparian units from the TreeMap pixels they overlap, using the same
  crosswalk as item 1, and assign the `no_management` regime from the regime library
  ([[management-pipeline-plan]] Step 3.1).
- Carry `buffer_class` (`ephemeral_intermittent`, `perennial_small`, `perennial_large`
  from `config/bmp_rules.yaml`) as an attribute on each buffer polygon, so summaries
  can be cut by class without the polygons themselves being merged by class.
- Waterbodies and the road buffer are **not** stands. Water is non-forest; the road
  buffer exists only to absorb road/parcel alignment artifacts. Both stay erase-only.

**Polygon identity — resolved.** Buffer polygons stay unique. Do not dissolve adjacent
buffer segments into one stand per stream reach, and do not merge them into the
managed units they abut. Where a buffer is split by parcel boundaries it inherited
from the input parcels, that split is acceptable — but each resulting polygon keeps
its own `unit_id` and its own row in the summaries. Simulation may still dedupe to
unique `(plot, no_management)` trajectory keys behind the scenes (item 4); that is a
run-count optimization and must not collapse the reporting geometry.

**`PLAN.md` §4b — updated.** The plan previously listed the riparian regime as "thin
only or no entry, depends on buffer class," which was looser than the decision above.
§4b now reads no entry, ever, with the reporting requirement attached.

**Still open.** Whether the buffer polygon set is generated once against the full
hydrography and clipped per county, or rebuilt per county — the current per-county
processing loop means a buffer straddling a county line could otherwise be split into
two polygons with unrelated IDs.

---

## 3. Hex-bin overlay for cartography at the end of the pipeline

**Idea.** After projection and painting, overlay a hexagonal tessellation and
summarize outputs into hex cells to produce clean, legible maps — accepting a loss of
spatial precision in exchange for cartographic quality.

**Constraint that makes this safe.** This is a **presentation layer only**. It runs
after painting, never feeds back into simulation, and never becomes the unit of
analysis. The pixel-level cube (`PLAN.md` §6) stays the authoritative product; hex
maps are a rendering of it.

**Aggregation rules (must be stated, easy to get wrong).**
- Per-acre densities (BA, TPA, volume, carbon — everything TreeMap and FVS emit; see
  [[treemap-methodology]]) aggregate as **area-weighted means**, not naive pixel
  means.
- Totals require multiplying by pixel acres first (900 m² = 0.2224 ac), then summing.
- Mixed forest/non-forest hexes need a declared denominator: per *forested* acre in
  the hex, or per hex acre. These differ a lot at the forest edge. Report the forested
  fraction per hex alongside the value so a reader can tell the difference.

**To decide.** Hex size (and whether one size or a couple for different map scales),
tessellation source (H3 vs. a locally generated hex grid in EPSG:5070 — a projected,
equal-area grid is easier to defend for area-weighted statistics), and whether hex
outputs are a published product or figure-generation only.

---

## 4. Alternative architecture: grow TreeMap pixels directly

**Idea.** Rather than building management units first, take the TreeMap pixels
themselves as the modeling unit: dedupe pixels to their unique imputed plots,
aggregate those up into FVS runs, and paint results back across the landscape — which
is mechanically what `pipeline/s4_fvs/paint_fvs_to_raster.py` already does for the
no-management baseline.

**The problem the adviser flagged.** If the *regime* is keyed to the plot, then
managing a plot manages **every pixel imputed to that plot**, wherever it occurs. TM_ID
2623 alone covers 1,385 pixels. A clearcut would be smeared in speckles across the
whole AOI instead of landing in a contiguous block — spatially wrong, and wrong in a
way that matters for anything edge-, patch-, or disturbance-related.

**RESOLVED (2026-08-06): the unit is the modeling unit; the plot is the tree-list source.**

The adviser's objection is decisive and the pixel-first architecture is rejected as the
*modeling* unit. Trajectories are keyed by **`(management unit, prescription)`**, not by
plot. A unit's tree list is the weighted union of its constituent plots' lists (item 1),
so the same plot contributes to many units and can be clearcut in one and untouched in
another — contiguity comes from the unit layer by construction, and the speckling problem
cannot arise because no decision is ever keyed to a plot.

Pixels remain what they always were: the painting substrate. Each pixel inherits the
selected trajectory of the unit it falls in (`PLAN.md` §4e), which is mechanically what
`paint_fvs_to_raster.py` already does for the no-management baseline.

**Consequence to watch — it changed shape.** FVS run count is now
`units × prescriptions per unit`, not `unique(plot × regime × SI bin)`. That is a larger
number (order 10⁵ for the five-county pilot rather than order 10³), and it no longer
shrinks when plots repeat across the landscape. The cost is bought back three ways: the
runs are barrier-free and embarrassingly parallel, identical `(tree list, site attrs,
prescription)` triples are cached, and the library is a **one-time cost per version**
rather than a per-scenario cost — re-running the scheduler under a new objective touches
no FVS at all. Grid growth is multiplicative, so parameter-grid size is the standing
budget question. See `trajectory-library-and-annealing.md` §4.

---

## Summary of decisions vs. open questions

| Item | Status |
|---|---|
| Riparian buffers grow freely, are never harvested, and are reported as unique buffer polygons | **Decided, and strengthened.** Now enforced structurally: a riparian unit's trajectory library contains only `no_management`, so no-entry is not a constraint the scheduler could weigh. Still needs implementation in `sketch_management_units.py` |
| Tree-list aggregation into units | **Resolved: weighted union.** Neither original option — donor trees kept intact, `TPA` scaled by area share (`build_tree_init`). Partial-harvest distribution rule dissolved |
| Modeling unit and trajectory key | **Resolved: `(management unit, prescription)`.** Pixel-first rejected as the modeling unit; pixels remain the painting substrate |
| Ownership's role | **Resolved: defines the eligible set, not the choice.** `config/prescriptions.yaml`; the scheduler selects within it by simulated annealing |
| Hex-bin overlay | **Cartographic post-process only.** Size and denominator undecided |

Related: [[trajectory-library-and-annealing]] (the architecture these resolutions come
from), [[management_units]], [[management-pipeline-plan]], [[treemap-methodology]],
[[fvs-to-raster-painting]].
