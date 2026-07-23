# Segmentation delineation comparison — Union County (task E)

An AOI-scoped run of the raster-segmentation delineation research, comparing two
segmentation strategies against the naive parcel-based delineation for Union County
(FIPS 12125). This is the experiment `research/mgmt_units/STATE.md` had staged but
never run.

## How it was run

`research/mgmt_units/run_segmentation_aoi.py` reuses the committed
`segmentation_delineation.py` functions but scopes them to one county using
pre-clipped, already-projected (EPSG:5070) layers pulled from the R2 `/mnt/d`
replica — no full-CONUS LANDFIRE download, no statewide GDBs. Two fixes were
required to make the committed code path actually run:

1. **Forest mask.** The committed `create_forest_mask()` selects EVT codes
   1000–2999. The real LANDFIRE 2022 EVT for this AOI uses codes 7292+, so that
   heuristic returns **zero** forest pixels. We use the pipeline's authoritative
   pre-computed `landfire_evt_forest_mask_5070.tif` (505,199 forest pixels) instead.
2. **BMP erase.** Rather than re-deriving buffers from statewide GDBs, we erase
   with the exact `road_buffers` / `stream_buffers` / `waterbody_buffers` the naive
   pipeline already saved to `qa_layers.gpkg` — the same cuts the naive units got,
   so the comparison is fair.

Command:

```bash
uv run python research/mgmt_units/run_segmentation_aoi.py \
  --aoi-dir data/interim/management_units_smoke_union/12125_union
```

## Result

`segmentation_strategy_comparison.csv` (also `research/mgmt_units/outputs/strategy_comparison.csv`):

| strategy | units | slivers <2 ha | sliver % | median ha | median compactness |
|---|---:|---:|---:|---:|---:|
| Naive (parcel ∩ forest) | 17,020 | 14,852 | 87.3% | 0.10 | 0.459 |
| Felzenszwalb | 21,210 | 19,181 | 90.4% | 0.09 | 0.526 |
| SLIC | 11,721 | 10,212 | 87.1% | 0.10 | 0.503 |

Figures: `segmentation_strategies_map.png` (three-panel county map),
`research/mgmt_units/outputs/strategy_comparison.png` (four-panel distribution plots).

## What it says (the honest read)

**Raster segmentation with default parameters does not solve the fragmentation
problem.** All three strategies are ~87–90% sub-2 ha slivers. Felzenszwalb is
actually *more* fragmented than the naive baseline; SLIC produces slightly fewer,
slightly larger units. Segmentation does buy a modest shape-quality gain —
median Polsby-Popper compactness rises from 0.46 (naive) to 0.50–0.53 — but not a
usable size distribution.

The reason is structural: the sliver count is driven mostly by the **BMP/road/water
erase shattering** whatever polygons it cuts, not by the delineation method that
produced them. So the sliver-resolution step (task C) remains necessary regardless
of how units are first drawn.

## Caveats

- **Default parameters, untuned.** `STATE.md` flags Felzenszwalb `scale=100` and
  SLIC `n_segments=1000` as guesses; they were not tuned toward a 5–10 ha median.
  This run is a baseline, not a verdict on segmentation.
- The committed segmentation stack uses EVT + forest mask + a constant band;
  TreeMap plot-ID and ownership bands are not yet wired in.
- The fair next comparison is **segmentation → sliver-merge**, i.e. run task C's
  merge on each segmentation output and compare the *resolved* maps, plus a
  parameter sweep.

## Files

| File | What |
|---|---|
| `segmentation_strategy_comparison.csv` | the summary table above |
| `segmentation_strategies_map.png` | three-panel Union County map (naive / Felzenszwalb / SLIC) |
| `../../research/mgmt_units/run_segmentation_aoi.py` | the AOI-scoped runner |
| `../../research/mgmt_units/outputs/strategy_comparison.{csv,png}` | the committed script's own comparison outputs |
