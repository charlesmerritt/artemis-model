# Notebooks

These notebooks are exploratory interfaces around ARTEMIS data acquisition, classification, and
validation. They are not a linear pipeline. Run them from the repository root so relative imports
and paths resolve consistently:

```bash
uv sync
uv run jupyter lab
```

## Notebook groups

| Entry point | Purpose | Main prerequisites |
|---|---|---|
| `TreeMap_COG_County_Summary.ipynb` | Windowed zonal summaries from a remote COG or STAC item | Network access |
| `Vector-Guided-Raster-Correction.ipynb` | Correct a classification raster (LANDFIRE EVT) inside a polygon: NAIP year slider for visual QA, then an embedding classifier reassigns the eligible classes | Earth Engine + the Florida EVT clip (see below) |
| `Embedding-Similarity-AOI-Finder.ipynb` | Find regions similar to selected clearcut references using AlphaEarth embeddings | Earth Engine authentication |
| `Clearcut-vs-Agriculture-Embeddings.ipynb` | Test embedding separation between recent clearcuts and agriculture | Earth Engine + local LANDFIRE data |
| `Clearcut-vs-Agriculture-EVT-Change.ipynb` | Compare forest-to-herb/agriculture/shrub EVT change with LCMS evidence | Earth Engine + local LANDFIRE data |
| `Clearcut-Grassland-Feature-Engineering.ipynb` | Assemble model features and spatial cross-validation baselines | Earth Engine + local LANDFIRE data |
| `Similarity-Embeddings.ipynb` | Original similarity prototype | Superseded; retained for reference |
| `FVS_5county_growth_smoke.ipynb.old` | Retired five-county FVS smoke workflow retained as a recovery reference | External TreeMap/FIA data and missing FVS helper modules |

`clearcut_ag_common.py` contains shared helpers for the embedding and clearcut notebooks.

`Vector-Guided-Raster-Correction.ipynb` is a thin interface over three
[`pipeline/s5_imagery/`](../pipeline/s5_imagery/) modules rather than a self-contained notebook —
`raster_correction.py` (windowing, sampling, spatially blocked CV, correction, manifests),
`feature_sources.py` (the `FeatureSource` protocol and its AlphaEarth implementation), and
`naip_viewer.py` (the ±N-year window resolver, the hatched-border geometry, and the slider widget).
The `FeatureSource` protocol is what lets the workflow run without Earth Engine against a
fixture source and a synthetic raster. Design and limits:
[`../pipeline/s5_imagery/raster_correction.py`](../pipeline/s5_imagery/raster_correction.py).

It reads a **Florida clip of the EVT raster** rather than the 2.99 GB CONUS original — 75 MB,
same CRS and 30 m grid, pixel-identical. Produce it once with:

```bash
uv run python -m pipeline.raster_clip \
    --raster raw.landfire.evt_tif --region config/extent.geojson --name LF2022_EVT_FL
```

The notebook falls back to the CONUS raster when the clip is absent. See
[`../pipeline/raster_clip.py`](../pipeline/raster_clip.py).

## Before running

1. Stage the local TreeMap, FIA, or LANDFIRE files these workflows read. Paths are configured in
   [`../config/data_paths.yaml`](../config/data_paths.yaml); with the `/mnt/d` mount absent,
   `cac.resolve(declared_path)` — `cac.resolve_dir` for shapefiles and geodatabases — fetches from
   the R2 mirror instead. Anything over the 512 MB cap, notably the 3 GB LF2022 EVT raster, raises
   with the one `rclone` command that stages it rather than pulling it mid-cell.
2. Authenticate Earth Engine interactively when required:

   ```bash
   uv run earthengine authenticate
   ```

3. Avoid committing generated outputs or embedded map-widget state. Large notebook state can
   increase a notebook by many megabytes.

## Detailed notes

- [Clearcut versus agriculture and embedding workflows](../pipeline/s1_initial_state/embed_holes.py)
- [TreeMap-to-FVS workflow](../pipeline/s4_fvs/build_fvs_inputs.py)

The FVS smoke notebook is not a runnable entry point: its helper modules are absent from the
repository. Its recovery notes are in `git show e207953:notes/fvs-5county-growth-smoke.md`.
