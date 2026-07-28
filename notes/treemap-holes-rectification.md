# Rectifying TreeMap 2022: recovering clearcut forest lost to LANDFIRE holes

Investigation of the ArcGIS exports on `/mnt/d` (2026-07-27) and the plan that
follows from them. Goal: find land that TreeMap 2022 treats as non-forest —
because LANDFIRE 2022 EVT calls it grassland/cropland — but which is actually
managed forest that happened to be clearcut around the TreeMap vintage.

Pipeline (all under `pipeline/s1_initial_state/`, tests alongside in `tests/`):

| step | module | needs GEE |
|---|---|---|
| 1. stratify holes by EVT forest evidence | `stratify_treemap_holes.py` | no |
| 2. draw labelled points (MMU + erosion + spatial blocks) | `sample_hole_points.py` | no |
| 3. sample AlphaEarth embeddings at those points | `embed_holes.py sample` | **yes** |
| 4. similarity mask + binary classifier + evaluation | `classify_holes.py` | no |
| 5. score the whole AOI server-side, download raster | `embed_holes.py apply` | **yes** |
| 6. final add-back mask + acreage report | `finalize_add_back.py` | no |

## The inputs that were exported

All four are 30 m COG GeoTIFFs on the NAD83 / Conus Albers grid, 4418 × 3527,
covering the Baker / Columbia / Hamilton / Suwannee / Union AOI
(1210125–1342665 E, 831795–937605 N).

| file | what it is |
|---|---|
| `TreeMap_Holes_CopyRaster` | value 1 where TreeMap 2022 has **no** `TM_ID`. **Not clipped to the AOI** — it is also 1 across the whole bounding box outside the county polygon (10.66 M px total, only 3.28 M of which are real in-AOI holes). |
| `Masked_Change_FL_AOI_16_22` | LANDFIRE EVT 2016→2022 transition, masked to TreeMap holes ∩ AOI |
| `Masked_Change_FL_AOI_22_24` | same, 2022→2024 |
| `Masked_Change_FL_AOI_16_24` | same, 2016→2024 |
| `Masked_Change_*_ExportTable.dbf` | byte-identical copies of the matching `.vat.dbf` |

Change-raster gotchas:

- The VAT decodes only the **changed** pixels. One extra raster value carries all
  "no change" pixels and is **absent from the VAT** (16_22 → 5965, 22_24 → 4970,
  16_24 → 5965). 43 % of 16_22 is that no-change code, so a VAT-only analysis has
  a large blind spot.
- All three share the same valid-data footprint: **3,282,389 px = 730,003 ac**.
  That footprint is exactly `TreeMap holes ∩ AOI`, so it is the usable definition
  of the hole universe (use it, not the unclipped holes raster).
- The AOI is 8.20 M px ≈ 1.82 M ac, so **40 % of the AOI is a TreeMap hole.**

To avoid the no-change blind spot, the pipeline reads the local
`LF{2016,2022,2024}_EVT_CONUS` tifs directly through a windowed read and derives
the transitions itself.

## Finding 1 — the local LF2016 download is Remap-legend, so the comparison is clean

`/mnt/d/LF2016_EVT_CONUS` is LF 2.0.0 ("2016 Remap"), whose codes match LF2022 and
LF2024 exactly (pasture 7997, ruderal grassland 9823, plantation 9322 in all
three; 1046 shared class names). This removes the constraint that forced
[[clearcut-vs-agriculture-embeddings]] Method 2 to bridge vintages at the coarse
"forest vs not" level — the EVT vintage published on Earth Engine is pre-Remap,
but the local one is not. **Do the EVT change analysis locally, not on GEE.**

## Finding 2 — LANDFIRE abandoned its "Recently Logged" classes (the root cause)

The three `Recently Logged-*` classes (7191 Herb, 7192 Shrub, 7193 Tree) exist in
the legend of all three vintages. Pixels assigned to them inside the AOI holes:

| vintage | Recently Logged pixels | acres |
|---|---|---|
| LF2016 | 259,885 | 57,798 |
| LF2022 | **0** | 0 |
| LF2024 | **0** | 0 |

The explicit "this was just logged" flag was populated in 2016 and is empty in
2022/2024; those pixels are now **Southeastern Ruderal Grassland** and
**Eastern Warm Temperate Pasture and Hayland**. Since TreeMap imputes only where
EVT is a tree lifeform, a harvested stand silently drops out of TreeMap with no
marker at all. This is the mechanism behind the holes, and it is why the fix has
to be inferential.

