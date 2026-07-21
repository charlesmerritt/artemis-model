# S1 Segmentation Synthesis Experiment Specification

**Date:** 2026-07-20
**Status:** Proposed experiment protocol; no hybrid is implemented or selected

## Research objective

Determine whether individual design choices from the LETO and boundary-overlay
segmentation methods improve management-unit usefulness without degrading
spatial coverage, TreeMap/FIA attribution, FVS readiness, or reproducibility.
The study tests factors independently before testing interactions. It does not
replace, merge, or deprecate either baseline.

## Evidence status

### Established code behavior

- The faithful LETO baseline uses the valid TreeMap domain, constrained-point
  Thiessen subdivision, a 200-acre ceiling, one point per 100 acres, 1,000-foot
  point separation, a 5-acre cleanup threshold, and a 35-foot SMZ measurement.
- The boundary-overlay baseline intersects parcels with the current LANDFIRE
  EVT forest approximation, erases configured streams, waterbodies, and a
  3-meter road-artifact buffer, classifies pieces below 2 hectares as slivers,
  and optionally splits large polygons with a 40-hectare fishnet.
- Both outputs can use the shared TreeMap cell-count attribution and the same
  FIA/FVS initial-state functions.
- The comparison module reports coverage, fragmentation, unit-size, donor, and
  weight diagnostics. Modal-plot agreement is unavailable without an explicit,
  valid one-to-one `CROSSWALK_ID` in both weight tables.

These statements describe the current code and its configured defaults. They
are not production performance results.

### Interpretation

The baselines encode different spatial assumptions. LETO emphasizes TreeMap
support and size control; boundary overlay emphasizes parcel and mapped
exclusion boundaries. Greater boundary length, higher TreeMap overlap, or more
FVS stands is not intrinsically better. Any claim of management realism needs
independent operational boundaries or field records.

### Evidence not yet available

No production paired run has established which method has better coverage,
fragmentation, FIA support, FVS readiness, runtime, or operational realism.
No numerical outcome is assumed in this specification.

## Immutable baselines

Every experiment must rerun and retain both baselines:

| Baseline | Domain | Exclusions and SMZ | Splitter | Sliver policy |
|---|---|---|---|---|
| Faithful LETO | Valid TreeMap cells within parcels | Report area inside 35-foot SMZ; do not erase it | Seeded constrained-point Thiessen; 200-acre ceiling | Remove pieces below 5 acres |
| Boundary overlay | Parcel × current EVT tree-mask approximation | Erase configured stream buffers, waterbodies, and 3-meter road buffer | Deterministic 40-hectare fishnet when enabled | Retain and classify pieces below 2 hectares |

Baseline source code, defaults, and outputs remain addressable by their current
method names regardless of any synthesis result. A synthesis candidate receives
a new experiment identifier; it must never overwrite a baseline artifact.

## Experimental units and controls

The primary paired experimental unit is an area of interest (AOI), not an
individual management unit. Use each of the five pilot counties as a separate
AOI and retain county-level results. If the production source does not cover a
county completely, fail that AOI rather than changing its boundary.

Hold these variables fixed within every pair:

- AOI geometry and projected CRS;
- source files, vintages, layer names, and file checksums;
- TreeMap raster and VAT, FIA database query, ownership raster, parcels,
  hydrography, roads, waterbodies, and species crosswalk;
- TreeMap rasterization rule, 0.05 donor threshold, FIA state set, species
  translation, nearest-unit imputation, FVS variant, and inventory year;
- software commit, lockfile, Python environment, machine thread count, and
  measurement procedure.

Use seeds `0` through `19` for every stochastic splitter. Run deterministic
variants once, then repeat one identical run to verify byte-stable tabular
outputs and geometry-equivalent spatial outputs. Randomized variants must use
the same seed in each paired comparison. Seed is a repeated condition nested
within AOI, not an independent AOI replicate.

## Factors and falsifiable hypotheses

Change one factor at a time from its parent baseline. A factor that passes may
enter a later, explicitly registered two-factor interaction experiment; it does
not authorize a full combinatorial search.

### F1: domain source

**Levels:** valid TreeMap domain; current EVT tree-mask approximation.

**Hypothesis H1.** Holding exclusions, splitter, sliver policy, and SMZ treatment
fixed, the EVT domain increases coincidence with independent mapped or field
forest boundaries without exceeding the coverage and FIA/FVS guardrails below.

**Falsification.** H1 is weakened if independent boundary coincidence does not
improve in at least four of five AOIs, or if any guardrail fails. If independent
reference boundaries are unavailable, H1 is inconclusive rather than supported.

### F2: exclusion boundaries

**Levels:** no erase; stream erase only; stream plus waterbody erase; stream,
waterbody, and 3-meter road-artifact erase.

