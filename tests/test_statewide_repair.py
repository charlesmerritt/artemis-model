"""Statewide (Florida) TreeMap repair: window, stratification, add-back, imputation."""

import numpy as np
import pandas as pd

from pipeline.s1_initial_state.statewide_repair import (
    FLGrid,
    legend_code_sets,
    stratify_block,
    unconditional_add_back,
)

EXCLUDED_PREFIXES = (
    "Eastern Warm Temperate Urban",
    "Eastern Warm Temperate Developed",
    "Developed",
    "Eastern Warm Temperate Orchard",
)


def _reference_stratify(evt16, evt24, legend):
    """The validated string-based semantics of stratify_treemap_holes, verbatim."""
    name_of = {int(v): n for v, n in zip(legend.VALUE, legend.EVT_NAME)}
    life_of = {int(v): lf for v, lf in zip(legend.VALUE, legend.EVT_LF)}

    def fields(v):
        name = np.array(
            [name_of.get(int(x), "NA") if x >= 0 else "NA" for x in v.ravel()],
            dtype=object,
        ).reshape(v.shape)
        life = np.array(
            [life_of.get(int(x), "NA") if x >= 0 else "NA" for x in v.ravel()],
            dtype=object,
        ).reshape(v.shape)
        return name, life

    def tree(name, life):
        text = name.astype(str)
        excluded = np.zeros(text.shape, dtype=bool)
        for prefix in EXCLUDED_PREFIXES:
            excluded |= np.char.startswith(text, prefix)
        return (life == "Tree") & ~excluded

    name16, life16 = fields(evt16)
    name24, life24 = fields(evt24)
    tree16, tree24 = tree(name16, life16), tree(name24, life24)
    logged16 = np.char.startswith(name16.astype(str), "Recently Logged")
    evidence16 = tree16 | logged16

    out = np.zeros(evt16.shape, dtype=np.uint8)
    out[logged16 & tree24] = 1
    out[tree16 & ~logged16 & tree24] = 2
    out[evidence16 & ~tree24] = 3
    out[~evidence16 & tree24] = 4
    out[~evidence16 & ~tree24] = 5
    return out


def test_florida_window_snaps_to_whole_pixels_and_covers_the_bounds():
    grid = FLGrid(left=-2361585.0, top=3177435.0, pixel=30.0)
    bounds = (796752.36627286, 252185.03139664, 1609016.51443515, 961154.43793337)
    window, transform = grid.window(bounds)
    west, north = transform * (0, 0)
    east, south = transform * (window.width, window.height)
    assert west <= bounds[0] and south <= bounds[1]
    assert east >= bounds[2] and north >= bounds[3]
    # Snapped outward to whole pixels of the grid, never inward.
    assert (west - grid.left) % grid.pixel == 0 and (grid.top - north) % grid.pixel == 0


def test_legend_code_sets_split_tree_logged_and_water():
    legend = pd.DataFrame({
        "VALUE": [100, 200, 300, 400, 7193, 7292, 9823, 7191],
        "EVT_NAME": [
            "Rocky Mountain Montane Dry-Mesic Mixed Conifer Forest",
            "Eastern Warm Temperate Urban Evergreen Forest",
            "Eastern Warm Temperate Orchard",
            "Introduced Upland Vegetation - Tree",  # Tree lifeform, no excluded prefix
            "Recently Logged Tree",
            "Open Water",
            "Southeastern Ruderal Grassland",
            "Recently Logged Herb",
        ],
        "EVT_LF": ["Tree", "Tree", "Tree", "Tree", "Tree", "NA", "Herbaceous", "Herbaceous"],
    })
    codes = legend_code_sets(legend)
    assert 100 in codes.tree
    assert 400 in codes.tree
    assert 200 not in codes.tree and 300 not in codes.tree  # urban/orchard excluded by design
    assert 7193 in codes.tree and 7193 in codes.logged
    assert 7191 in codes.logged
    assert 7292 == codes.water
    assert 9823 not in codes.tree


def test_stratify_block_matches_the_string_based_reference():
    rng = np.random.default_rng(7)
    evt16 = rng.choice([100, 200, 300, 7191, 7193, 9823, -9999], size=(40, 40))
    evt24 = rng.choice([100, 200, 9823, 7292, -9999], size=(40, 40))
    legend = pd.DataFrame({
        "VALUE": [100, 200, 300, 7191, 7193, 9823, 7292],
        "EVT_NAME": [
            "Montane Mixed Conifer Forest", "Eastern Warm Temperate Urban Evergreen Forest",
            "Eastern Warm Temperate Orchard", "Recently Logged Herb", "Recently Logged Tree",
            "Southeastern Ruderal Grassland", "Open Water",
        ],
        "EVT_LF": ["Tree", "Tree", "Tree", "Herbaceous", "Tree", "Herbaceous", "NA"],
    })
    codes = legend_code_sets(legend)
    assert np.array_equal(
        stratify_block(evt16, evt24, codes, codes), _reference_stratify(evt16, evt24, legend)
    )


def test_unconditional_add_back_takes_only_s1_and_s2():
    strata = np.array([[1, 2, 3], [4, 5, 0]], dtype=np.uint8)
    assert np.array_equal(
        unconditional_add_back(strata),
        np.array([[True, True, False], [False, False, False]]),
    )


def test_read_aligned_pads_a_short_footprint_with_nodata(tmp_path):
    import rasterio as rio
    from rasterio.transform import Affine

    from pipeline.s1_initial_state.statewide_repair import _read_aligned

    path = tmp_path / "tiny.tif"
    transform = Affine(30, 0, -2362425, 0, -30, 3177435)  # the LF grid phase
    with rio.open(
        path, "w", driver="GTiff", height=10, width=10, count=1, dtype="int16",
        crs="EPSG:5070", transform=transform, nodata=32767,
    ) as dst:
        dst.write(np.arange(100, dtype="int16").reshape(10, 10), 1)

    # Request 20x20 whose top-left corner is the raster's top-left: the lower
    # and right edges fall outside the footprint and must come back as nodata.
    out = _read_aligned(path, (-2362425, 3177135, -2361825, 3177435), (20, 20), transform)
    assert out.shape == (20, 20)
    assert out[0, 0] == 0 and out[9, 9] == 99
    assert (out[10:, :] == 32767).all() and (out[:, 10:] == 32767).all()

    # Entirely outside: all nodata, never an origin error.
    out = _read_aligned(path, (-1000000, 400000, -994000, 406000), (200, 200), transform)
    assert (out == 32767).all()
