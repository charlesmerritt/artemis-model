"""Regression tests for report figures built from exported score rasters."""

import json

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import pytest
import rasterio
from rasterio.transform import from_origin

from pipeline.s1_initial_state import make_report_figures as report


def _write_raster(path, data):
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=data.shape[-2],
        width=data.shape[-1],
        count=1 if data.ndim == 2 else data.shape[0],
        dtype=data.dtype,
        transform=from_origin(0, data.shape[-2], 1, 1),
    ) as dst:
        dst.write(data, 1) if data.ndim == 2 else dst.write(data)


def test_fig10_decodes_exported_uint16_scores_before_plotting(tmp_path, monkeypatch):
    """Catch a stale score divisor making Figure 10 surfaces 100x too large."""
    scored = np.array(
        [
            [[1_000, 5_000], [7_500, 10_000]],
            [[12_000, 15_000], [19_000, 20_000]],
        ],
        dtype=np.uint16,
    )
    _write_raster(tmp_path / "hole_prob_similarity.tif", scored)
    _write_raster(tmp_path / "treemap_hole_strata.tif", np.ones((2, 2), dtype=np.uint8))
    (tmp_path / "hole_model.json").write_text(
        json.dumps({"similarity_threshold": 0.9, "decision_threshold": 0.5})
    )

    captured = {}
    monkeypatch.setattr(report, "DATA", tmp_path)
    monkeypatch.setattr(report, "save", lambda fig, _name: captured.setdefault("fig", fig))

    report.fig10_gee_surfaces()

    fig = captured["fig"]
    try:
        np.testing.assert_allclose(
            fig.axes[0].images[0].get_array(),
            [[0.2, 0.5], [0.9, 1.0]],
        )
        np.testing.assert_allclose(
            fig.axes[1].images[0].get_array(),
            [[0.1, 0.5], [0.75, 1.0]],
        )
    finally:
        plt.close(fig)


def test_analysis_domain_separates_outside_bbox_from_treemap_coverage():
    strata = np.array([[0, 3], [0, 0]], dtype=np.uint8)
    raw_holes = np.array([[255, 1], [1, 255]], dtype=np.uint8)

    domain = report.analysis_domain_mask(strata, raw_holes, nodata=255)

    np.testing.assert_array_equal(domain, [[True, True], [False, True]])


@pytest.mark.parametrize("nodata", [None, np.nan])
def test_analysis_domain_requires_finite_nodata(nodata):
    with pytest.raises(ValueError, match="finite NoData"):
        report.analysis_domain_mask(
            np.zeros((2, 2), dtype=np.uint8),
            np.ones((2, 2), dtype=np.uint8),
            nodata=nodata,
        )


def test_fig1_leaves_outside_domain_white(monkeypatch):
    strata = np.array([[0, 3], [0, 0]], dtype=np.uint8)
    domain = np.array([[True, True], [False, True]])
    names = np.full(strata.shape, "class", dtype=object)
    lifeforms = np.full(strata.shape, "Herb", dtype=object)
    captured = {}
    monkeypatch.setattr(report, "read_evt_window", lambda *_args: (names, lifeforms))
    monkeypatch.setattr(report, "save", lambda fig, _name: captured.setdefault("fig", fig))

    report.fig1_study_area(strata, domain, None, None)

    fig = captured["fig"]
    try:
        np.testing.assert_array_equal(fig.axes[0].images[0].get_array(), [[1, 2], [0, 1]])
    finally:
        plt.close(fig)


def test_fig9_extrapolates_only_within_eligible_patch_interiors(tmp_path, monkeypatch):
    summary = pd.DataFrame(
        {
            "n": [400, 400, 400, 400],
            "LU_forest_2022": [0.99, 0.80, 0.25, 0.08],
            "LC_trees_pre_cut": [0.9, 0.8, 0.2, 0.1],
            "LC_trees_2024": [0.9, 0.8, 0.2, 0.1],
            "tree_removal_2016_2022": [0.3, 0.3, 0.4, 0.0],
        },
        index=[
            "S1_reference_positive",
            "S3_accepted",
            "S3_rejected",
            "S5_reference_negative",
        ],
    )
    summary.to_csv(tmp_path / "s3_validation_summary.csv")
    strata = np.zeros((8, 14), dtype=np.uint8)
    strata[1:6, 1:6] = 3
    strata[1:6, 8:13] = 3
    add_back = np.zeros_like(strata, dtype=bool)
    add_back[1:6, 1:6] = True
    captured = {}
    monkeypatch.setattr(report, "DATA", tmp_path)
    monkeypatch.setattr(report, "save", lambda fig, _name: captured.setdefault("fig", fig))
    report.VALUES.clear()

    report.fig9_s3_validation(strata, add_back)

    try:
        values = report.VALUES["s3_validation"]
        frame_acres = 9 * report.ACRES_PER_PIXEL
        assert values["sampling_frame_accepted_acres"] == pytest.approx(frame_acres)
        assert values["sampling_frame_rejected_acres"] == pytest.approx(frame_acres)
        assert values["estimated_missed_acres_within_sampling_frame"] == pytest.approx(
            frame_acres * 0.25
        )
        assert values["recall_proxy_within_sampling_frame"] == pytest.approx(
            0.80 / (0.80 + 0.25)
        )
        assert "400 eligible-interior points per group" in captured["fig"]._suptitle.get_text()
        assert values["sampling_uncertainty"] == "not estimated for spatially correlated pixels"
    finally:
        plt.close(captured["fig"])
