"""The EVT 2022 add-back gate: which LANDFIRE classes may take an add-back proposal."""

from pathlib import Path

import numpy as np
import pandas as pd

from pipeline.s1_initial_state.evt_gate import DEVELOPED_BLOCKED, EvtGatePolicy, eligibility

# The published v1 run's legend (improved-rasters-evt2022-gated/aoi_5county/), which
# carries the LF 2022 EVT columns the gate reads and the ``eligible`` column it chose.
PUBLISHED_V1 = Path(__file__).parent / "fixtures" / "evt2022_gate_legend_v1.csv"


def legend(*rows):
    return pd.DataFrame(rows, columns=["VALUE", "EVT_NAME", "EVT_LF", "EVT_PHYS"])


LEGEND = legend(
    (7292, "Open Water", "Water", "Open Water"),
    (7296, "Developed-Low Intensity", "Developed", "Developed-Low Intensity"),
    (7755, "Agriculture-Cultivated Crops and Irrigated Agriculture", "Agriculture", "Agricultural"),
    (7990, "Eastern Warm Temperate Orchard", "Tree", "Agricultural"),
    (7997, "Eastern Warm Temperate Pasture and Hayland", "Herb", "Agricultural"),
    (9001, "Some Pine Forest", "Tree", "Conifer"),
)


def test_v1_reproduces_the_published_legends_eligible_column():
    published = pd.read_csv(PUBLISHED_V1)
    codes = EvtGatePolicy.AGRICULTURE_V1.eligible_codes(published)
    assert sorted(codes) == sorted(published.loc[published["eligible"], "VALUE"])


def test_v2_is_v1_with_the_developed_classes_blocked():
    published = pd.read_csv(PUBLISHED_V1)
    v1 = set(EvtGatePolicy.AGRICULTURE_V1.eligible_codes(published))
    v2 = set(EvtGatePolicy.AGRICULTURE_DEVELOPED_V2.eligible_codes(published))
    assert v1 - v2 == set(DEVELOPED_BLOCKED)
    assert v2 < v1


def test_v1_blocks_crops_and_orchards_keeps_pasture_and_forest():
    codes = set(EvtGatePolicy.AGRICULTURE_V1.eligible_codes(LEGEND))
    assert codes == {7296, 7997, 9001}


def test_the_policy_values_are_the_names_recorded_in_run_summaries():
    assert EvtGatePolicy("evt2022_agriculture_v1") is EvtGatePolicy.AGRICULTURE_V1
    assert EvtGatePolicy("evt2022_agriculture_developed_v2") is EvtGatePolicy.AGRICULTURE_DEVELOPED_V2
    assert EvtGatePolicy("none") is EvtGatePolicy.NONE


def test_eligibility_fails_closed_on_codes_the_legend_does_not_list_and_on_nodata():
    evt = np.array([[9001, 7997, 7755], [1234, 32767, -9999]], dtype=np.int16)
    ok = eligibility(evt, LEGEND, EvtGatePolicy.AGRICULTURE_V1)
    assert ok.tolist() == [[True, True, False], [False, False, False]]


def test_v2_eligibility_rejects_developed_pixels_v1_accepts():
    evt = np.array([7296, 9001])
    assert eligibility(evt, LEGEND, EvtGatePolicy.AGRICULTURE_V1).tolist() == [True, True]
    assert eligibility(evt, LEGEND, EvtGatePolicy.AGRICULTURE_DEVELOPED_V2).tolist() == [False, True]


def test_no_gate_leaves_every_pixel_eligible():
    evt = np.array([7755, 1234, 32767])
    assert eligibility(evt, LEGEND, EvtGatePolicy.NONE).tolist() == [True, True, True]