## Finding 3 — the holes stratify cleanly, and 28 % of them are recoverable forest

Forest evidence in 2016 = Tree lifeform (excluding urban/developed/orchard tree
classes, which TreeMap excludes by design) **or** any `Recently Logged-*` class.

| stratum | meaning | px | acres | % of holes |
|---|---|---|---|---|
| **S1** `cut_pre2016_regrown` | LF2016 Recently Logged → LF2024 tree | 186,820 | 41,549 | 5.7 % |
| **S2** `cut_2016_2022_regrown` | LF2016 natural tree → LF2024 tree | 51,396 | 11,430 | 1.6 % |
| **S3** `cut_2016_2022_open` | LF2016 forest evidence → still non-tree in 2024 | 363,047 | 80,742 | 11.1 % |
| **S4** `regrown_only` | no 2016 evidence → LF2024 tree | 316,975 | 70,495 | 9.7 % |
| **S5** `no_evidence` | neither endpoint is tree | 2,364,151 | 525,787 | 72.0 % |

**204,216 ac (28 % of holes, ~11 % of the whole AOI) carry forest evidence at one
or both endpoints.** S5 is genuine non-forest (pasture, row crop, developed,
marsh, water) and should stay a hole.

S1+S2 is the unambiguous signature — hole in 2022, forest at both ends. LF2022
calls it Southeastern Ruderal Grassland (66 %), ruderal shrubland, and pasture;
LF2024 calls it Forest Plantation (99,138 px), Interior Upland Longleaf Pine
Woodland (65,638), and Atlantic Coastal Plain Upland Longleaf (25,120). That is
silviculture, not land-use change.

## Finding 4 — the real problem is LANDFIRE's lag, not the 2022 vintage specifically

**78 % of the strong stratum (S1, 41,549 ac) was already `Recently Logged` in
LF2016.** Those stands were cut around/before 2016, are still mapped as ruderal
grassland in 2022, and only re-enter the forest classes in LF2024. So EVT takes
roughly **6–9 years** to re-recognise a replanted pine stand.

The consequence is bigger than the original framing: TreeMap is not just missing
stands cut *near* 2022, it is systematically missing the entire **0–8 year age
class of managed pine**. For an even-flow harvest scheduler that is the cohort
that determines the next rotation's supply, so its absence biases the whole
schedule.

*Needs verification:* the exact temporal window LANDFIRE means by "Recently
Logged" — assumed here to be roughly 0–10 years post-harvest, not confirmed
against LANDFIRE documentation.

## Finding 5a — S2 is almost entirely vintage-misregistration, not clearcut blocks

Interior fraction after a 5 ac MMU and one-pixel erosion is a shape diagnostic:
a real clearcut block survives erosion, a one-pixel sliver does not.

| stratum | eligible px | interior px | interior % | patches ≥5 ac |
|---|---|---|---|---|
| S1 | 186,820 | 51,536 | **27.6 %** | 1,432 |
| S2 | 51,396 | 1,194 | **2.3 %** | 97 |
| S3 | 363,047 | 41,070 | 11.3 % | 1,513 |
| S4 | 316,975 | 37,429 | 11.8 % | 1,417 |

S2 (`LF2016 natural tree → hole → LF2024 tree`) is 97.7 % edge. It is dominated
by single-pixel disagreement along stand boundaries between LANDFIRE vintages,
not by stands that were cut. **S1 is the real positive anchor population**;
sampling in proportion to genuine patch supply gives ~1409 S1 to ~91 S2, which
is correct rather than a sampling bug. Do not "fix" that imbalance by
over-weighting S2 — it would train the model on registration noise.

## Finding 5 — patch structure needs a minimum mapping unit

S1+S2 forms 31,101 connected components (8-connectivity): median 0.22 ac (a single
pixel), mean 1.70 ac, but **69.8 % of the acreage sits in patches ≥ 5 ac** (1,707
patches). The large patches are visually unmistakable clearcut blocks; the
small ones are stand-edge and road/riparian-corridor speckle. Any fix must apply
an MMU / morphological filter — do not treat single pixels as stands.

## Finding 6 — no donor stand exists for a young plantation, and older TreeMap vintages don't help

Plots imputed inside the AOI by stand-size class (FIA `STDSZCD`; 1 = large,
2 = medium, 3 = small diameter, 5 = nonstocked — confirmed by monotonic QMD
7.72 / 5.18 / 3.21 in):

