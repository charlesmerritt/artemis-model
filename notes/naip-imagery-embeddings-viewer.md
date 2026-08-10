# NAIP imagery, embedding clustering, and the map-viewer connection

Status notes for `pipeline/s5_imagery/` and `viewer/`. Stable operating instructions live in
[`../pipeline/s5_imagery/README.md`](../pipeline/s5_imagery/README.md) and
[`../viewer/README.md`](../viewer/README.md); this file records decisions, findings, and open
questions.

## Why two vector layers

The stage takes an **imagery extent** and an **area of interest** separately. NAIP is delivered as
quarter-quad tiles, so any mosaic covering a study area spills past it. Rather than trim the
spill, it is used: embeddings are generated across the whole extent and split by the AOI, making
"do the features we care about look different from their surroundings?" measurable.

Consequence worth remembering: `--derive-extent bbox` on an AOI that is itself a rectangle still
leaves usable outside area, because EPSG:5070 rotates the lon/lat graticule enough that the
projected envelope is ~20% larger at Florida longitudes. Passing the same file as both `--extent`
and `--aoi` does not, and is refused (`MIN_OUTSIDE_FRACTION = 0.05`).

## Coverage checking is the load-bearing part of NAIP acquisition

NAIP is flown state-by-state on a two- to three-year cycle. Requesting a single year over an
arbitrary polygon routinely returns imagery for part of it and nothing for the rest. A holed
mosaic renders fine and silently corrupts anything downstream that assumes full coverage.

Coverage is measured server-side as the mean of the mosaic's unmasked footprint over the extent,
at `--coverage-scale` (default 30 m, matching the project grid — a 1 m coverage check over a
county is billions of pixels to answer a yes/no question). Unmasking to 0 before reducing is
load-bearing: `reduceRegion` skips masked pixels, so a mean over the raw mask reports 1.0 for a
mosaic covering one corner of the extent.

`--coverage-mode fill` (the default) adds neighbour years one at a time, re-measuring after each,
and drops any that contributed nothing. That keeps `contributing_years` in the manifest to the
minimum set actually needed rather than everything inside the window. Ties in distance go to the
newer year.

Gap-filled years are flagged distinctly from fully covered ones everywhere they surface. A
gap-filled 2023 mosaic containing 2022 pixels is not the same product as a clean 2023 mosaic, and
year-over-year change read across it will be wrong in exactly the filled areas.

## Embedding clustering

- AlphaEarth `GOOGLE/SATELLITE_EMBEDDING/V1/ANNUAL`, 64 bands at 10 m, annual 2017 onward — the
  same asset the clearcut/agriculture work uses (`notebooks/clearcut_ag_common.py`), kept in sync
  deliberately so embedding-space results stay comparable across the project.
- Sampling is `stratifiedSample` on a painted 0/1 inside-AOI band, so the inside/outside balance
  is guaranteed by construction rather than by luck of the draw.
- **k-means trains on embedding bands only.** The inside/outside flag is withheld from training.
  Handing the clusterer the boundary it is being evaluated against would make the result
  circular — the same trap documented for featsets B and C in
  [`clearcut-vs-agriculture-embeddings.md`](clearcut-vs-agriculture-embeddings.md), where
  AUC ≈ 1.0 turned out to be true by construction.
- Separability is Jensen-Shannon divergence (base 2, bounded 0–1) between the inside and outside
  cluster distributions. Symmetric and finite when one side has zero mass in a cluster, which KL
  is not.
- PCA for the scatter chart is plain numpy SVD. `scikit-learn` is present transitively via
  `mapclassify` but is not a declared dependency, and this is three lines of numpy.

**Interpretation limit, worth restating before anyone quotes a divergence number.** High
divergence says the AOI boundary coincides with a change in what the satellite sees. It does not
say the boundary is correct, and it does not say what the difference is — a fence line, a road, a
soil change, and a management boundary all separate cleanly in embedding space. Clusters are
unsupervised and unlabeled. `samples.csv` and the NAIP layers exist so clusters can be checked
against imagery before meaning is read into them.

## Viewer connection

The viewer (`charlesmerritt/map-viewer`) is a separate static MapLibre app with no build step.
The connection is deliberately non-invasive:

- `viewer_catalog.py` writes the viewer's `layers.json` plus an `artemis/catalog.json` for the
  panel.
- `viewer/serve_viewer.py` copies the viewer's `public/` into a build directory, overlays the
  panel assets and catalog, injects two tags into `index.html`, and serves it. **The viewer
  checkout is never modified**, so it can be updated independently.
- The panel touches the viewer only through `window.AppState` and `window.Layers`.

Two findings from wiring it up:

- The viewer's roadmap lists "no XYZ/WMTS layer support", but `Layers.addD2STileLayer()` takes a
  raw `tileUrl` and adds a MapLibre raster tile source. That is XYZ support, just not exposed in
  the UI — which is what lets Earth Engine tile URLs render without exporting anything.
- The viewer paints every GeoJSON layer the same green. The panel repaints extent and AOI after
  adding them, because telling the two apart on the map is the entire point here.

Earth Engine tile URLs expire. They are recorded with a mint timestamp, the panel shows the age
and warns past 24 h, and they are deliberately kept **out** of `layers.json` — that file is
durable configuration, and a dead URL in it is worse than no entry. Exported COGs are the durable
path, and the panel prefers one whenever available.

Drive exports have no derivable public URL, so `--public-base` is required to turn them into
viewer layers. GCS exports resolve themselves from bucket and object.

## Verified so far

- Full test suite green: 250 passed, 22 skipped. New coverage is 104 assertions across
  `tests/test_s5_*.py` and `tests/test_serve_viewer.py`, plus a node test for the panel helpers.
- End-to-end rehearsal on synthetic data through the real code paths (manifest builder, chart
  payload builder, catalog bridge, viewer build), rendered in headless Chromium: panel mounts,
  all four sections render, coverage chips read `partial coverage` / `full coverage` /
  `gap-filled` correctly, all three charts draw, the collapse tab toggles both ways.
- **Not yet run against Earth Engine.** This environment has no credentials, so every `ee.*` path
  — coverage measurement, gap fill, stratified sampling, `wekaKMeans`, export submission — is
  unexercised. That is the first thing to do on a machine with `earthengine authenticate`
  completed.
- MapLibre and Chart.js load from CDNs the sandbox blocks, so the map itself never rendered
  during verification. Chart.js was vendored locally to confirm the chart code path.

## Open questions

- **Band sets.** `--bands rgbn` selects NIR, but NAIP band availability varies by year and state,
  and a year mixing 3- and 4-band DOQQs will fail to mosaic. Default is `rgb`. Whether to filter
  the collection server-side by band count, or just document the failure, is unresolved.
- **Export CRS.** Defaults to EPSG:4326 rather than the project's EPSG:5070, since NAIP is
  reference imagery, not a 30 m analysis raster. If NAIP ever feeds analysis rather than viewing,
  revisit.
- **k selection** is a user parameter with no guidance. A silhouette sweep over k would make the
  choice defensible rather than arbitrary.
- **Sample size vs. cost.** `--n-samples` defaults to 1500 per side; samples are pulled to the
  client in 500-feature pages because `getInfo` fails outright on large collections. Very large
  extents may want a coarser `--scale` instead of more samples.
- **Cluster stability** across years is untested. The clusterer is fit per run, so cluster IDs are
  not comparable between two runs — including two years of the same AOI. Comparing years needs a
  fitted clusterer carried across them.
