"""
Tests for pipeline/s5_imagery/raster_correction.py.

Everything here runs offline. The feature source is the seam that makes that
possible: :class:`TruthFeatureSource` stands in for AlphaEarth by reading a
"truth" raster the test wrote itself, so the whole correction — read, sample,
fit, spatially-blocked CV, predict, write — is exercised end to end against a
landscape whose right answer is known.

The scenario, in every test that needs one: 90 x 90 pixels of 30 m EPSG:5070,
class 100 (forest) with a class-300 (water) band down one side. A rectangle in
the middle is *observed* as class 200 (the eligible "grassland" misclassification)
while the truth underneath it is still forest. A correct run flips that rectangle
back to 100 and leaves everything else alone.
"""

import json
import sys
from pathlib import Path

import numpy as np
import pytest
import rasterio
from affine import Affine
from rasterio import windows as rio_windows
from rasterio.warp import transform as warp_transform
from shapely.geometry import box

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline.s5_imagery import raster_correction as rc
from pipeline.s5_imagery import vectors
from pipeline.s5_imagery.feature_sources import FeatureSource

CRS = "EPSG:5070"
PIXEL_M = 30.0
SIZE = 90

# Somewhere in north Florida, on the project's 30 m snap grid.
ORIGIN_X = 1_210_125.0
ORIGIN_Y = 937_605.0
TRANSFORM = Affine(PIXEL_M, 0.0, ORIGIN_X, 0.0, -PIXEL_M, ORIGIN_Y)

FOREST = 100
WATER = 300
ELIGIBLE = 200
NODATA = 0

# Rows/cols of the mislabeled rectangle, and of the AOI polygon that contains it.
PATCH = (slice(35, 55), slice(35, 55))
AOI_ROWS = (30, 60)
AOI_COLS = (30, 60)

# Per-class feature centroids, well separated so a linear model can learn them
# from a handful of samples; the noise is what stops it being trivial.
CLASS_CENTROIDS = {
    FOREST: np.array([1.0, 0.0, 0.0, 0.0]),
    WATER: np.array([0.0, 1.0, 0.0, 0.0]),
}
FEATURE_NOISE = 0.08


# ──────────────────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────────────────


def _truth_array() -> np.ndarray:
    values = np.full((SIZE, SIZE), FOREST, dtype=np.int16)
    values[:, :12] = WATER
    return values


def _write(path: Path, values: np.ndarray, nodata=NODATA) -> Path:
    profile = {
        "driver": "GTiff",
        "height": values.shape[0],
        "width": values.shape[1],
        "count": 1,
        "dtype": str(values.dtype),
        "crs": CRS,
        "transform": TRANSFORM,
        "nodata": nodata,
    }
    with rasterio.open(path, "w", **profile) as dataset:
        dataset.write(values, 1)
    return path


class TruthFeatureSource:
    """A FeatureSource that answers from a truth raster instead of Earth Engine.

    ``blind_fraction`` makes a deterministic share of points come back NaN, which
    is how AlphaEarth behaves at the edges of its coverage.
    """

    def __init__(self, truth_path: Path, seed: int = 0, blind_fraction: float = 0.0):
        self.truth_path = Path(truth_path)
        self.rng = np.random.default_rng(seed)
        self.blind_fraction = blind_fraction
        self.calls = 0

    @property
    def feature_names(self) -> list[str]:
        return [f"F{i}" for i in range(4)]

    @property
    def description(self) -> str:
        return f"truth raster {self.truth_path.name}"

    def features_at(self, lonlats):
        self.calls += 1
        points = list(lonlats)
        matrix = np.full((len(points), 4), np.nan)
        if not points:
            return matrix

        lons = [lon for lon, _ in points]
        lats = [lat for _, lat in points]
        xs, ys = warp_transform(vectors.WGS84, CRS, lons, lats)
        with rasterio.open(self.truth_path) as dataset:
            truth = [int(value[0]) for value in dataset.sample(zip(xs, ys, strict=True))]

        for index, class_value in enumerate(truth):
            if self.blind_fraction and (index % int(1 / self.blind_fraction) == 0):
                continue
            centroid = CLASS_CENTROIDS.get(class_value)
            if centroid is None:
                continue
            matrix[index] = centroid + self.rng.normal(0, FEATURE_NOISE, size=4)
        return matrix


