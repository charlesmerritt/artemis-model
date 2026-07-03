# Design: Discriminating clearcut forest from agriculture in Florida

**Date:** 2026-07-01
**Status:** Draft for review
**Deliverable:** `notebooks/Clearcut-vs-Agriculture-Discrimination.ipynb` (analysis notebook, no raster export)

## Problem

Some Florida pixels that LANDFIRE EVT labels as agriculture/grassland/shrubland are, on
the ground, **recently clearcut forest**. Three EVT classes are the prime suspects:

| EVT_NAME | LF2022 VALUE | EVT_PHYS |
|----------|--------------|----------|
| Eastern Warm Temperate Pasture and Hayland | 7997 | Agriculture |
| Southeastern Ruderal Grassland | 9823 | Herbaceous (ruderal) |
| East Gulf Coastal Plain Small Stream and River Floodplain Shrubland | 9585 | Shrubland |

We want to know: **can we tell a clearcut apart from genuine agriculture**, and which of
two methods does it better —
1. **AlphaEarth annual embeddings** (learned 64-D spectro-temporal features), or
2. **LANDFIRE EVT year-over-year change** (a pixel that went from a forest type to one of
   the three classes above is evidence of a clearcut, not real farmland)?

This is an **investigation**, not a production classifier. Output is diagnostics and a
method comparison, not a Florida-wide raster.

## Key data-availability finding

GEE hosts **only one** LANDFIRE EVT vintage: `LANDFIRE/Vegetation/EVT/v1_4_0` contains
just AK / CONUS / HI images, all circa **2016** and on the **pre-Remap** code scheme.
There is no annual EVT time series on GEE. The three target classes above use the
**Remap** scheme, which lives in the local `/mnt/d/LF2022_EVT_CONUS/.../LF2022_EVT_CONUS.tif`.

Consequence for Method 2: we pair **GEE EVT v1.4.0 (2016, "before")** with the **local
LF2022 EVT (2022, "after")**. Because the two vintages use different code schemes, we do
**not** diff raw codes. Instead we map each vintage's EVT to a coarse, stable
**lifeform/physiognomy** (Forest vs Herbaceous/Agriculture/Shrub via the `EVT_PHYS`
attribute) and flag `Forest(2016) → Herb/Ag/Shrub(2022)` transitions. A cleaner
Remap-era comparison (LF2016 vs LF2022, identical codes) would need a second local
download and is noted as a follow-up.

## Architecture: one shared point-sample table

Every method is computed on a single tidy `pandas.DataFrame`, one row per sampled Florida
location. This keeps methods directly comparable (same rows) and avoids heavy raster
exports — appropriate for an analysis notebook.

```
build_sample_points()  -> GeoDataFrame of labeled points (lon, lat, label)
        |
        +-- GEE sampleRegions --> AlphaEarth 64-D vector (event year) + LCMS history
        +-- GEE sampleRegions --> EVT_2016 physiognomy (v1.4.0)
        +-- rasterio .sample()  --> EVT_2022 code / name / physiognomy (local LF2022 tif)
        |
        v
   sample_table (DataFrame)  <-- all methods read from here
```

### Label classes (hybrid: automated bulk + manual anchors)

- **`clearcut`** — automated: LCMS `Land_Cover == Trees` at a pre-year (e.g. 2020) AND
  `Change == Tree Removal` at an event year (e.g. 2021–2023). Stratified random sample of
  pixels meeting this. Plus a handful of manually chosen anchor points (the existing
  `Interactive-Clearcut-Similarity` default point is one).
- **`true_agriculture`** — automated: LF2022 EVT genuine ag/pasture/row-crop classes that
  were **also** non-forest in EVT 2016 (stable ag, unlikely to be a fresh clearcut). Plus
  manual anchors over obvious center-pivot / row-crop fields.
- **`confused_evt`** — the three target classes (7997 / 9823 / 9585) sampled from the local
  LF2022 tif, tagged by which of the three. This is the ambiguous set we want to *place*
  relative to `clearcut` vs `true_agriculture`.

Sample sizes: ~250 pts/class default (tunable), stratified across Florida to limit spatial
autocorrelation. Reference year for AlphaEarth: the event year (2017–2024 available).

### Per-point features
- AlphaEarth 64-D embedding vector at event year (and optionally pre-year for a delta).
- LCMS Land_Cover + Change for pre-year and event year.
- EVT 2016 physiognomy (GEE v1.4.0, via `EVT_class_values`/`EVT_class_names` lookup).
- EVT 2022 VALUE, EVT_NAME, EVT_PHYS (local tif via `rasterio` windowed `.sample()` at
  point coords — never load the full CONUS raster).

## Method 1 — AlphaEarth embedding separability
- PCA(2) scatter of all points colored by label (visual separability).
- Class **centroids** in 64-D; pairwise **cosine distance** between clearcut / true-ag /
  each confused class.
- **Silhouette score** for clearcut vs true-ag.
- Supervised probe: logistic-regression (and KNN) trained on clearcut vs true-ag with
  stratified k-fold CV → accuracy + confusion matrix. Then **score the `confused_evt`
  points**: fraction each of the three classes is assigned to `clearcut` vs `true-ag`.
  Interpretation: a high "clearcut" share means that EVT class is frequently mislabeled
  clearcut in embedding space.

## Method 2 — LANDFIRE EVT year-over-year change
- Per point: `evt_forest_2016` (EVT_PHYS Tree/Forest) AND `evt_ag_grass_shrub_2022`
  (one of 7997/9823/9585, or physiognomy Herb/Ag/Shrub) → `evt_change_clearcut = True`.
- Report the flag rate within each label group and **agreement with LCMS Tree Removal**
  (the independent clearcut signal).

## Method comparison (the point of the notebook)
- Cross-tab of three independent clearcut signals per point: LCMS Tree-Removal label,
  embedding-classifier prediction, EVT-change flag.
- Which method best (a) recovers known clearcuts and (b) keeps true agriculture separate.
- Short written verdict + recommended next step.

## Out of scope
- Florida-wide raster export / production mask.
- UMAP (not installed; PCA via sklearn suffices) and seaborn (matplotlib only).
- A second Remap-era local EVT download (noted as follow-up).

## Verification
- Notebook executes top-to-bottom against live GEE with default (small) sample sizes.
- Pure helper functions (EVT code→physiognomy mapping, label predicate, change flag)
  covered by a light `tests/` unit test — no network.
- A results summary written to `notes/clearcut-vs-agriculture-embeddings.md` and indexed
  in `notes/README.md`.

## Risks / caveats
- GEE single EVT vintage → physiognomy-level bridge (documented above).
- Spatial autocorrelation & class imbalance in sampled points → stratify, note limitation.
- AlphaEarth annual coverage 2017–2024 constrains event-year choice.
- Local LF2022 CONUS tif is large → windowed sampling only.
- "true_agriculture" is itself defined partly from EVT, so it is not fully independent of
  the EVT-change method; the embedding method is the more independent adjudicator.