| STDSZCD | plots | median BALIVE | median height |
|---|---|---|---|
| 1 large | 359 | 84.0 ft²/ac | 70 ft |
| 2 medium | 261 | 74.7 | 53 |
| 3 small | 255 | 29.7 | 29 |
| 5 nonstocked | **1** | 11.3 | 60 |

There is effectively **no young-stand donor in TreeMap's local imputation
universe** — by construction, since TreeMap can only impute where LANDFIRE
already says forest. So "just copy a nearby `TM_ID`" cannot represent a 2-year-old
plantation.

Inheriting from an older TreeMap vintage also fails: **TreeMap 2020 covers only
3.1 % of S1+S2 pixels** (and 15.3 % of S3, 2.0 % of all holes). TreeMap 2020 was
built on LANDFIRE 2020, which had already relabelled these stands as grassland,
so the holes are inherited across vintages. TreeMap 2016 could in principle cover
the S2 pixels (LF2016 natural tree) but not S1 (`Recently Logged` is Herb
lifeform, so TreeMap 2016 has no `TM_ID` there either) — an upper bound of
11,430 ac out of 52,979. It is only in the 5.4 GB
`/mnt/d/TreeMap-Vintage/TreeMap-2016.zip`, unextracted.

**Implication: the fix must synthesise a young stand, not copy one.**

## Plan

### Phase 1 — lock the evidence layer (done)

`pipeline/s1_initial_state/stratify_treemap_holes.py` writes
`data/interim/treemap_holes/treemap_hole_strata.tif` (uint8, codes 1–5) plus a
summary CSV, reproducing the table above from the LANDFIRE tifs.

### Phase 2 — the embedding funnel (built, blocked on Earth Engine auth)

Implemented as steps 2–6 in the table above. The design:

**Stage A, similarity mask.** Average the AlphaEarth embeddings of the clearcut
anchors (S1+S2) into a centroid and score every hole by cosine similarity.
AlphaEarth vectors are unit-length, so cosine is a plain dot product. The
threshold is not hand-picked — `--recall` sets the fraction of anchors the mask
must retain and the threshold is that quantile of anchor similarity. Recall
oriented: it narrows the universe, it does not decide.

**Stage B, binary classifier.** Logistic regression, `anchor_clearcut` (S1+S2)
against `anchor_nonforest` (S5 **restricted to herb / agriculture / shrub**).
Water, developed and barren are deliberately excluded from the negatives — they
make the problem trivially easy and inflate accuracy without teaching the model
anything about the pasture-vs-young-plantation boundary that actually matters.
Applied to S3+S4; a hole is proposed for add-back only if it clears **both**
stages.

Three deliberate guards, because every failure mode here is silent:

1. **Feature years are capped at 2022** (`embed_holes.MAX_FEATURE_YEAR`, raises
   if violated). The anchor label is *defined* by LF2024 calling the pixel
   forest, so a 2023/2024 embedding lets the model read the label off the
   feature — and would generalise disastrously to S3, which is defined as *not*
   tree in 2024. This is the same trap that drove AUC to 1.000 "largely by
   construction" in [[clearcut-vs-agriculture-embeddings]].
2. **GroupKFold on 0.25° blocks** plus a **label-shuffle baseline** printed
   beside every score. If real and shuffled AUC are within 0.15 the run prints a
   warning and the apply rates should not be trusted.
3. **S1/S2 are never re-scored by the model** in `finalize_add_back.py` — they
   are the training positives, and their forest status is already proven by
   LANDFIRE regrowth. Scoring your own anchors is circular; letting the model
   overrule ground evidence is worse.

**Known generalisation gap, to watch rather than assume away:** S1 anchors were
cut ~2014–2016, so in a 2022 embedding they are 6–8 year old pine. A *fresh*
clearcut looks very different, and much of S3 is fresh. The per-stratum apply
rates are reported separately so this shows up instead of hiding in an average.
If the S3 rate comes back implausibly low, that is the likely cause, and the fix
is to add age-referenced positives (sample an S1 anchor's embedding from a year
when it was young — e.g. 2017 for a 2015 cut) rather than to loosen thresholds.

The sklearn model is folded into a single dot product plus intercept and pushed
back to Earth Engine to score the AOI server-side, so the exported raster is the
fitted model exactly rather than a re-fit. `tests/test_classify_holes.py` asserts
that algebra to 1e-9.

### Phase 2b — corroborating evidence for S3

S3 (80,742 ac) is the largest recoverable stratum and the one matching the
original "clearcut near 2022" framing, but it has no regrowth confirmation: it is
a mix of *cut and replanted, not yet recognised by LF2024* and *genuinely
converted to pasture/crop*. The Phase-2 funnel decides it; these corroborate the
decision independently rather than being folded into the same model:

