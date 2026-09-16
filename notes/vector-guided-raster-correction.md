# Vector-guided classification-raster correction

**Added 2026-08-13.** A polygon says *the classification is wrong in here*; a list of class
values says *these labels may be overwritten*; satellite embeddings say what the pixels
actually are. The output is a corrected raster plus the evidence for every change.

Entry point: [`notebooks/Vector-Guided-Raster-Correction.ipynb`](../notebooks/Vector-Guided-Raster-Correction.ipynb).
Implementation: `pipeline/s5_imagery/{raster_correction,feature_sources,naip_viewer}.py`, plus
[`pipeline/raster_clip.py`](../pipeline/raster_clip.py) for the Florida source raster.
Tests: `tests/test_s5_{raster_correction,feature_sources,naip_viewer}.py` and
`tests/test_raster_clip.py` — 79, all offline.

This generalises what [`treemap-holes-rectification.md`](treemap-holes-rectification.md) and
`pipeline/s1_initial_state/classify_holes.py` do for TreeMap holes specifically: same
embedding-classifier shape, same spatially blocked evaluation, but the *where* comes from an
arbitrary vector polygon and the *what* from an arbitrary eligible-class list, so it works on
any categorical raster rather than only on the hole strata.

## The four decisions this design rests on

Settled with the user before implementation; each had a defensible alternative.

| Decision | Chosen | Alternative rejected |
|---|---|---|
| **What corrected pixels get** | Multiclass reassignment — the classifier predicts a class from the vocabulary it saw in training, per pixel | A single user-named replacement code (cannot express a heterogeneous polygon); a mask + probability raster with reassignment deferred |
| **Which pixels are trusted labels** | Every non-eligible pixel, **inside the polygon and outside it** | Outside-polygon only (discards in-situ evidence, extrapolates across the boundary); binary inside-vs-outside (answers polygon membership, not land cover, and cannot reassign) |
| **How previous years feed in** | Visual QA only — the NAIP slider is evidence for the analyst; features are the target year's embedding alone | A multi-year embedding stack with L2 deltas (richer, more leakage surface); prior-vintage EVT change detection (GEE hosts one EVT vintage, so forest/not-forest only) |
| **How the slider is rendered** | geemap + `ipywidgets` inline in the notebook | The repo's MapLibre `viewer/` app via `viewer_catalog.py` (durable and shareable, but the slider leaves the notebook); both |

## Why the trusted set spans the boundary

An AOI is usually a managed unit sitting in a landscape it does not resemble — that is why
someone drew a polygon around it. Train only on the outside and the model extrapolates across
the boundary into ground it has never seen. The undisputed pixels *inside* the polygon (pixels
whose class is not on the eligible list) are the closest thing to in-situ reference data
available, and in the smoke scenario they are a third of the training sample.

The corollary is a real limit: **the correction can only assign a class it was trained on.** A
polygon that is truly planted pine, in a padding ring with no planted pine, gets assigned the
nearest thing the model did see. `PAD_M` (default 500 m) is the knob; the §7 class inventory is
where you check before believing §10.

## The in-band fill trap (found on real data)

LANDFIRE EVT declares GeoTIFF nodata **32767**, and separately codes ocean and
out-of-CONUS ground as **-9999 inside the valid range**. Dropping nodata is therefore not
enough. Measured on a real coastal AOI in the Florida clip, **46.8% of the padding-ring
window is -9999**, and it would have been the second-largest trusted class — meaning the
classifier would have been trained on what the ocean looks like, and could have assigned
"ocean" to a land pixel.

`pixel_table(..., invalid_values=...)` drops these alongside nodata, `CorrectionRequest`
carries the list, and the manifest records it. The notebook defaults to `(-9999,)`. Any new
categorical raster should be checked for its own in-band fill before use.

## Honesty machinery

Carried over from `classify_holes.py`, because the failure mode here is silent:

- **Spatially blocked CV.** `GroupKFold` over square blocks (`block_m`, default 300 m = ten
  30 m pixels). Neighbouring pixels are not independent samples; a random split reports the
  most flattering possible number.
- **A label-shuffle baseline** runs the identical procedure on permuted labels. If the two
  accuracies are close, the embeddings carry no signal for this landscape and the headline
  number is class imbalance, not evidence. Both go in the manifest side by side.
