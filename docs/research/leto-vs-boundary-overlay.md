# LETO versus boundary-overlay segmentation

## Evidence status

**Established code behavior.** S1 currently preserves two independently runnable
segmentation methods. Both can feed the shared TreeMap/FIA attribution path and
the method-neutral diagnostics in
`pipeline/s1_initial_state/segmentation/comparison.py`.

**Limitation.** No production smoke comparison has been run for this review.
Consequently, this document reports no production unit counts, areas, overlap
rates, donor distributions, or plot-agreement values. Those results are
unavailable evidence, not zero values.

## Source-stage comparison

| Stage | LETO | Boundary overlay | Direct source provenance | Evidence label |
|---|---|---|---|---|
| Spatial domain | Polygonizes valid TreeMap cells inside the parcel area of interest. | Intersects parcels with a vectorized LANDFIRE EVT forest mask. | `leto.build_treemap_domain`; `boundary_overlay.process_county` steps 3–4 | Established code behavior |
| Forest approximation | TreeMap validity defines the initial domain; the code does not apply an EVT class filter. | The current county runner treats EVT values 1000–2999 as tree dominated; its source comment identifies this as an approximation. | `leto.build_treemap_domain`; `boundary_overlay.process_county` step 3 | Established code behavior and limitation |
| Internal partitioning | Repeated constrained-point Thiessen subdivision targets no unit above 200 acres, using one point per 100 acres and a 1,000-foot minimum separation by default. | Optional regular-grid splitting targets 40 hectares. | `leto.subdivide_large_units`; `boundary_overlay.split_large_geometry` and `boundary_overlay.process_county` step 9 | Established code behavior |
| Exclusions and riparian treatment | Explodes multipart geometry, removes pieces below 5 acres before a final parcel clip, and reports area within the default 35-foot streamside management-zone buffer. | Erases configured stream buffers, waterbodies, and a 3-meter road-artifact buffer; pieces below 2 hectares are classified as slivers but are retained by the current county runner. | `leto.cleanup_and_clip_units`; `leto.assign_smz_percent`; `boundary_overlay.process_county` steps 5–8 | Established code behavior |
| Unit identity | Assigns stable IDs after spatial sorting. | Assigns county-scoped sequential IDs after processing. | `leto._assign_stable_mu_ids`; `boundary_overlay.process_county` step 10 | Established code behavior |
| TreeMap/FIA attribution | Counts native TreeMap cells per management unit and emits `MU_PLT_CN_Weights.csv`; modal identity uses descending cell count with ascending TreeMap value as the tie break. | Uses the same shared attribution function once its units are passed to S1 attribution. | `weights.build_plot_weights`; `weights.attach_modal_plot` | Established code behavior |

**Interpretation.** The methods encode different spatial hypotheses. LETO starts
from TreeMap support and creates size-constrained Thiessen units, whereas the
boundary overlay starts from parcel and mapped-feature boundaries. A difference
in unit count or boundary length therefore does not by itself establish that one
method is more realistic.

## Shared comparison contract

`compare_segmentations` reports quantities that do not require corresponding
unit IDs:

- reference and candidate unit counts;
- union coverage, coverage intersection, symmetric difference, and Jaccard
  overlap;
- within-method duplicate coverage, calculated as the summed unit area minus
  dissolved coverage area;
- total, median, 5th-percentile, and 95th-percentile geometry-derived acreage;
- counts below 5 acres and above 200 acres; and
- total, median, 5th-percentile, and 95th-percentile per-unit boundary length.

The reference projected CRS supplies the area and distance units. Candidate
geometry is reprojected to that CRS. Coverage, overlap, thresholds, and every
unit-size summary use geometry-derived acreage; the stored `Acres` column is
validated but is not trusted as comparison evidence.

`compare_attribution` reports donor-count distributions, the fraction of units
with more than one donor plot, and weight-sum diagnostics. “Raw weight” means
`CELL_COUNT / TOTAL_CELLS`; “normalized weight” means the supplied `WEIGHT`
column. A maximum absolute error from one is reported for both.

**Established code behavior.** Cross-method modal-plot agreement is unavailable
unless both weight tables contain a non-null `CROSSWALK_ID`. Within each method,
`CROSSWALK_ID` and `MU_ID` must map one-to-one. Modal ranking requires numeric
`CELL_COUNT` and `TM_VALUE`, rejects ambiguous duplicate donor rows, and uses
descending cell count followed by ascending TreeMap value. The comparison never
assumes that equal `MU_ID` strings identify the same spatial unit across methods.

When both unit artifacts carry a defensible one-to-one `CROSSWALK_ID`, the same
bidirectional safeguards support ownership agreement and SMZ absolute-difference
summaries (mean, median, 95th percentile, and maximum percentage-point
difference). Without that explicit crosswalk these measures remain unavailable.

`compare_initial_states` reports direct, imputed, and missing stand counts and
rates, tree-row count, donor plots per MU, and an explicitly labeled FVS workload proxy:
the number of stand runs represented by the input tables. It does not
claim measured FVS runtime.

Canonical GeoPackages are accompanied by JSON manifests recording the schema,
strategy, AOI, experiment, seed, parameters, code version, artifact path, and
cheap source fingerprints. `load_comparable_artifacts` fails closed before
comparison when run identity or shared TreeMap/FIADB/ownership/species provenance
does not match. `write_comparison` writes stable JSON for machine review;
Markdown remains an optional human-readable rendering.

**Limitation.** A one-to-one crosswalk may not be scientifically appropriate
when one method splits a region that the other keeps intact. In that case,
coverage and attribution distributions remain valid descriptive comparisons,
but modal agreement should remain unavailable until an overlap-weighted
crosswalk is designed and reviewed.

## Research implications

**Hypothesis.** Boundary-overlay units may align more closely with operational
features because parcel, hydrography, and road layers contribute boundaries.
This needs testing against independent management records; boundary complexity
alone is not supporting evidence.

**Hypothesis.** LETO may produce a tighter unit-size distribution because its
subdivision loop explicitly targets an acreage ceiling. This should be tested
with the shared percentiles and oversized counts on the same area of interest.

**Interpretation.** Donor-count and mixed-plot metrics describe how spatial
partitioning changes TreeMap/FIA mixture. They do not measure ecological truth:
both methods inherit TreeMap imputation and FIA sampling limitations.

## Production evidence still needed

The next smoke run should use identical counties, source vintages, projected
CRS, and TreeMap lookup for both methods. It should retain the returned metric
series as the machine-readable record and serialize the same series as JSON with
`write_comparison` for review.

Open questions:

1. Which independent operational boundaries or field records can validate
   management-unit realism?
2. Should sliver and oversized gates stay at the present shared 5-acre and
   200-acre diagnostics, or should a later analysis report method-specific
   thresholds alongside them?
3. Is a reviewed one-to-one crosswalk defensible for any study area, or is an
   overlap-weighted many-to-many attribution comparison required?
4. How sensitive are the conclusions to EVT, TreeMap, parcel, road, and
   hydrography vintage differences?

No hybrid segmentation is proposed or implemented here.
