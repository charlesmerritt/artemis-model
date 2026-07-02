# Discriminating clearcut forest from agriculture in Florida

Two notebooks investigate whether Florida pixels that LANDFIRE EVT labels as
agriculture/grassland/shrubland are actually **recently clearcut forest**, and which of two
methods best flags that confusion.

- `notebooks/Clearcut-vs-Agriculture-Embeddings.ipynb` — Method 1, AlphaEarth embedding separability.
- `notebooks/Clearcut-vs-Agriculture-EVT-Change.ipynb` — Method 2, LANDFIRE EVT year-over-year change + cross-method comparison.
- `notebooks/clearcut_ag_common.py` — shared helpers (constants, GEE/rasterio sampling, labeling). Pure helpers covered by `tests/test_clearcut_ag_common.py`.
- Design/spec: `docs/superpowers/specs/2026-07-01-clearcut-vs-agriculture-embeddings-design.md`.

## The three "confused" EVT classes (LF2022 Remap codes)

| EVT_NAME | VALUE | EVT_LF | EVT_PHYS |
|----------|-------|--------|----------|
| Eastern Warm Temperate Pasture and Hayland | 7997 | Herb | Agricultural |
| Southeastern Ruderal Grassland | 9823 | Herb | Exotic Herbaceous |
| East Gulf Coastal Plain Small Stream and River Floodplain Shrubland | 9585 | Shrub | Riparian |

## Key data-availability findings (load-bearing)

- **AlphaEarth** = `GOOGLE/SATELLITE_EMBEDDING/V1/ANNUAL`, 64 bands `A00..A63`, 10 m, annual **2017–2024**.
- **LCMS** = `projects/gtac-data-publish/assets/LCMS/Product_Version/2025-11` (annual `Land_Cover`, `Change`, `Land_Use`). Clearcut signal = `Land_Cover==Trees(1)` pre-year AND `Change==Tree Removal(9)` event year — the repo's established clearcut definition.
- **LANDFIRE EVT on GEE is a single vintage.** `LANDFIRE/Vegetation/EVT/v1_4_0` has only AK/CONUS/HI images, all ~2016, **pre-Remap** code scheme (e.g. "Eastern Warm Temperate Pasture and Hayland" = **3997** in 2016 vs **7997** in LF2022). There is **no annual EVT series on GEE**.
- **LF2022 EVT** (Remap codes, the source of the three target classes) is local only: `/mnt/d/LF2022_EVT_CONUS/.../Tif/LF2022_EVT_CONUS.tif` (EPSG:5070, 30 m, 156336×101538, nodata 32767). Class attributes in the sibling `CSV_Data/LF2022_EVT.csv` (`VALUE, EVT_NAME, EVT_LF, EVT_PHYS`). Tif is huge — **windowed per-point sampling only**, never a full read.

### Method 2 bridge across the code-scheme change
Because GEE has only the 2016 vintage and it uses different codes than LF2022, the EVT
year-over-year comparison is done at the **stable forest-vs-not level**: 2016 "forest" is
inferred from the EVT class *name* (`forest`/`woodland`/`plantation` keyword), and the flag
fires when a 2016-forest point is one of the three confused classes (strict) or any
Herb/Agriculture/Shrub lifeform (broad) in LF2022. A clean same-scheme diff (LF2016 vs
LF2022) would need a second local EVT download — the natural follow-up.

## Architecture
Both methods run off one labeled point-sample table (`build_sample_table`): clearcut points
from the LCMS mask + random Florida points + manual anchors, each attributed with the
AlphaEarth vector, LCMS pre/event cover+change, GEE EVT-2016 class, and local LF2022 EVT
class. `derive_labels` assigns primary labels with priority **confused > clearcut >
agriculture > other** (confused points excluded from the clean clearcut/agriculture training
anchors so both methods can adjudicate them). Method 1 writes
`data/interim/clearcut_ag/embeddings_samples.csv`; Method 2 reads it so the two are compared
on identical points.

## Environment notes
- `earthengine-api` is authenticated in this environment (`ee.Initialize()` works, no re-auth).
- `scikit-learn` present; `umap` and `seaborn` are **not** installed → use PCA + matplotlib.
- `pyarrow` absent → shared table persisted as **CSV**, not parquet.

## Results (first full run: event 2022, pre 2020, 1700 usable points)

Sample: 304 clearcut, 85 agriculture, 182 confused (pasture_hay 144, ruderal_grass 37,
floodplain_shrub **1**), 1129 other. Outputs in
`data/interim/clearcut_ag/{embeddings_samples,evt_change_samples}.csv` + `embeddings_pca.png`.