- **Confidence floor.** A prediction is written only when it differs from the original *and*
  clears `min_confidence` (default 0.60). A pixel that stays suspect is recoverable; a
  confidently wrong correction is not.
- **Too-few-blocks is reported, not raised.** A small AOI can fail to produce enough blocks to
  fold. The correction is still produced; `cross_validation.skipped_reason` says the accuracy
  is unmeasured.
- **Every change is itemised.** `corrections.transitions` in the manifest is a from→to class
  tally, and the diagnostics raster carries the original value and per-pixel confidence.

## Design shape

`FeatureSource` (a `runtime_checkable` `Protocol`: `feature_names`, `description`,
`features_at(lonlats) -> ndarray`) is the only seam between the correction logic and the
outside world. Two consequences, both load-bearing:

1. **Nothing in `raster_correction.py` imports Earth Engine**, so the whole workflow —
   read, sample, fit, cross-validate, predict, write — is unit-tested against a synthetic
   raster with a fixture source that reads the known truth. `AlphaEarthEmbeddings` is the
   production implementation and is separately tested only for its guards.
2. **The scaling escape hatch does not touch the correction code.** Point-by-point
   `sampleRegions` caps the AOI at `DEFAULT_MAX_APPLY_PIXELS` (20,000 — every apply pixel is a
   point in an Earth Engine request). Correcting a county needs a second implementation that
   reads an exported embedding raster in blocks; it drops in behind the same protocol.

Missing features are part of the contract, not an error: AlphaEarth does not cover every pixel
of every year and `sampleRegions` silently drops masked points, so a source returns `NaN` rows
and `attach_features` drops them. Those pixels stay uncorrected and are counted in the manifest
as `eligible_pixels_without_features`. A partially observed embedding is not a weaker
observation of the same thing, so nothing is imputed.

## Two output rasters, not three bands

The chosen design was "band 1 corrected class, band 2 confidence, band 3 changed flag" — but a
GeoTIFF cannot mix dtypes across bands, and a categorical raster should not become float32 just
to carry a probability. So:

- `<slug>_corrected.tif` — one band, **source dtype and nodata**, a drop-in replacement for the
  clipped input.
- `<slug>_diagnostics.tif` — three float32 bands (`original_class`, `confidence`, `changed`),
  co-registered. Confidence is `NaN` where no prediction was made; zero would read as "scored,
  and hopeless".

## The ±10-year window is asymmetric and says so

A ±10-year window around 2022 asks for imagery through 2032. `resolve_year_window` trims to
NAIP's first year (2003) and to today, intersects with the years NAIP actually holds over the
extent, and returns the trim as warnings rather than performing it quietly — "four years after
the target instead of ten" changes how much weight the after-comparison can carry.

Measured live over the north-Florida test AOI, the asymmetry is worse than "one or two years
after": NAIP holds `[2005, 2006, 2007, 2010, 2013, 2015, 2017, 2019, 2021, 2022]` there, so a
±10-year window around 2022 resolves to **five years before the target and none after it**.
The comparison is entirely retrospective, and the notebook says so before the slider is drawn.

## The hatched border is geometry

Leaflet styles a stroke with a colour, a width and a dash pattern; there is no hatch or fill
pattern to ask for. `hatch_ticks` therefore walks the boundary at a spacing derived from the
perimeter and emits a short segment at each station, rotated 45° off the local tangent and
flipped when needed so it falls *inside* the polygon — the cartographic convention. It returns
a `MultiLineString` in EPSG:4326, so it is usable by any map library, not just the widget.

## Which raster the notebook reads

`data/interim/clips/LF2022_EVT_FL.tif` — Florida cut out of the CONUS EVT, 75 MB rather than
2.99 GB, pixel-identical and on the same grid. Produced by
[`pipeline/raster_clip.py`](../pipeline/raster_clip.py); see
[raster-clips.md](raster-clips.md), which also records the `data_paths.yaml` R2 mapping bug
that had been making the EVT raster look absent. The notebook falls back to the CONUS raster
when the clip is missing, and `RASTER_PATH` overrides both.

## Verified

