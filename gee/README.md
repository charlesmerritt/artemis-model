# Google Earth Engine exports

`gee/scripts/` prepares remote raster inputs for ARTEMIS with the Python
`earthengine-api`. Exports target the Florida extent and are intended to be aligned to the
project's EPSG:5070, 30 m grid.

## Setup

```bash
uv sync
uv run earthengine authenticate
```

Pass `--project YOUR_GEE_PROJECT` when the default Earth Engine project is not configured.

## Script inventory

| Script | Purpose | Useful options |
|---|---|---|
| `scripts/export_lcms.py` | Export annual LCMS land-cover and change bands | `--start-year`, `--end-year`, `--folder` |
| `scripts/export_polaris.py` | Export depth-weighted POLARIS soil properties | `--folder` |
| `scripts/export_prism.py` | Export PRISM 1991–2020 climate normals | `--folder` |
| `scripts/export_terrain.py` | Export elevation, slope, aspect, and optional TPI | `--dem-source`, `--export-tpi`, `--folder` |
| `scripts/gee_utils.py` | Shared initialization, extent, grid, and export helpers | Imported by the export scripts |

Examples:

```bash
uv run python gee/scripts/export_lcms.py --start-year 1985 --end-year 2024
uv run python gee/scripts/export_polaris.py
uv run python gee/scripts/export_prism.py
uv run python gee/scripts/export_terrain.py --dem-source 3dep --export-tpi
```

Each command submits Earth Engine export tasks; monitor and start tasks in the Earth Engine task
manager when required. Use `--help` on a script for its current options.

## Alignment and provenance

- Keep categorical rasters categorical when reprojecting or resampling.
- Use the shared grid helpers rather than independent `scale` settings so outputs remain aligned.
- POLARIS uses the GEE community catalog; verify asset availability before a production run.
- Record export dates and source versions in project notes until a formal dataset lock file is
  implemented.
