"""Invariant checks on the clearcut-vs-agriculture run outputs.

These skip when the notebooks have not been executed (the CSV is a gitignored artifact).
When present, they guard the properties the analysis relies on.
"""

import sys
from pathlib import Path

import numpy as np
import pytest

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT / "notebooks"))

import clearcut_ag_common as cac  # noqa: E402

CSV = REPO_ROOT / "data" / "interim" / "clearcut_ag" / "embeddings_samples.csv"


@pytest.fixture(scope="module")
def samples():
    if not CSV.exists():
        pytest.skip(f"{CSV} not present — run Clearcut-vs-Agriculture-Embeddings.ipynb first")
    import pandas as pd

    return pd.read_csv(CSV)


def test_embeddings_present_and_finite(samples):
    bands = list(cac.EMBEDDING_BANDS)
    assert all(b in samples.columns for b in bands)
    assert int(samples[bands].isna().sum().sum()) == 0


def test_embeddings_are_unit_vectors(samples):
    norms = np.linalg.norm(samples[list(cac.EMBEDDING_BANDS)].to_numpy(), axis=1)
    # AlphaEarth V1 embeddings are L2-normalized per pixel.
    assert np.allclose(norms, 1.0, atol=0.02)


def test_clearcut_probability_in_unit_interval(samples):
    if "clearcut_prob" not in samples.columns:
        pytest.skip("clearcut_prob only present when Method 1 wrote the table")
    assert samples["clearcut_prob"].between(0.0, 1.0).all()


def test_labels_are_internally_consistent(samples):
    assert set(samples["label"]) <= {"clearcut", "agriculture", "confused", "other"}
    clearcut = samples[samples.label == "clearcut"]
    assert (
        (clearcut.lc_pre == cac.LCMS_LAND_COVER_TREES)
        & (clearcut.change_event == cac.LCMS_CHANGE_TREE_REMOVAL)
    ).all()
    confused = samples[samples.label == "confused"]
    assert confused["evt2022"].isin(cac.CONFUSED_EVT_VALUES).all()


FEATURE_CSV = REPO_ROOT / "data" / "interim" / "clearcut_ag" / "feature_table.csv"


@pytest.fixture(scope="module")
def feature_table():
    if not FEATURE_CSV.exists():
        pytest.skip(f"{FEATURE_CSV} not present — run Clearcut-Grassland-Feature-Engineering.ipynb")
    import pandas as pd

    return pd.read_csv(FEATURE_CSV)


def test_feature_table_embeddings_finite(feature_table):
    cols = list(cac.EMBEDDING_BANDS) + [f"P{i:02d}" for i in range(64)]
    assert all(c in feature_table.columns for c in cols)
    assert int(feature_table[cols].isna().sum().sum()) == 0
    assert (feature_table["emb_delta_l2"] >= 0).all()


def test_feature_table_roles_and_labels(feature_table):
    assert set(feature_table["role"]) <= {
        "positive_forest", "negative_grassland", "apply_confused", "other"
    }
    pos = feature_table[feature_table.role == "positive_forest"]
    neg = feature_table[feature_table.role == "negative_grassland"]
    apply = feature_table[feature_table.role == "apply_confused"]
    assert (pos["y"] == 1.0).all()
    assert (neg["y"] == 0.0).all()
    assert feature_table[feature_table.role == "other"]["y"].isna().all()
    # positives are LCMS clearcuts; apply rows are confused EVT classes
    assert (
        (pos.lc_pre == cac.LCMS_LAND_COVER_TREES) & (pos.change_event == cac.LCMS_CHANGE_TREE_REMOVAL)
    ).all()
    assert apply["evt2022"].isin(cac.CONFUSED_EVT_VALUES).all()


def test_feature_table_clean_columns_present(feature_table):
    assert all(c in feature_table.columns for c in cac.clean_feature_columns())