**Method 1 (AlphaEarth embeddings) — strong separator.**
- Clearcut vs agriculture: logistic-regression 5-fold CV accuracy **0.956** (KNN 0.951);
  silhouette (cosine) ≈ 0.45. Embeddings genuinely tell clearcut from farmland.
- Share of each confused class the classifier calls clearcut (`frac_pred_clearcut`):
  **ruderal_grass 0.84** (mean prob 0.69), **pasture_hay 0.34** (0.42), floodplain_shrub 1.0 (n=1).
- Centroid cosine distance to clearcut vs to agriculture: ruderal_grass **0.141 vs 0.272**
  (much closer to clearcut), pasture_hay 0.373 vs 0.194 (closer to agriculture).

**Method 2 (LANDFIRE EVT 2016→2022 change) — conservative, weak vs LCMS.**
- 24 points flagged strict (forest-2016 → one of the three classes), 43 broad.
- 13.2% of confused-class points carry a strict EVT-change flag.
- As a detector of LCMS tree-removal clearcuts: strict precision 0.04 / recall 0.003, broad
  0.09 / 0.013 — low, because EVT vintages are ~6 yr apart and coarse and don't align with the
  LCMS event year.

**Cross-method (share flagged clearcut on the confused classes):**
| class | embeddings | EVT-change strict | LCMS tree-removal |
|-------|-----------|-------------------|-------------------|
| ruderal_grass (n=37) | 0.84 | 0.38 | 0.00 |
| pasture_hay (n=144) | 0.34 | 0.06 | 0.01 |
| floodplain_shrub (n=1) | 1.00 | 1.00 | 0.00 |

**Takeaways.**
- **Southeastern Ruderal Grassland (9823)** is the class most often mislabeling clearcut
  forest — all three methods rank it highest, and it sits closer to clearcut than to
  agriculture in embedding space. Pasture/Hayland (7997) is genuinely more agricultural.
- Embeddings are the **most sensitive** adjudicator; EVT-change is conservative; LCMS
  tree-removal almost never fires on these pixels (they were likely cut before the window
  or reclassified). Use embeddings to flag candidates, EVT-change/LCMS to corroborate.
- **Limitation:** random Florida sampling barely hits floodplain shrubland (9585, n=1). To
  characterize it, add targeted sampling of that class from the LF2022 raster (raise
  `N_RANDOM` or draw class-stratified points), then re-run.

## Validation of the run (scrutinized 2026-07-01)

- **Integrity:** 1700 rows, 0 NaN in embedding bands, 0 duplicate (lon,lat), `clearcut_prob`
  in [0.02, 0.99]; every `clearcut` label satisfies the LCMS predicate and every `confused`
  label is in {7997,9823,9585} (0 violations); 2 confused points are also LCMS clearcuts
  (expected overlap).
- **Embedding sanity:** per-row L2 norm = 1.000 ± 0.002 (min 0.992, max 1.007) — confirms the
  correct AlphaEarth unit vectors were sampled; no zero-variance bands.
- **Spatial autocorrelation is not driving the result.** clearcut and agriculture anchors
  both span the whole state and share 21 of 59 half-degree cells. Accuracy under **spatial
  block CV (GroupKFold by 0.5° cell) = 0.954** vs random CV 0.956 (balanced acc 0.907 vs
  0.913, AUC 0.995 vs 0.994). A label-shuffle baseline collapses to the 0.781 majority rate.
  So the classifier is learning land cover, not geography.
- **Geographic realism (see `validation_spatial_map.png`):** clearcuts land in the
  Panhandle / North Florida pine-timber belt, agriculture in the South-Central row-crop /
  sugarcane belt near Lake Okeechobee, confused in central-Florida ranch country.