**Hypothesis H2.** Adding verified exclusion boundaries reduces management-unit
area that overlaps held-out validation exclusions not used to construct the
candidate, without an unacceptable loss of eligible forest coverage or direct
FIA/FVS readiness.

**Falsification.** Reject a level if exclusion overlap does not decrease, if a
coverage or readiness guardrail fails, or if total sliver acreage increases
relative to the paired parent.

### F3: large-unit splitter

**Levels:** no split; LETO constrained-point Thiessen; fixed 40-hectare fishnet.

**Hypothesis H3.** A splitter reduces units above the registered 200-acre
diagnostic threshold while producing less boundary fragmentation and no worse
FIA/FVS readiness than the competing splitter.

**Falsification.** The no-split level is a negative control. Reject a candidate
splitter if units above its declared ceiling remain, if it fails to terminate,
if its fragmentation cost exceeds the registered margin without a readiness
benefit, or if its result is unstable across seeds.

### F4: sliver policy

**Levels:** retain and label; drop below 2 hectares; drop below 5 acres; merge
into the adjacent unit sharing the longest boundary. The merge level is a
future candidate and is not implemented by this specification.

**Hypothesis H4.** A reviewed sliver policy reduces sliver count and total
boundary length without materially reducing eligible coverage or increasing
mixed-plot attribution and FVS imputation.

**Falsification.** Reject a policy if it breaches a guardrail, creates invalid
geometry, or changes results with input row order.

### F5: SMZ treatment

**Levels:** measure percent within a 35-foot buffer; erase the configured stream
buffer; retain an explicit SMZ subunit. The explicit-subunit level is a future
candidate and is not implemented by this specification.

**Hypothesis H5.** Explicit SMZ representation preserves more usable forest
coverage than erasure while retaining the information needed for management
constraints and FVS handoff.

**Falsification.** Reject a treatment if SMZ accounting is not area-conserving,
if it double-counts coverage, or if downstream tables cannot retain an
unambiguous SMZ measure.

### F6: seed sensitivity

**Levels:** seeds `0` through `19` for each stochastic splitter.

**Hypothesis H6.** Scientific conclusions and keep/reject classifications do
not depend on a small subset of random seeds.

**Falsification.** A candidate is unstable if fewer than 16 of 20 seeds per AOI
retain the direction of its primary effect, or if any seed breaches a hard
validity gate. An unstable candidate is rejected or redesigned before any
interaction experiment.

## Outcomes

Report every metric by method, AOI, and seed. Aggregate only after retaining the
paired records.

### Hard validity and reproducibility gates

- required shared fields present; unique, non-null `MU_ID` values;
- projected CRS; finite, valid, non-empty Polygon/MultiPolygon geometry;
- no unexplained within-method overlap above 0.01 acre per AOI;
- finite non-negative weights and maximum normalized weight-sum error at or
  below `1e-9`;
- all expected manifests and output tables present and non-empty when the stage
  contract requires rows;
- identical inputs and seed reproduce geometry-equivalent units and identical
  ordered tabular outputs.

Any hard-gate failure rejects that run and blocks candidate selection. It is not
converted to a zero metric.

### Spatial coverage and fragmentation

- dissolved coverage acres, intersection, union, symmetric difference, and
  coverage Jaccard;
- within-method overlap acres;
- unit count and total, median, 5th-, and 95th-percentile unit acres;
- sliver count and acreage; oversized count and acreage;
- total and per-unit boundary length;
- components per unit and Polsby-Popper compactness
  (`4 * pi * area / perimeter^2`);
- exclusion-layer overlap acres and, when independent operational boundaries
  exist, the fraction of candidate boundary within the registered distance
  tolerance of those boundaries.

### TreeMap/FIA attribution

- donor-count distribution and mixed-plot rate;
- raw and normalized weight-sum errors;
- weighted plots absent from the FIA query result;
- units with at least one live, translated FIA tree before imputation;
- modal-plot agreement only where a defensible explicit crosswalk exists.

### FVS readiness and cost

- direct, imputed, and missing stand counts and proportions;
- stand and tree row counts, invalid or missing required FVS fields;
- fixed-horizon FVS completion rate and runtime on the same engine build;
- segmentation, attribution, and initial-state wall time plus peak resident
  memory.

Imputation rate is reported separately because imputation can make a table
runnable while masking weak direct FIA support.

## Paired decision rules

The numerical margins below are protocol choices, not established ecological
facts. They must be approved before production execution and cannot be changed
after results are inspected. The registered primary improvements are:

| Factor | Primary improvement required in each supporting AOI |
|---|---|
| Domain source | At least `0.05` absolute increase in the fraction of boundary within 30 meters of a held-out forest boundary |
| Exclusion boundaries | At least 50% reduction in area overlapping held-out validation exclusions |
| Large-unit splitter | At least 90% reduction in acreage above 200 acres, with no more than 10% added total boundary length |
| Sliver policy | At least 50% reduction in sliver acreage, with no increase in total boundary length |
| SMZ treatment | Retain at least 90% of the eligible coverage removed by erasure while passing exact area-accounting checks |

