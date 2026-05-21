# GEE Scripts

Google Earth Engine scripts for raster acquisition and preprocessing.
All scripts use the Python `earthengine-api` + `geemap` stack.

Authenticate once with:
```bash
uv run earthengine authenticate
```

## Script inventory

| Script | Step | Output |
|--------|------|--------|
| `scripts/clip_treemap.py` | 1a | `treemap_2022_clipped.tif` exported to Drive/GCS |
| `scripts/terrain_3dep.py` | 2b | `slope.tif`, `aspect.tif`, `elevation.tif` |
| `scripts/soils_polaris.py` | 2a | `soil_awc.tif`, `clay_pct.tif`, `ph.tif` |
| `scripts/climate_prism.py` | 2c | `tmean.tif`, `precip.tif` |
| `scripts/lcms_export.py` | 3c | LCMS change stack for harvest model training |
| `scripts/ownership_clip.py` | 3c | `ownership_class.tif` clipped to Florida |

## Notes

- All exports target EPSG:5070, 30 m resolution, snapped to TreeMap grid.
- Use `crsTransform` not `scale` in GEE exports to guarantee pixel alignment.
- POLARIS asset path: `projects/sat-io/open-datasets/POLARIS/`
  (GEE community catalog — verify asset ID before running).
- Export to Google Drive for local pipeline, or GCS bucket for HPC-bound jobs.
