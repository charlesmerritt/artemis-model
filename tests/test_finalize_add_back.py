"""Tests for the add-back decision rule.

The risk this guards: a stratum silently changing sides. S1/S2 must be accepted
regardless of what the model says, S5 must never be accepted, and S3/S4 must
require *both* stages — an OR where an AND belongs would quietly add tens of
thousands of acres of pasture back into TreeMap.
"""

import importlib.util
from pathlib import Path

import numpy as np
import pytest

_ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "finalize_add_back", _ROOT / "pipeline/s1_initial_state/finalize_add_back.py")
finalize = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(finalize)

SIM_T, DEC_T = 0.5, 0.5


def _decide(strata, prob, sim):
    return finalize.decide(np.array([strata]), np.array([prob]), np.array([sim]), SIM_T, DEC_T)[0]


@pytest.mark.parametrize("stratum", [1, 2])
def test_proven_strata_are_accepted_even_when_the_model_rejects_them(stratum):
    assert _decide(stratum, prob=0.0, sim=-1.0)


def test_stable_nonforest_is_never_accepted():
    assert not _decide(5, prob=1.0, sim=1.0)


@pytest.mark.parametrize("stratum", [3, 4])
@pytest.mark.parametrize("prob,sim,expected", [
    (0.9, 0.9, True),    # clears both stages
    (0.9, 0.1, False),   # fails the similarity mask
    (0.1, 0.9, False),   # fails the classifier
    (0.1, 0.1, False),
])
def test_ambiguous_strata_require_both_stages(stratum, prob, sim, expected):
    assert bool(_decide(stratum, prob, sim)) is expected


def test_thresholds_are_inclusive_at_the_boundary():
    assert _decide(3, prob=DEC_T, sim=SIM_T)


def test_mmu_drops_speckle_and_keeps_blocks():
    mask = np.zeros((20, 20), dtype=bool)
    mask[2, 2] = True           # 1 px  = 0.22 ac -> dropped
    mask[10:16, 10:16] = True   # 36 px = 8.01 ac -> kept
    out = finalize.apply_mmu(mask, min_acres=5.0)
    assert not out[2, 2]
    assert out[10:16, 10:16].all()
    assert out.sum() == 36


def test_export_precision_bounds_decision_flips_to_half_a_step():
    """Regression: at 1/100 the exported scores disagreed with the local model.

    ``finalize_add_back`` compares decoded raster values against the model's
    full-precision thresholds. Encoding at hundredths pushed a true similarity of
    0.9046 down to 0.90 (wrongly rejected at a 0.90457 threshold) and a true
    probability of 0.499 up to 0.50 (wrongly accepted at 0.5).

    No fixed-point scale can preserve a comparison against an arbitrary real
    threshold exactly — a value within half a step of the boundary will always be
    able to cross it. What the scale controls is *how wide* that band is. This
    asserts the bound rather than exactness, and pins the specific values that
    used to flip at 1/100 but no longer do.
    """
    scale = finalize_scale()
    half_step = 0.5 / scale
    sim_threshold, dec_threshold = 0.9045695612737514, 0.5

    def round_trip(value, offset=0.0):
        return round((value + offset) * scale) / scale - offset

    for value, threshold, offset in (
        [(v, sim_threshold, 1.0) for v in np.linspace(0.895, 0.915, 2001)]
        + [(v, dec_threshold, 0.0) for v in np.linspace(0.490, 0.510, 2001)]
    ):
        if (round_trip(value, offset) >= threshold) != (value >= threshold):
            assert abs(value - threshold) <= half_step, (
                f"{value} flipped but is {abs(value - threshold):.2e} from the "
                f"threshold, beyond the {half_step:.2e} half-step bound"
            )

    # The concrete values that broke at 1/100 must now agree with the model.
    assert round_trip(0.9046, 1.0) >= sim_threshold
    assert round_trip(0.9049, 1.0) >= sim_threshold
    assert round_trip(0.499) < dec_threshold
    assert round_trip(0.495) < dec_threshold


def finalize_scale() -> int:
    """The fixed-point scale the export and the decoder must agree on."""
    spec = importlib.util.spec_from_file_location(
        "embed_holes", _ROOT / "pipeline/s1_initial_state/embed_holes.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.SCORE_SCALE


def test_summary_accounts_for_every_stratum_and_the_mmu_loss():
    strata = np.array([[1, 3, 5], [3, 4, 0]], dtype=np.uint8)
    raw = np.array([[True, True, False], [False, True, False]])
    final = np.array([[True, False, False], [False, True, False]])
    table = finalize.summarize(strata, raw, final)

    assert set(table.stratum) == set(finalize.STRATUM_NAMES.values())
    s3 = table[table.stratum == "S3_cut_2016_2022_open"].iloc[0]
    assert s3.accepted_acres > s3.after_mmu_acres  # one S3 pixel lost to the MMU
    assert s3.frac_added_back == 0.0
    assert table[table.stratum == "S5_no_evidence"].iloc[0].after_mmu_acres == 0.0