If the required held-out reference does not exist, the associated factor is
inconclusive. Substituting a construction input as its own validation reference
is not allowed.

A factor level is **kept for the next synthesis experiment** only when all of
the following hold:

1. every hard gate passes;
2. its registered primary benefit has the hypothesized direction in at least
   four of five AOIs and, for stochastic variants, at least 16 of 20 seeds
   within each supporting AOI;
3. its area-weighted coverage Jaccard is no more than `0.01` below its paired
   parent and its added symmetric-difference area is no more than 1% of the
   paired coverage union;
4. the proportion of units with direct runnable FIA/FVS trees is no more than
   `0.02` below its paired parent, the missing-stand proportion does not
   increase, and fixed-horizon FVS completion does not decrease; and
5. the paired median, minimum, and maximum AOI effects and the complete seed
   distribution are reported. With only five AOIs, these summaries are
   descriptive and must not be presented as broad population inference.

A factor level is **rejected** when a hard gate fails, a guardrail is breached,
the primary effect is opposite in at least three AOIs, or seed stability fails.
It is **inconclusive** when the primary independent reference is unavailable,
the direction criterion is unmet without a reject condition, or the interval
crosses the practical threshold. Inconclusive factors are not promoted.
All keep, reject, and inconclusive decisions, including null and failed runs,
remain in the experiment record.

Passing means only that a factor may be combined with one other passing factor
in a preregistered interaction test. It does not establish a production winner.

## Execution order

1. **Lock inputs and baselines.** Run both immutable baselines on all AOIs,
   record manifests, and verify deterministic or seeded reproducibility.
2. **One-factor experiments.** Test F1 through F5 independently. Evaluate F6
   for every stochastic level.
3. **Limited interactions.** Register at most one scientifically motivated
   two-factor comparison at a time using only levels that passed individually.
4. **External validation.** Evaluate operational-boundary hypotheses against
   data that were not used to create any candidate.
5. **Decision review.** Publish paired metrics, failures, uncertainty, and the
   keep/reject/inconclusive classification. Preserve both baselines.

## Reproducibility record

Each run must store:

- experiment ID, parent baseline, factor and level, AOI, and seed;
- Git commit and dirty-worktree state;
- Python version, lockfile checksum, package versions, and FVS engine identity;
- absolute source identifiers plus size, modification time, and content hash;
- AOI geometry hash, CRS, all effective parameters, and environment thread
  settings;
- ordered stage timings, warnings, failure traceback, and artifact checksums;
- raw per-AOI/per-seed metrics and the script or command that generated them.

Notebook cells may inspect results, but the experiment runner and metrics must
be scriptable and must not depend on hidden notebook state. Production artifacts
must not be inferred from saved notebook outputs.

## Expected failure modes and responses

| Failure mode | Required response |
|---|---|
| Production mount or declared source missing | Stop before segmentation; do not substitute synthetic input |
| Source vintage or AOI mismatch | Mark the pair invalid and rerun both sides from the locked manifest |
| Invalid, empty, non-polygon, or overlapping geometry | Fail the hard gate and preserve the failing artifact |
| Constrained splitter cannot place points or terminate | Record the seed and geometry; reject that run rather than retaining an oversized unit silently |
| Boundary source misregistration creates slivers | Report sliver acreage and source alignment; do not clean it away outside the registered policy |
| Missing TreeMap/FIA identifiers or non-unit weights | Fail attribution; do not treat missing donors as zero evidence |
| Nearest-unit imputation hides direct-data loss | Report direct and imputed rates separately and apply the direct-readiness guardrail |
| FVS crash or incomplete output | Count as a failed completion for that pair and retain logs |
| Seed-dependent conclusion | Apply H6 and reject or redesign the stochastic factor |
| Invalid cross-method unit correspondence | Leave modal agreement unavailable; do not match equal `MU_ID` strings |
| Multiple exploratory comparisons | Label them exploratory and require a new preregistered confirmation run |

## Open questions

1. Which independent management boundaries or field records can support the
   primary operational-realism outcome without sharing inputs with a candidate?
2. Are the provisional `0.01` Jaccard, 1% symmetric-difference, and `0.02`
   direct-readiness margins acceptable to forestry and FVS stakeholders?
3. Should county be the final inference unit, or should additional independent
   AOIs be registered before making a production recommendation?
4. Are Polsby-Popper compactness and the provisional 30-meter
   boundary-coincidence tolerance defensible for these source resolutions?
5. Is a one-to-one crosswalk defensible anywhere, or is an overlap-weighted
   many-to-many attribution comparison required?

Until these questions and the production evidence are resolved, both current
baselines remain the supported reference methods and no hybrid is selected.
