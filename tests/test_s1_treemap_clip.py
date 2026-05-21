"""
Tests for Step 1a — TreeMap clip to Florida.

Tests marked @pytest.mark.requires_data run the actual clip (slow; ~1 min).
Tests marked @clip_done verify the output file once it exists.
Run post_clip tests only after the clip has been executed:

    uv run pytest -m post_clip
"""

import pytest
from pathlib import Path
import numpy as np

PROJECT_ROOT = Path(__file__).parent.parent
INTERIM_DIR  = PROJECT_ROOT / "data" / "interim"
CLIPPED_TIF  = INTERIM_DIR / "treemap_2022_fl.tif"
BOUNDARY_GPK = INTERIM_DIR / "florida_boundary_5070.gpkg"

# Skip entire module if clip hasn't been run yet
clip_done = pytest.mark.skipif(
    not CLIPPED_TIF.exists(),
    reason="treemap_2022_fl.tif not found — run: uv run python -m pipeline.s1_initial_state.clip_treemap",
)


@clip_done
def test_clipped_tif_exists():
    assert CLIPPED_TIF.exists(), f"Run clip_treemap.py first: {CLIPPED_TIF}"


@clip_done
def test_clipped_tif_crs_is_5070():
    import rasterio
    with rasterio.open(CLIPPED_TIF) as src:
        assert src.crs.to_epsg() == 5070


@clip_done
def test_clipped_tif_snapped_to_treemap_grid():
    """Origin must align exactly to the CONUS TreeMap grid (30m pixel boundaries)."""
    import rasterio
    with rasterio.open(CLIPPED_TIF) as src:
        t = src.transform
        assert t.a == pytest.approx(30.0), "Pixel width must be 30m"
        assert t.e == pytest.approx(-30.0), "Pixel height must be -30m"
        # Origin must be a multiple of 30 offset from TreeMap origin (-2361585, 3177435)
        assert (t.c - (-2361585)) % 30 == pytest.approx(0, abs=1e-3), \
            "X origin not aligned to TreeMap grid"
        assert (t.f - 3177435) % 30 == pytest.approx(0, abs=1e-3), \
            "Y origin not aligned to TreeMap grid"


@clip_done
def test_clipped_tif_dtype_and_nodata():
    import rasterio
    with rasterio.open(CLIPPED_TIF) as src:
        assert src.dtypes[0] == "uint32"
        assert src.nodata == pytest.approx(4294967295)


@clip_done
def test_clipped_tif_has_valid_pixels():
    import rasterio
    with rasterio.open(CLIPPED_TIF) as src:
        data = src.read(1)
        valid = data[data != int(src.nodata)]
        # Florida should have millions of forested pixels
        assert len(valid) > 1_000_000, f"Only {len(valid):,} valid pixels — expected > 1M"


@clip_done
def test_clipped_tif_plot_ids_are_positive():
    """TM_ID values must be positive uint32; zero is not a valid plot ID."""
    import rasterio
    with rasterio.open(CLIPPED_TIF) as src:
        data = src.read(1)
        nodata = int(src.nodata)
        valid = data[(data != nodata)]
        assert np.all(valid > 0), "Found zero-value TM_IDs — check nodata handling"


@clip_done
def test_clipped_tif_bounds_within_florida():
    """Raster bounds (in EPSG:5070) must fall within Florida's known extent."""
    import rasterio
    with rasterio.open(CLIPPED_TIF) as src:
        bounds = src.bounds
    # Florida in EPSG:5070 roughly: x 900k–2000k, y 200k–1000k
    assert bounds.left   > 800_000,   f"Left bound {bounds.left:.0f} too far west"
    assert bounds.right  < 2_100_000, f"Right bound {bounds.right:.0f} too far east"
    assert bounds.bottom > 100_000,   f"Bottom {bounds.bottom:.0f} too far south"
    assert bounds.top    < 1_100_000, f"Top {bounds.top:.0f} too far north"


@clip_done
def test_florida_boundary_gpkg_exists():
    assert BOUNDARY_GPK.exists()


@clip_done
def test_florida_boundary_crs_is_5070():
    import geopandas as gpd
    gdf = gpd.read_file(BOUNDARY_GPK)
    assert gdf.crs.to_epsg() == 5070


@clip_done
def test_extent_geojson_updated_with_tiger_polygon():
    """After clip, extent.geojson must contain the TIGER polygon (not bounding box)."""
    import json
    with open(PROJECT_ROOT / "config" / "extent.geojson") as f:
        gj = json.load(f)
    feature = gj["features"][0]
    assert "TIGER" in feature["properties"].get("source", ""), \
        "extent.geojson source should reference Census TIGER"
    # Polygon should have far more than 5 coordinates (bounding box = 5)
    coords = feature["geometry"]["coordinates"]
    # May be Polygon or MultiPolygon
    if feature["geometry"]["type"] == "Polygon":
        n_coords = len(coords[0])
    else:  # MultiPolygon
        n_coords = sum(len(ring[0]) for ring in coords)
    assert n_coords > 10, "extent.geojson still looks like a bounding box"