- **LCMS annual Tree Removal** — the repo's established clearcut definition.
  Caveat from [[clearcut-vs-agriculture-embeddings]]: LCMS tree-removal almost
  never fired on the statewide confused-class sample. Re-test it on these patches
  specifically before relying on it; corroboration, not a second detector.
- **The prior run's ranking is a free sanity check.** That work scored
  Southeastern Ruderal Grassland (9823) as the class most often mislabelling
  clearcut — and 66 % of S1+S2 is exactly that class in LF2022. Independent
  agreement from a different method and sample.

Also worth acquiring: **LANDFIRE Annual Disturbance (1999–2023)**, which carries
disturbance type and year per pixel. Not currently on `/mnt/d`. It would give the
harvest year directly instead of inferring it, and would collapse most of Phase 2
and 3a into a lookup. **This is the highest-value missing dataset.**

### Phase 3 — assign a stand to each accepted patch

For each patch that survives MMU + adjudication:

1. **Harvest year.** S1 → LF2016 `Recently Logged` implies ~2014–2016. S2 → between
   the 2016 and 2022 vintages. S3/S4 → from the embedding-change year. Refine with
   LANDFIRE Annual Disturbance if acquired.
2. **Site and species.** Take the modal `TM_ID` of the surrounding TreeMap pixels
   (a 7×7 dilation ring around the ≥5 ac patches already yields 509 distinct
   donor plots — slash pine 227, loblolly 74, longleaf 60 dominate). That donor
   supplies forest type, site productivity, and species mix.
3. **State at 2022.** Do **not** copy the donor's tree list. Run the donor stand
   through FVS (Southern variant `SN`), clearcut it at the estimated harvest year
   via `fvsCutNow`, regenerate at a planting density informed by the TPO harvest
   guidance, and grow it forward to 2022. Store the resulting tree list as a
   synthetic `TM_ID`. This reuses the machinery the LETO/ARTEMIS workflow already
   needs (see [[treemap-fvs-workflow]] and [[management-pipeline-plan]]) and
   produces a stand that is physically consistent with its site rather than
   borrowed from an unrelated age class.

### Phase 4 — validate before adopting

- Hand-label a sample of patches against NAIP imagery (the follow-up already
  flagged in [[clearcut-vs-agriculture-embeddings]] and still not done).
- Check the corrected forest area against FIA design-based estimates for the five
  counties using the existing `1_FL_FIA_TreeMap_comparison.R` approach — the
  correction should move TreeMap's area *toward* the FIA estimate, and the
  age-class distribution should gain the missing 0–8 year cohort. If it
  overshoots FIA forest area, the detection is too loose.

## Results of the first full run (2026-07-27)

Auth restored, all six steps executed. 4,500 points × 3 embedding years, 0 missing
bands, L2 norm 1.0001 ± 0.002, no duplicate coordinates.

**Stage A** (6 exemplars, k-means over anchor years 2018+2022): threshold 0.9046
at 90 % anchor recall; clearcut anchors median similarity 0.9541, stable
non-forest 0.8241 and only **11.6 %** of it passes.

**Stage B**: block-CV **AUC 0.9818, accuracy 0.9433**, label-shuffle AUC 0.4829.
The shuffle baseline sitting on 0.5 is what makes the headline number
believable — the model is learning land cover, not geography.

**Final add-back: 75,792 ac of 730,003 ac of holes (10.4 %)**, in 2,975 patches,
median 13.6 ac, mean 25.5 ac, max 377 ac — realistic harvest-unit sizes. The
MMU removed 25,673 ac of speckle.

| stratum | hole acres | accepted | after MMU | % of stratum |
|---|---|---|---|---|
| S1 cut_pre2016_regrown | 41,549 | 41,549 | **38,786** | 93.3 % |
| S2 cut_2016_2022_regrown | 11,430 | 11,430 | **2,097** | 18.3 % |
| S3 cut_2016_2022_open | 80,742 | 13,112 | **8,079** | 10.0 % |
| S4 regrown_only | 70,495 | 35,374 | **26,830** | 38.1 % |
| S5 no_evidence | 525,787 | 0 | **0** | 0 % |

### The age-referencing fix, and the evidence it was needed

The first run trained on 2022 anchors against a single centroid and returned
**S3 18.3 % / S4 83.0 %** — the predicted generalisation gap, made visible. S1
anchors are 6–8 year old pine in a 2022 embedding; S4 is similar-aged regrowth so
it matched, while S3 is *fresh* cuts that look nothing like established pine. Two
changes, both using data already sampled:

