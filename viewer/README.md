# ARTEMIS viewer panel

A drop-in side panel for the [PERSEUS map viewer](https://github.com/charlesmerritt/map-viewer)
that adds NAIP imagery layers, the two ARTEMIS vector layers, the embedding cluster raster,
and inside-versus-outside clustering charts.

The panel is a guest in the viewer. It builds its own DOM, reads only `window.AppState` and
`window.Layers`, and changes nothing in the viewer's own files — so the viewer can be updated
independently without breaking it.

## Files

| File | Purpose |
|---|---|
| `artemis-panel-core.js` | Pure data transforms: chart shaping, coverage badges, tile-age math. No DOM. |
| `artemis-panel.js` | Panel DOM, Chart.js wiring, layer adds, collapse tab. |
| `artemis-panel.css` | Styling, reusing the viewer's design tokens. |
| `serve_viewer.py` | Assembles a viewer + panel + catalog build and serves it. |
| `tests/test-artemis-panel-core.mjs` | Node test for the pure helpers. |

## Running it

`serve_viewer.py` copies the viewer's `public/` tree into a build directory, overlays the
panel assets and the generated catalog, injects two tags into `index.html`, and serves the
result. The viewer checkout is never modified.

```bash
# Generate the catalog first
uv run python -m pipeline.s5_imagery.viewer_catalog \
  --naip-manifest data/interim/naip/<slug>/naip_manifest.json \
  --clusters data/interim/embeddings/<slug>/clusters.json

# Build and serve on http://127.0.0.1:8000
uv run python viewer/serve_viewer.py

# Against a local viewer checkout, on another port
uv run python viewer/serve_viewer.py --map-viewer ~/src/map-viewer --port 8080

# Build without serving (e.g. to publish the directory elsewhere)
uv run python viewer/serve_viewer.py --no-serve
```

Without `--map-viewer` the script looks for `$MAP_VIEWER_DIR`, then
`data/interim/map-viewer`, and shallow-clones the repo there if it finds neither. Pass
`--no-clone` to require a local checkout instead.

With no catalog present the panel still loads and shows the commands needed to produce one.

## Installing it into the viewer permanently

If the panel should ship with the viewer rather than being overlaid at serve time, copy the
three asset files into the viewer's `public/` and add two tags to `public/index.html`:

```html
<!-- in <head>, after styles.css -->
<link rel="stylesheet" href="artemis-panel.css" />

<!-- at the end of <body>, after app.js -->
<script src="artemis-panel-core.js"></script>
<script src="artemis-panel.js"></script>
```

Script order matters: the core module must load before the panel, and both must load after
`app.js`, because the panel reads `window.AppState` and `window.Layers` as it initializes.

## Catalog location

The panel fetches `artemis/catalog.json` relative to the viewer's document root. Override it
for a dev copy without rebuilding:

```
http://localhost:8000/?artemisCatalog=https://example.com/catalog.json
```

or `localStorage.setItem("artemisCatalog", "…")`. This mirrors how the viewer's own TiTiler
endpoints are overridden.

## What the charts say

| Chart | Reading |
|---|---|
| **Cluster share: inside vs outside** | Share of each side's samples per cluster, normalized within each side so the two are comparable at unequal sample counts. Equal bars mean the AOI boundary does not register in embedding space. |
| **Composition of each cluster** | Of the samples in a cluster, how many are inside the AOI. Bars near 50% are shared with the surroundings; bars near 0 or 100 belong to one side. |
| **Embedding space (PCA)** | Sampled embeddings on their first two principal components. Color is cluster; filled circles are inside the AOI, outlined triangles outside. |

The headline number is the Jensen-Shannon divergence between the inside and outside cluster
distributions, bounded 0 to 1. Zero means the two sides are the same population in embedding
space; one means they share no cluster at all.

## Layer sources and their lifetimes

NAIP years and the cluster raster reach the map two ways:

- **Exported COGs** — durable, and the viewer's zonal statistics work over them. Requires the
  files to be hosted somewhere CORS-enabled, declared with `--public-base` when the catalog is
  built.
- **Earth Engine tile URLs** — instant, nothing to export or host, but Earth Engine map IDs
  expire. The panel shows how long ago each was minted and warns past a day.

The panel prefers a COG whenever one is available. Only COG-backed layers are written into the
viewer's `layers.json`, since that file is durable configuration and a dead URL there is worse
than no entry.

## Testing

```bash
node viewer/tests/test-artemis-panel-core.mjs   # pure panel helpers
uv run pytest tests/test_serve_viewer.py        # build and injection
```

The panel's DOM layer has no automated coverage; it is verified by loading the built viewer in
a browser.