- Figures in `data/interim/clearcut_ag/`: `embeddings_pca.png`, `validation_spatial_map.png`,
  `validation_confused_prob.png`. Output CSV invariants are guarded by
  `tests/test_clearcut_ag_outputs.py` (skips if the run hasn't been produced).

## Feature-engineering step (2026-07-02)

`notebooks/Clearcut-Grassland-Feature-Engineering.ipynb` builds the model-ready table for the
ultimate goal: **predict whether a pixel LANDFIRE EVT calls grassland/ag/shrub is actually
recently clearcut forest.** New code lives in `clearcut_ag_common.py`.

- **Sampling fix (floodplain shrubland n=1 solved).** `stratified_evt_points` does a two-stage
  read of the local LF2022 tif: a fast decimated (factor 8) read of the FL window locates blocks
  containing a target class, then a tiny full-res read of each block pins the exact pixel. The
  naive decimated-coordinate approach only matched the rare class 2/30 at full res; two-stage is
  **150/150**. Each confused class now gets a guaranteed N (250 each in the run).
- **Anchor-based labels** (LCMS tree-removal ≈0 inside the confused classes, so no in-universe
  labels): `positive_forest` = LCMS clearcut; `negative_grassland` = stable non-forest (never
  Trees, not EVT-2016 forest, not clearcut); `apply_confused` = the three EVT classes, scored not
  trained. `derive_feature_label` assigns roles/`y`.
- **Features** (`sample_features`, 156 cols): AlphaEarth event (`A00..A63`) + pre-year
  (`P00..P63`) embeddings, `emb_delta_l2` (disturbance magnitude), LCMS multi-year history
  (`lcms_tree_removal_count`, `lcms_ever_trees`, land cover/use/change), and EVT-change flags.
  `feature_dictionary()` tags each column with `lcms_derived`; `clean_feature_columns()` returns
  the 132 leakage-free predictors (embeddings + EVT). Outputs: `feature_table.csv`,
  `feature_dictionary.csv`, `confused_scored_full_features.csv`.

### Run results (1750 rows; 400 pos / 281 neg anchors) and the key caveat
- `emb_delta_l2` by role: positive_forest **0.72** vs negative_grassland 0.40 — the temporal
  disturbance signal is real.
- Spatial block CV (GroupKFold 0.5° cell): featset A (event embeddings only) AUC **0.994**;
  B (+pre+delta) and C (+EVT) AUC **1.000**.
- **AUC ≈ 1.0 is largely by construction, not a solved problem.** Positives are defined as
  pre-forest (LCMS Trees in PRE_YEAR) and negatives as never-forest, and the pre-year embedding
  encodes exactly that — so B/C are near-circular and should be read as an upper bound. Featset A
  (0.994) is the honest appearance-based number.
- Apply-set `frac_forest` (the deliverable): floodplain_shrub **0.81**, ruderal_grass **0.41**,
  pasture_hay **0.06**. Differs from the embeddings-only Method-1 ranking because temporal
  features re-weight toward "was it forest 2 years ago." These are estimates needing imagery/field
  validation.
- **Next to make it rigorous:** define the label with an earlier pre-year than the embedding
  pre-year (decouple label from feature), and validate the apply set against NAIP/hand labels.
  Easy feature adds already in-repo: ownership, terrain, soils, climate.

## Embedding-similarity AOI finder (2026-07-02)

`notebooks/Embedding-Similarity-AOI-Finder.ipynb` is a standalone tool: pick reference
clearcut points (coord list or draw on a geemap map), choose an AOI (all Florida or selected
counties via GEE `TIGER/2018/Counties`), pick an embedding year, and it returns a vector layer
of everything similar. Helpers are in `clearcut_ag_common.py`.

- **Similarity** = max cosine similarity to any reference embedding (`similarity_image`,
  `agg="max"`); AlphaEarth vectors are unit-length so cosine = dot product.
- **AOI** via `counties_aoi(names)`; vectorize scale is adaptive (`vector_scale_for_area_km2`:
  20 m ≤8000 km², 40 m ≤40000, else 90 m — so all-Florida ≈ 90 m).
- **`vectorize_similarity`** polygonizes `similarity >= threshold`, tags `area_ha`, and drops
  patches below `min_area_ha` **server-side** — essential, since a 20 m mask otherwise
  vectorizes into tens of thousands of specks (Marion County at 0.85 gave 14,065 polygons; with
  a 1 ha min it's a manageable set of real patches).
- **Output**: a GeoPackage with two layers — `reference_points` (what you selected) and
  `similar_regions` (polygons with `area_ha`) — under `data/interim/similarity_finder/`.
- **Smoke/demo run** (Marion County, 2023, threshold 0.86, 1 reference clearcut, min 1 ha):
  **1889 similar patches totaling 24,401 ha**. Threshold sensitivity in Marion: 0.80→29,711 /
  0.85→14,065 / 0.90→3,482 raw polygons (pre-area-filter).
- **Gotcha:** the geemap/ipyleaflet map cells embed widget state into the notebook on execution
  (a 16.9 MB file!). Strip `nb.metadata["widgets"]` (and widget-view outputs) before committing —
  the committed notebook is ~17 KB.
