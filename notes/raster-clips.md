# Regional raster clips (`pipeline/raster_clip.py`)

**Added 2026-08-13.** The project's source rasters are CONUS-wide and Florida is 4% of
CONUS. `pipeline/raster_clip.py` cuts a region out of one, keeping the source CRS, grid
alignment, dtype and nodata, so the clip stays pixel-co-registered with everything else on
the 30 m grid.

```bash
uv run python -m pipeline.raster_clip \
    --raster raw.landfire.evt_tif --region config/extent.geojson --name LF2022_EVT_FL
```

`--raster` takes a filesystem path or a dotted `config/data_paths.yaml` key. `--region`
defaults to `config/extent.geojson` (authoritative Florida, TIGER 2022 STATEFP 12).
`--dry-run` reports the clip size and writes nothing. Outputs land in
`data/interim/clips/` with a JSON manifest beside each tif.

## Produced so far

| Clip | Source | Size | Shape |
|---|---|--:|---|
| `LF2022_EVT_FL.tif` | LANDFIRE 2022 EVT CONUS (2.99 GB) | **75.3 MB** | 27,077 x 23,633 |
| `LF2022_VCC_FL.tif` | LANDFIRE 2022 VCC CONUS (1.57 GB) | **36.5 MB** | 27,077 x 23,633 |

Both verified pixel-identical to their source over sampled interior windows, and both land
exactly on the project's 30 m snap grid (offset from the TreeMap 2022 affine origin is 0 in
both axes). `data/interim/clips/` is gitignored; regenerate rather than commit.

## Two decisions

**Stage the source, then clip locally — do not stream it.** The elegant alternative is to
leave the file in R2 and let GDAL range-request only the tiles the region covers; the EVT is
internally tiled (128 x 128, LZW) so it works. **Measured, it is much the worse trade.** Bulk
transfer off this bucket runs at ~100 MB/s, so the whole 2.99 GB arrives in about 30 seconds,
while a windowed read over `/vsicurl/` issues roughly 39,000 range requests at GDAL's default
16 KB chunk size — the first 4,096-row strip had not finished after four minutes. The
streaming implementation (an ephemeral `rclone serve http` fronting the bucket) was written,
measured, and deleted. The staged copy lands in the gitignored `data/r2_cache/` and is
disposable once the clip exists.

**Clip to the bounding box, not the outline.** Masking to the Florida polygon would set every
Georgia pixel to nodata, and the workflows consuming these clips need that ground:
`raster_correction` trains on a padding ring around an AOI, and an AOI near the state line
would lose half its ring to a political boundary that means nothing to a classifier. `--mask`
opts into outline masking when the outline really is the point.

## Two data findings from doing this

**1. The LANDFIRE rasters had moved in the bucket.** `config/data_paths.yaml` mapped
`/mnt/d/LF2022_EVT_CONUS/...` to `r2:artemis-r2/data/LF2022_EVT_CONUS/`, which does not
exist — the bucket now files them under `landfire/`. `data_access` reported them as simply
absent, which is why the notebooks recorded "the EVT tif needs staging" as an environment
problem. Fixed by two `r2.renames` entries; rename values may contain slashes, so a directory
grouped under a bucket-side parent with no drive counterpart is expressible.

Note that [`data/index.md`](../data/index.md) (generated 2026-08-07) still lists
`LF2022_EVT_CONUS/` at the bucket root. **It is stale on this point**; regenerate with
`scripts/r2_index.py` rather than trusting it for LANDFIRE paths.

**2. Eight of thirty declared `raw.*` paths are unreachable** from either source, all
pre-existing and unrelated to this work:

- `raw.leto_ownership_run.*` (5 paths) — declared under
  `/mnt/d/forest_condition_2026/FVS/FVS_Database_Runs/...`, present in the bucket as
  top-level `20260804_095846_Hard_Ownership_Boundaries/`. The key's own `r2_prefix` comment
  already records this. **A `renames` entry cannot fix it**: the map rewrites only the first
  path component, so the intervening `FVS/FVS_Database_Runs/` would still be appended. Needs
  a full-prefix rewrite mechanism in `data_access.remote_url`.
- `raw.parcel_owner_summary.txt` — declared at the drive root, not in the bucket at that name.
- `raw.ecoregions.us_eco_l4_shp` and `..._state_boundaries_shp` — Level IV ecoregions appear
  not to have been uploaded (Level III resolves fine).

## Verified

- 18 offline tests (`tests/test_raster_clip.py`): co-registration, outward pixel rounding,
  clipping to the raster edge, disjoint-region rejection, chunk-size invariance, bbox vs
  mask behaviour, the dotted-key resolver, and the CLI manifest.
- Both Florida clips produced and checked against their sources as described above.