@pytest.fixture
def aoi():
    """The AOI polygon, in WGS84, covering rows/cols 30–60 of the grid."""
    minx = ORIGIN_X + AOI_COLS[0] * PIXEL_M
    maxx = ORIGIN_X + AOI_COLS[1] * PIXEL_M
    maxy = ORIGIN_Y - AOI_ROWS[0] * PIXEL_M
    miny = ORIGIN_Y - AOI_ROWS[1] * PIXEL_M
    return vectors.to_wgs84(box(minx, miny, maxx, maxy))


@pytest.fixture
def rasters(tmp_path):
    truth = _truth_array()
    observed = truth.copy()
    observed[PATCH] = ELIGIBLE
    return {
        "truth": _write(tmp_path / "truth.tif", truth),
        "observed": _write(tmp_path / "observed.tif", observed),
    }


@pytest.fixture
def source(rasters):
    return TruthFeatureSource(rasters["truth"])


# ──────────────────────────────────────────────────────────────────────────────
# read_window
# ──────────────────────────────────────────────────────────────────────────────


def test_read_window_covers_the_aoi_plus_the_pad(rasters, aoi):
    pad = 300.0
    window = rc.read_window(rasters["observed"], aoi, pad_m=pad)

    # The window must contain the AOI plus the whole pad ring. It is a little
    # larger than 30 AOI pixels + 10 pad pixels each side, because a 5070 box
    # round-tripped through WGS84 comes back rotated ~7° off the grid (5070 north
    # is not true north at this longitude) and its envelope grows accordingly.
    left, bottom, right, top = rio_windows.bounds(window.window, TRANSFORM)
    aoi_minx, aoi_miny, aoi_maxx, aoi_maxy = vectors.to_equal_area(aoi).bounds
    assert left <= aoi_minx - pad
    assert bottom <= aoi_miny - pad
    assert right >= aoi_maxx + pad
    assert top >= aoi_maxy + pad

    assert window.pixel_area_m2 == pytest.approx(900.0)
    assert window.nodata == NODATA


def test_read_window_clips_to_the_raster_edge(rasters, aoi):
    window = rc.read_window(rasters["observed"], aoi, pad_m=100_000.0)
    assert window.shape == (SIZE, SIZE)


def test_read_window_rejects_a_zero_pad(rasters, aoi):
    with pytest.raises(ValueError, match="pad_m must be > 0"):
        rc.read_window(rasters["observed"], aoi, pad_m=0.0)


def test_read_window_rejects_an_aoi_that_misses_the_raster(rasters):
    elsewhere = vectors.to_wgs84(box(ORIGIN_X + 500_000, ORIGIN_Y, ORIGIN_X + 501_000, ORIGIN_Y + 1_000))
    with pytest.raises(ValueError, match="does not overlap"):
        rc.read_window(rasters["observed"], elsewhere, pad_m=300.0)


def test_read_window_guards_on_size(rasters, aoi):
    with pytest.raises(ValueError, match="over the .* guard"):
        rc.read_window(rasters["observed"], aoi, pad_m=300.0, max_pixels=100)


# ──────────────────────────────────────────────────────────────────────────────
# pixel_table
# ──────────────────────────────────────────────────────────────────────────────


def test_pixel_table_marks_the_aoi_pixels_and_only_those(rasters, aoi):
    window = rc.read_window(rasters["observed"], aoi, pad_m=300.0)
    table = rc.pixel_table(window, aoi)

    height, width = window.shape
    assert len(table) == height * width
    # A 900 m square is 30x30 pixels; the count moves by a pixel or two because
    # the reprojected AOI is very slightly rotated against the grid.
    assert int(table["inside"].sum()) == pytest.approx(900, abs=30)
    assert int((~table["inside"]).sum()) > 0


