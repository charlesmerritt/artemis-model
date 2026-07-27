# Methodology Directions (advisor meeting, 2026-07-27)

Four methodological questions raised with the adviser. These are **directions and
open decisions**, not measured results and not yet implemented. Each item records
the question, the options, the trade-offs, and what in the current code would have
to change.

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

**Current lean.** Option B. Assign regimes at the unit level (that is the management
decision), simulate at the plot level, area-weight back to the unit. This keeps the
FVS side identical to the trajectory-library plan and keeps units as a *management*
abstraction rather than a *biophysical* one.

**To decide.**
- The distribution rule for unit-level partial-harvest targets across plots
  (proportional to plot BA? to acres? treat the unit target as per-acre and apply the
  same per-acre residual to each plot?).
- Whether unit-level reporting is per forested acre in the unit or per unit acre.
- Whether tiny plot slivers inside a unit (a plot contributing a handful of pixels)
  get dropped below some area threshold to control run count.

---

## 2. Riparian buffers must be their own stands — excluded from management, still grown

**Decision.** Riparian/BMP buffers are never managed. But they are forested, they
accumulate volume and carbon, and they must not silently vanish from the landscape
accounting. They should be carried as **their own stands/units with a no-management
regime**, grown through the same FVS projection as everything else.

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
- Erase-then-add, so managed and riparian units **partition** the forested area with
  no overlap. Add an area-accounting check: `Σ managed + Σ riparian == forested AOI
  area` within tolerance.
- Attribute riparian units from the TreeMap pixels they overlap, using the same
  crosswalk as item 1, and assign the `no_management` regime from the regime library
  ([[management-pipeline-plan]] Step 3.1).
- Waterbodies and the road buffer are **not** stands. Water is non-forest; the road
  buffer exists only to absorb road/parcel alignment artifacts. Both stay erase-only.

**Note on `PLAN.md` §4b.** The plan lists a riparian regime as "thin only or no entry,
depends on buffer class." The adviser's direction is stricter — no management in the
buffers. Reconcile: default to no entry, and treat any thinning-in-buffer variant as
an explicit scenario, not the default.

**Open.** Whether buffer classes (`ephemeral_intermittent`, `perennial_small`,
`perennial_large` in `config/bmp_rules.yaml`) stay distinct unit classes for reporting,
and whether adjacent buffer segments dissolve into one stand per stream reach or stay
split by the parcel boundaries they came from.

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

**Reconciliation with item 1.** These two are not competing architectures once you
split the keys:
- **Regime is assigned per pixel** (inherited from the management unit the pixel falls
  in, plus ownership, forest type, riparian class).
- **Trajectories are keyed by `(plot_id, regime, site-index bin)`** — `PLAN.md` §4c.

The same plot can then be clearcut in one unit and untouched in another, because those
pixels look up different trajectories. Contiguity comes from the unit layer; FVS run
count stays bounded by unique key combinations, not by pixel or unit count. Item 1's
Option B and item 4 converge on exactly this design — the unit decides *what
treatment*, the plot decides *what tree list*.

**Consequence to watch.** FVS run count is `unique(plot × regime × SI bin)`. Every
regime parameterization we add multiplies it. Keeping the regime library small and
discrete (rather than continuously parameterized per unit) is what keeps this tractable
statewide.

---

## Summary of decisions vs. open questions

| Item | Status |
|---|---|
| Riparian buffers as separate unmanaged-but-growing stands | **Decided.** Needs implementation in `sketch_management_units.py` |
| Keep per-plot tree lists, area-weight to units | **Leaning B.** Needs the unit×stand crosswalk + a partial-harvest distribution rule |
| Pixel-first growth with per-pixel regime + `(plot, regime, SI)` trajectory keys | **Compatible with the above.** Adopt as the scaling design |
| Hex-bin overlay | **Cartographic post-process only.** Size and denominator undecided |

Related: [[management_units]], [[management-pipeline-plan]], [[treemap-methodology]],
[[fvs-to-raster-painting]].
