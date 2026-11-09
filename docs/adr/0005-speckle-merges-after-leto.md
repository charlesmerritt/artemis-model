# Speckle merges into a neighbour after LETO, on by default, across owner classes

**Status:** accepted, 2026-11-09. Departs from LETO v1, which keeps every fragment its
`force_merge` leaves. Supersedes the 2026-10-05 "keep every stand LETO keeps" decision
(`research/leto_parity/README.md`).

## Context

In the FL5 golden run, 58% of the 114,727 management units are under 1 ac, and together they
hold 1.6% of the area. 30% are smaller than one TreeMap cell, so they are slivers from the
vector riparian and county splits, not segmentation output. The maps read as salt and pepper,
and each unit costs the annealer about 17.9 KB whatever its size.

## Decision

LETO's own small-stand actions (`force_merge`, `retain`, `delete`, `proximity_merge`) are ported
as upstream has them, and `force_merge`, LETO's verified default, stays the default. They act
on parent stands before the riparian split, so none of them reaches the slivers the split
makes. That is why this step exists on top of them.

After LETO's riparian and county splits, a unit under the minimum unit area (1 ac, a
regional LETO setting) is speckle. `SpeckleAction.MERGE`, the default, merges it; `KEEP` leaves
LETO's output untouched. A speckle joins the first neighbour that qualifies in this order:

1. The same owner class and the same unit class.
2. The same unit class under another owner class. The speckle takes that owner class.
3. For a managed speckle only, a riparian neighbour. The speckle becomes grow-only.
4. Otherwise it is kept.

Within a step, the neighbour with the lowest `force_merge` cost wins: the difference in stand
age, live basal area, QMD, TPA and forest type, less 0.2 × shared edges. Ties go to the lower
`unit_id`. Longest shared edge was rejected because a young-plot speckle (2,451 of FL5's 5,627
young-plot units are under 1 ac) would join its mature neighbour and inherit its age, which is
the failure ADR 0003 fixed. The merge
repeats until no speckle can merge. It never crosses a county line, and riparian area never
joins a managed unit. The absorbed area takes the receiving unit's parent stand, donor
weights, representative plot and age, and acres are conserved.

## Consequences

- Unit counts no longer match LETO's own. The Maine baseline replay must run with `KEEP`
  and `force_merge`.
- Under `proximity_merge` a unit can be multipart, and the 1 ac test applies to the whole unit.
- Step 2 moves acres between owner classes, and so between TPO owner groups. Step 3 turns
  managed acres into grow-only acres. The QA report shows both by county.

## Implementation notes (#167, 2026-11-09)

- Masked units (Harris 1-2) are neither speckle nor receivers, so forest never becomes masked
  and non-forest never becomes scheduled.
- A unit's cost terms are its parent stand's: the CA's robust-standardized means and dominant
  forest type. A shared edge is a cell side's length (30 m) of common vector boundary, so
  slivers cut by a buffer score fractional edges. Contact under 1 m is a corner, not an edge,
  as `force_merge`'s four-neighbour adjacency has it.
- `parent_stands.tif` is LETO's parent-stand raster before the splits and is not relabelled.
  A unit's parent stand is its `SEG_ID` in the units layer.
- On Union (12125, v4 run) the step order usually leaves a young-plot speckle a single
  candidate, so the cost rarely chooses: 90 of 128 merged young speckle (15.7 of 5,250
  add-back ac) join mature land, 71 of them grow-only riparian units. Accepted as small;
  `tests/test_leto_t3_sanity.py` bounds the add-back acres that join mature managed land
  at 1%.
