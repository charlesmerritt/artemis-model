"""Regression tests for report figures built from exported score rasters."""

import json

import matplotlib.pyplot as plt
import numpy as np
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