1. **Age-referenced training** (`--anchor-years 2018 2022`) stacks each anchor
   once per year. S1 was cut ~2014–2016, so its 2018 embedding shows it at age
   ~2–4 and its 2022 embedding at age ~6–8; the model then learns "post-harvest
   managed forest at any age". S3 mean probability 0.244 → 0.394.
2. **Multi-exemplar Stage A** (`--anchor-clusters 6`). A single centroid averaged
   over ages 2–8 represents neither end. Counter-intuitively this made Stage A
   *more* selective, not less: at equal 90 % anchor recall, stable non-forest
   leakage fell **30.3 % → 11.6 %**. Better discrimination on the classes we have
   labels for, so it is the default.

Net effect on the target stratum: S3 add-back 0.183 → 0.266.

### Validation against FIA — the correction is conservative

FIA design-based forest area for the five counties (EVALID 122201, `EXPCURR`,
333 plots, `CONDPROP_UNADJ × ADJ_FACTOR × EXPNS`):

| quantity | acres | % of AOI |
|---|---|---|
| AOI extent | 1,824,689 | — (FIA total 1,803,585, agrees to 1.2 %) |
| TreeMap 2022 forest | 1,094,686 | 60.0 % |
| **FIA forest, circa 2022** | **1,255,424** | **69.6 %** |
| TreeMap shortfall | 160,738 | — |
| our add-back | 75,792 | **47 % of the shortfall** |
| corrected TreeMap forest | 1,170,478 | 64.1 % |
| still unexplained | 84,946 | — |

This is the strongest evidence available without field data: the correction moves
TreeMap **toward** the independent design-based estimate and **does not
overshoot**. If detection were too loose it would have blown past 1,255,424 ac.

Plausible homes for the remaining 84,946 ac: the 72,663 ac of S3 we rejected, the
9,333 ac of S2 lost to the MMU, and the ~40,487 ac of holes where LF2022 says
*Urban/Developed Evergreen Forest* — excluded from the strata by design as
non-FIA tree classes, though FIA's forest definition would count part of it.

### Known caveats on these numbers

- **The model works at 10 m, the product at 30 m.** Points were sampled at
  AlphaEarth's native 10 m; the exported raster is 30 m to match TreeMap's grid.
  Agreement between the two is r = 0.988 (probability) / 0.991 (similarity), with
  97.7 % decision agreement at 0.5 — the residual is aggregation, not a bug.
- **S3's true positive rate is still unknown.** 10 % accepted is defensible and
  conservative, but nothing here proves it is *right*. NAIP hand-labelling of S3
  patches remains the missing validation.
- **Earth Engine notebook-mode credentials expire in 7 days.** Re-run
  `uv run earthengine authenticate` (localhost mode) for durable access.

### Blocker (resolved 2026-07-27)

The stored refresh token had expired (`invalid_grant`), contradicting
`notes/clearcut-vs-agriculture-embeddings.md`, which records auth working as of
2026-07-01. Re-authenticated via notebook mode. **Those credentials last 7 days**
— use `uv run earthengine authenticate` (localhost mode) for durable access.

Two Earth Engine export limits hit along the way, both handled in
`embed_holes.py`:

- **50 MB per download request.** The whole AOI is ~62 MB, and even after tiling
  the 6-exemplar similarity plus the 64-band dot product tripped *"User memory
  limit exceeded"*. Six horizontal strips is the working configuration
  (`--tiles 6`).
- **Earth Engine rounds each requested region outward**, so tiles return a row or
  column larger than asked and overlap. Concatenating them drifts the grid off
  TreeMap's — every tile is placed by its own geotransform onto a canvas sized
  from `AOI_BOUNDS_5070` instead.

### Open decisions

- **Scope**: adopt S1+S2 only (52,979 ac, high confidence) or push through the S3
  adjudication for the full 204,216 ac? S3 is where most of the acreage and all
  of the risk is.
- **Representation**: synthetic `TM_ID`s appended to the TreeMap VAT, or a
  separate "regenerating stand" layer that ARTEMIS consumes alongside TreeMap?
  The latter keeps the published product unmodified.
- **Extent**: fix the 5-county AOI only, or design for all of Florida / the
  Southeast from the start? The stratification script is AOI-agnostic; only the
  ArcGIS-exported masks are AOI-bound, and those are now reproducible from the
  LANDFIRE tifs.