def test_pixel_table_drops_nodata(tmp_path, aoi):
    values = _truth_array()
    values[:, 80:] = NODATA
    path = _write(tmp_path / "holed.tif", values)
    window = rc.read_window(path, aoi, pad_m=100_000.0)
    table = rc.pixel_table(window, aoi)
    assert NODATA not in set(table["class_value"])
    assert len(table) == SIZE * (SIZE - 10)


def test_pixel_table_drops_in_band_fill_values(rasters, aoi):
    """LANDFIRE codes ocean as -9999 *inside* the valid range, below the 32767 nodata."""
    # 600 m of pad is what reaches the water band down the side of the grid.
    window = rc.read_window(rasters["observed"], aoi, pad_m=600.0)
    with_fill = rc.pixel_table(window, aoi)
    assert WATER in set(with_fill["class_value"])

    without = rc.pixel_table(window, aoi, invalid_values=[WATER])
    assert WATER not in set(without["class_value"])
    assert len(without) < len(with_fill)


def test_excluded_values_cannot_become_a_trusted_class(table, aoi):
    """The point of the exclusion: no training on what the ocean looks like."""
    window, _ = table
    pixels = rc.pixel_table(window, aoi, invalid_values=[WATER])
    with pytest.raises(ValueError, match="Fewer than two trusted classes"):
        rc.split_samples(pixels, [ELIGIBLE])


def test_attach_lonlat_lands_in_florida(rasters, aoi):
    window = rc.read_window(rasters["observed"], aoi, pad_m=300.0)
    table = rc.attach_lonlat(rc.pixel_table(window, aoi).head(20), window.crs)
    assert table["lon"].between(-88, -79).all()
    assert table["lat"].between(24, 32).all()


# ──────────────────────────────────────────────────────────────────────────────
# split_samples
# ──────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def table(rasters, aoi):
    window = rc.read_window(rasters["observed"], aoi, pad_m=600.0)
    return window, rc.pixel_table(window, aoi)


def test_split_puts_eligible_inside_pixels_in_the_apply_set(table):
    _, pixels = table
    split = rc.split_samples(pixels, [ELIGIBLE])
    assert len(split.apply) == 20 * 20
    assert set(split.apply["class_value"]) == {ELIGIBLE}
    assert split.apply["inside"].all()


def test_split_trains_on_undisputed_pixels_from_both_sides(table):
    _, pixels = table
    split = rc.split_samples(pixels, [ELIGIBLE])
    assert ELIGIBLE not in set(split.training["class_value"])
    # The chosen AOI contains undisputed forest around the patch, and it must be
    # used: training only outside the polygon is a different method.
    assert bool(split.training["inside"].any())
    assert bool((~split.training["inside"]).any())


def test_split_caps_each_class(table):
    _, pixels = table
    split = rc.split_samples(pixels, [ELIGIBLE], max_per_class=50)
    assert split.training["class_value"].value_counts().max() == 50


def test_split_drops_classes_that_are_too_rare(table):
    _, pixels = table
    pixels = pixels.copy()
    pixels.loc[pixels.index[:5], "class_value"] = 999
    split = rc.split_samples(pixels, [ELIGIBLE], min_per_class=30)
    assert split.dropped_classes == {999: 5}
    assert 999 not in split.class_counts


def test_split_reports_eligible_classes_that_are_absent(table):
    _, pixels = table
    split = rc.split_samples(pixels, [ELIGIBLE, 12345])
    assert split.absent_eligible == [12345]


def test_split_rejects_an_empty_eligible_list(table):
    _, pixels = table
    with pytest.raises(ValueError, match="No eligible classes"):
        rc.split_samples(pixels, [])


def test_split_rejects_eligible_classes_absent_from_the_polygon(table):
    _, pixels = table
    with pytest.raises(ValueError, match="No pixels inside the polygon"):
        rc.split_samples(pixels, [4242])


def test_split_guards_the_apply_set_size(table):
    _, pixels = table
    with pytest.raises(ValueError, match="over the .* guard"):
        rc.split_samples(pixels, [ELIGIBLE], max_apply_pixels=10)