- **79 offline tests** over the four modules (34 correction, 20 viewer, 18 clip, 7 feature sources); full suite 877 passed, 10 skipped.
- The notebook's non-Earth-Engine cells executed end to end against a synthetic EVT-like
  raster (183 × 175 px, two trusted classes, a nodata strip, an 840-pixel mislabelled block):
  all 840 eligible pixels reassigned `9823 → 9312`, blocked CV accuracy 1.000 against a 0.477
  shuffled baseline, all three outputs written with the source dtype preserved.
- **The full correction run against the real Florida EVT clip**, on a 144 ha AOI over the
  densest Southeastern Ruderal Grassland cluster in the state, with a neighbourhood oracle
  standing in for AlphaEarth. 16 real EVT classes learned and 8 too-rare ones dropped;
  blocked CV 0.571 against a 0.150 shuffled baseline; of 149 eligible pixels, **143 held
  below the confidence floor and 6 corrected** — the conservative default doing real work.
  Verified that no pixel outside the apply set moved. This checks the plumbing on real data;
  it is *not* a result, because the oracle's features are derived from the raster being
  corrected.
- **A full live run against real Earth Engine**, project `perseus-gee`, 2026-08-13 — the
  first time anything in `pipeline/s5_imagery/` has run against credentials. Same 144 ha AOI,
  real LF2022 EVT clip, real AlphaEarth 2022 embeddings, 42.7 s end to end:

  | | |
  |---|---|
  | Training | 2,099 px, 16 classes, 59 spatial blocks, 5 folds |
  | Blocked CV | **accuracy 0.534, macro F1 0.372** |
  | Shuffled baseline | accuracy 0.134, macro F1 0.053 |
  | Margin | **+0.399** — real signal, not class imbalance |
  | Corrections | 99 of 149 eligible px corrected, 50 held below the 0.60 floor |

  Per-class held-out F1 behaves the way physical sense predicts: Open Water 0.99, Developed-
  Roads 0.71, Nonriverine Basin Swamp 0.72, Longleaf Pine Woodland 0.60, Wet Flatwoods 0.49.
  74 of the 99 corrections went to Atlantic Coastal Plain Upland Longleaf Pine Woodland,
  which is the expected answer for ruderal grassland that is really regrowing pine.

  Also verified live: `AlphaEarthEmbeddings` returns unit-norm 64-vectors, correctly returns
  all-NaN for an offshore point, and holds row alignment under reordering; NAIP year
  availability over the test AOI is `[2005, 2006, 2007, 2010, 2013, 2015, 2017, 2019, 2021,
  2022]`; `NaipYearSlider` builds in 1.3 s, each year mosaics at 100% coverage with no
  gap-fill, exactly one NAIP layer stays visible, and the AOI stays on top.

## Observed limitation: implausible target classes

The live run reassigned **7 pixels from ruderal grassland to Open Water**. That is the
method working as specified and still being wrong: water is the most separable class in the
embedding space (F1 0.99), so wet or shadowed grassland lands there with high confidence. The
confidence floor cannot catch it because the model is genuinely confident.

The transition table exists to make this visible, and it did. **A grassland-to-water
correction is a flag, not a finding.** The fix — restricting which classes are *assignable*,
separately from which are *trusted for training* — is the clearest v2 change and is not
implemented.

## Open

- **No CLI.** Every sibling module in `pipeline/s5_imagery/` has one; this does not, because a
  GEE-dependent entry point could not be verified here. `correct_raster(request, source)` is the
  single-call path an `argparse` wrapper would wrap.
- **No committed example polygon.** The repo has only `config/extent.geojson` (all Florida), so
  the notebook falls back to a lon/lat `AOI_BBOX` parameter. A real stand polygon from the
  management-unit delineation would make it runnable as committed.
- **Restricting the assignable class vocabulary** — see the Open Water finding above. The
  clearest v2 change.
- **`cac.init_ee(project=...)` was needed and is now supported.** Earth Engine requires a
  Cloud project (`perseus-gee` here) and a stored credential does not carry one. The old
  blanket `except` sent a *working* token to `ee.Authenticate()`, which cannot supply a
  project — so a headless run hung on an interactive browser prompt instead of saying what
  was wrong. It now raises with the fix. This affects all five GEE notebooks, not just this one.
- **Confidence is a logistic-regression probability**, calibrated against its own training
  distribution and not against ground truth. `MIN_CONFIDENCE` is a knob to sweep, not a
  probability of being right.
