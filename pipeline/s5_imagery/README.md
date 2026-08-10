# s5 — imagery and embeddings

Pulls NAIP imagery over a study area, generates Earth Engine embeddings across it, compares how
those embeddings cluster inside versus outside the area of interest, and publishes the result to
the [PERSEUS map viewer](https://github.com/charlesmerritt/map-viewer).

## Two vector layers

This stage takes two vector layers, and the separation is the point rather than a convenience:

| Layer | Flag | What it is |
|---|---|---|
| **Imagery extent** | `--extent` | The footprint imagery must completely cover. |
| **Area of interest** | `--aoi` | The actual features on the ground under study. |

NAIP arrives as quarter-quad tiles and any mosaic covering a study area necessarily spills past
it. That spill is not waste — it is the control group. Embeddings are generated across the whole
extent and then split by the AOI, so "do the features we care about look different from their
surroundings?" becomes a measurable question.

Supplying only `--aoi` derives an extent from it (`--derive-extent bbox|hull|buffer`), so a
single-layer workflow still works. Supplying only `--extent` skips the inside/outside analysis.

## Modules

| Module | Purpose |
|---|---|
| `vectors.py` | Shared layer loading, extent derivation, area/containment math. Geometry leaves in EPSG:4326; area and buffers route through EPSG:5070. |
| `naip_acquire.py` | NAIP mosaics per year, with an explicit extent-coverage check and gap filling. |
| `embeddings.py` | AlphaEarth embeddings, balanced inside/outside sampling, k-means, separability statistics. |
| `viewer_catalog.py` | Turns the above into viewer catalog files. |

## Coverage is checked, not assumed

NAIP is flown state-by-state on a two- to three-year cycle. Asking for one year over an arbitrary
polygon routinely returns imagery for part of it and nothing for the rest, and a mosaic with
holes looks fine on a map while quietly corrupting anything downstream.

`naip_acquire.py` measures, for every requested year, what fraction of the extent actually has
imagery, and records it in the manifest. `--coverage-mode` decides what happens next:

| Mode | Behaviour |
|---|---|
| `fill` (default) | Fill holes from the nearest years within `--fill-window`, newest first, adding one year at a time until covered. Every contributing year is recorded. |
| `strict` | Fail if a requested year cannot cover the extent on its own. |
| `report` | Record the fraction and carry on with a partial mosaic. |

A gap-filled year is flagged distinctly from a fully covered one in both the manifest and the
viewer panel, because part of that mosaic is imagery from another year and anyone reading change
between years needs to know.

NAIP is deliberately **not** snapped to the project's 30 m TreeMap grid (`gee/scripts/gee_utils.py`).
It is sub-meter reference imagery for viewing and for context around clusters, not a 30 m
analysis raster.

## Commands

```bash
# Three years, streamed to the viewer as Earth Engine tiles, nothing exported
uv run python -m pipeline.s5_imagery.naip_acquire \
  --extent config/study_extent.geojson --aoi config/stands.geojson \
  --years 2019,2021,2023 --dest none

# Every other year, exported as COGs to Drive
uv run python -m pipeline.s5_imagery.naip_acquire \
  --extent config/study_extent.geojson \
  --start-year 2015 --end-year 2023 --year-step 2 --dest drive

# Every NAIP year available over the extent
uv run python -m pipeline.s5_imagery.naip_acquire \
  --aoi config/stands.geojson --derive-extent buffer --buffer-m 500 --all-available

# Embeddings and inside/outside clustering
uv run python -m pipeline.s5_imagery.embeddings \
  --extent config/study_extent.geojson --aoi config/stands.geojson \
  --year 2024 --k 6

# Publish both to the viewer
uv run python -m pipeline.s5_imagery.viewer_catalog \
  --naip-manifest data/interim/naip/stands/naip_manifest.json \
  --clusters data/interim/embeddings/stands/clusters.json

uv run python viewer/serve_viewer.py
```

Use `--help` on any module for its full options. Earth Engine authentication is required:

```bash
uv run earthengine authenticate
```

## Outputs

```text
data/interim/naip/<slug>/
  naip_manifest.json      Per-year coverage, contributing years, tile URLs, export tasks
  extent.geojson          Imagery extent, dissolved, EPSG:4326
  aoi.geojson             Area of interest, dissolved, EPSG:4326

data/interim/embeddings/<slug>/
  clusters.json           Chart payload: per-cluster inside/outside counts, divergence, PCA
  samples.csv             One row per sample: lon, lat, inside, cluster, pc1, pc2
  extent.geojson, aoi.geojson

data/interim/viewer/<slug>/
  layers.json             Viewer built-in catalog (COG and GeoJSON layers only)
  artemis/catalog.json    Panel catalog (tile URLs, coverage, charts)
  artemis/*.geojson
```

## How the inside/outside comparison works

1. Take the AlphaEarth annual embedding (64 bands, 10 m) across the whole extent.
2. Draw a stratified sample balanced between inside and outside, so the clustering is not
   dominated by whichever side is larger.
3. Fit k-means in Earth Engine on **embedding bands only** — the inside/outside flag is withheld
   from training, so the clusterer is not handed the boundary it is being evaluated against.
4. Apply the clusterer to the sample and to the full extent, then compare cluster composition
   across the boundary.

The headline statistic is the Jensen-Shannon divergence between the two cluster distributions,
bounded 0 to 1. It is symmetric and finite even when one side has no mass in a cluster, which is
why it is used instead of KL divergence.

**What this does and does not establish.** A high divergence says the AOI boundary coincides with
a change in what the satellite sees. It does not say the AOI boundary is *correct*, and it does
not identify what the difference is — a fence line, a road, a soil change, and a management
boundary all separate cleanly in embedding space. Cluster identity is unsupervised and unlabeled;
`samples.csv` and the NAIP layers are there so clusters can be inspected against imagery before
anyone reads meaning into them.

## Verification

```bash
uv run pytest tests/test_s5_vectors.py tests/test_s5_naip_acquire.py \
  tests/test_s5_embeddings.py tests/test_s5_viewer_catalog.py tests/test_serve_viewer.py
```

Tests cover the pure logic: temporal-parameter resolution, gap-fill year ordering, extent
derivation, coverage and containment math, cluster statistics, PCA, and catalog construction.
Earth Engine calls are not mocked and are not covered — they need credentials and a live service.