def test_split_needs_two_trusted_classes(tmp_path, aoi):
    uniform = np.full((SIZE, SIZE), FOREST, dtype=np.int16)
    uniform[PATCH] = ELIGIBLE
    path = _write(tmp_path / "uniform.tif", uniform)
    window = rc.read_window(path, aoi, pad_m=300.0)
    with pytest.raises(ValueError, match="Fewer than two trusted classes"):
        rc.split_samples(rc.pixel_table(window, aoi), [ELIGIBLE])


# ──────────────────────────────────────────────────────────────────────────────
# blocks, features, CV
# ──────────────────────────────────────────────────────────────────────────────


def test_block_groups_share_an_id_within_a_block():
    x = np.array([0.0, 100.0, 400.0])
    y = np.array([0.0, 200.0, 0.0])
    groups = rc.block_groups(x, y, block_m=300.0)
    assert groups[0] == groups[1]
    assert groups[0] != groups[2]


def test_block_groups_rejects_a_zero_edge():
    with pytest.raises(ValueError, match="block_m must be > 0"):
        rc.block_groups(np.zeros(2), np.zeros(2), block_m=0.0)


def test_attach_features_drops_rows_the_source_cannot_answer_for(table, rasters):
    window, pixels = table
    split = rc.split_samples(pixels, [ELIGIBLE], max_per_class=60)
    blind = TruthFeatureSource(rasters["truth"], blind_fraction=0.5)
    kept, matrix = rc.attach_features(rc.attach_lonlat(split.training, window.crs), blind)
    assert len(kept) == len(matrix)
    assert len(kept) < len(split.training)
    assert not np.isnan(matrix).any()


def test_attach_features_requires_lonlat(table, source):
    _, pixels = table
    with pytest.raises(ValueError, match="attach_lonlat"):
        rc.attach_features(pixels.head(5), source)


def test_attach_features_rejects_a_misaligned_source(table):
    window, pixels = table

    class Misaligned:
        feature_names = ["a"]
        description = "misaligned"

        def features_at(self, lonlats):
            return np.zeros((len(list(lonlats)) + 1, 1))

    with pytest.raises(ValueError, match="row-aligned"):
        rc.attach_features(rc.attach_lonlat(pixels.head(5), window.crs), Misaligned())


def test_spatial_cross_validate_separates_learnable_classes(table, source):
    window, pixels = table
    split = rc.split_samples(pixels, [ELIGIBLE], max_per_class=150)
    training, features = rc.attach_features(rc.attach_lonlat(split.training, window.crs), source)
    labels = training["class_value"].astype(int).to_numpy()
    groups = rc.block_groups(training["x"].to_numpy(), training["y"].to_numpy())

    report = rc.spatial_cross_validate(features, labels, groups)

    assert report.skipped_reason is None
    assert report.accuracy > 0.95
    # The shuffled baseline is the check that the score means something.
    assert report.accuracy > report.shuffled_accuracy + 0.2
    assert set(report.per_class) == {FOREST, WATER}


def test_spatial_cross_validate_skips_rather_than_raises_with_one_block():
    features = np.random.default_rng(0).normal(size=(20, 4))
    labels = np.array([FOREST] * 10 + [WATER] * 10)
    groups = np.zeros(20, dtype=int)
    report = rc.spatial_cross_validate(features, labels, groups)
    assert report.skipped_reason is not None
    assert report.accuracy is None
    assert report.n_folds == 0


# ──────────────────────────────────────────────────────────────────────────────
# End to end
# ──────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def request_for(rasters, aoi, tmp_path):
    def build(**overrides):
        defaults = dict(
            aoi=aoi,
            raster_path=rasters["observed"],
            eligible_classes=[ELIGIBLE],
            out_dir=tmp_path / "out",
            slug="patch",
            pad_m=600.0,
            max_per_class=150,
            class_names={FOREST: "forest", WATER: "water", ELIGIBLE: "grass"},
        )
        return rc.CorrectionRequest(**{**defaults, **overrides})

    return build


