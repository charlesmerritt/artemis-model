# TreeMap hole rectification

Correcting TreeMap 2022 — the raster that underlies the whole ARTEMIS model — where
LANDFIRE's existing-vegetation classification silently drops managed forest into a
non-forest label.

## Language

**Hole**:
A TreeMap 2022 pixel with no `TM_ID`. TreeMap only imputes a plot where LANDFIRE EVT
calls the pixel a `Tree` lifeform, so any pixel EVT calls non-tree is a hole,
regardless of what it actually is on the ground.

**Confused class**:
A specific LANDFIRE EVT class that is a hole by construction, but whose pixels are a
mix of two ground-truth populations: land the class name honestly describes, and
managed/clearcut forest that LANDFIRE mislabeled once its `Recently Logged-*` classes
stopped being populated after 2016. "Hole" and "confused class" are the same
phenomenon seen from two sides — TreeMap's (missing plot) and LANDFIRE's (which wrong
label it fell into) — not two different populations needing separate treatment.

**Ruderal grassland**:
The first confused class under active rectification. LANDFIRE EVT LF2022 value
`9823`, name "Southeastern Ruderal Grassland", `EVT_LF = Herb`. The largest sink for
holes that used to carry a `Recently Logged` flag pre-2016.

**Mask**:
The population a confused-class rectification run targets: every pixel whose LF-EVT
2022 classification equals the confused class under study, within the current AOI.
Membership is decided by that single-year categorical label alone — not by any
multi-year transition test.
_Avoid_: hole stratum, S1–S5 (that was the discarded per-stratum design from the
earlier clearcut-recovery pipeline; it does not define mask membership here).

**Review verdict**:
A human's judgment on one sampled, classified point in the labeling webapp: approve,
disapprove, or ambiguous. Stored per point, per reviewer, in the review database.
_Avoid_: label (ambiguous with the model's own predicted class).