def test_correct_raster_restores_the_mislabeled_patch(request_for, source):
    run = rc.correct_raster(request_for(), source)

    with rasterio.open(run.corrected_path) as dataset:
        corrected = dataset.read(1)
        assert dataset.dtypes[0] == "int16"
        assert dataset.nodata == NODATA

    # Every eligible pixel was inside the patch, and the patch is truly forest.
    assert ELIGIBLE not in set(np.unique(corrected))
    assert run.outcome.n_changed == 20 * 20
    assert run.outcome.transitions == {f"{ELIGIBLE}->{FOREST}": 400}


def test_correct_raster_leaves_pixels_outside_the_polygon_alone(request_for, source, rasters):
    run = rc.correct_raster(request_for(), source)
    with rasterio.open(run.corrected_path) as dataset:
        corrected = dataset.read(1)

    original = run.window.values
    untouched = corrected != original
    assert untouched.sum() == run.outcome.n_changed
    # The water band is trusted evidence, never an apply target.
    assert (corrected[original == WATER] == WATER).all()


def test_correct_raster_writes_three_diagnostic_bands(request_for, source):
    run = rc.correct_raster(request_for(), source)
    with rasterio.open(run.diagnostics_path) as dataset:
        assert dataset.count == 3
        assert dataset.descriptions == rc.DIAGNOSTIC_BANDS
        original, confidence, changed = dataset.read()

    np.testing.assert_array_equal(original, run.window.values.astype("float32"))
    # Confidence exists only where a prediction was made.
    scored = ~np.isnan(confidence)
    assert scored.sum() == len(run.outcome.applied)
    assert changed.sum() == run.outcome.n_changed
    assert confidence[scored].min() >= 0.0


def test_correct_raster_manifest_records_the_whole_run(request_for, source):
    run = rc.correct_raster(request_for(), source)
    manifest = json.loads(run.manifest_path.read_text())

    assert manifest["schema"] == rc.MANIFEST_SCHEMA
    assert manifest["parameters"]["eligible_classes"] == [ELIGIBLE]
    assert manifest["samples"]["eligible_pixels_inside"] == 400
    assert manifest["corrections"]["changed_pixels"] == 400
    assert manifest["corrections"]["changed_hectares"] == pytest.approx(36.0)
    assert manifest["cross_validation"]["accuracy"] > 0.95
    assert manifest["inputs"]["features"].startswith("truth raster")
    assert manifest["outputs"] == {
        "corrected": "patch_corrected.tif",
        "diagnostics": "patch_diagnostics.tif",
    }


def test_high_confidence_threshold_leaves_pixels_uncorrected(request_for, source):
    run = rc.correct_raster(request_for(min_confidence=1.0), source)
    assert run.outcome.n_changed == 0
    assert run.outcome.n_low_confidence == len(run.outcome.applied)
    with rasterio.open(run.corrected_path) as dataset:
        np.testing.assert_array_equal(dataset.read(1), run.window.values)


def test_pixels_without_features_are_counted_and_left_alone(request_for, rasters):
    blind = TruthFeatureSource(rasters["truth"], blind_fraction=0.25)
    run = rc.correct_raster(request_for(), blind)
    assert run.outcome.n_featureless > 0
    assert run.outcome.n_eligible == 400
    assert len(run.outcome.applied) == 400 - run.outcome.n_featureless
    assert run.manifest["samples"]["eligible_pixels_without_features"] == run.outcome.n_featureless


def test_transition_table_names_the_classes(request_for, source):
    run = rc.correct_raster(request_for(), source)
    frame = rc.transition_table(run.outcome, run.window.pixel_area_m2, request_for().class_names)
    assert list(frame.columns) == [
        "from_value",
        "from_name",
        "to_value",
        "to_name",
        "pixels",
        "hectares",
    ]
    assert frame.iloc[0]["from_name"] == "grass"
    assert frame.iloc[0]["to_name"] == "forest"
    assert frame.iloc[0]["hectares"] == pytest.approx(36.0)


def test_truth_source_satisfies_the_feature_source_protocol(source):
    assert isinstance(source, FeatureSource)
