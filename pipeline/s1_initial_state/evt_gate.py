"""The EVT 2022 add-back gate: which LANDFIRE 2022 classes may take an add-back proposal.

Every add-back method proposes hole pixels from disturbance or bookend evidence; none of
them looks at what the ground is in 2022. The gate does, from the LF 2022 EVT legend, and
:mod:`county_improvement` ANDs it into every method's proposals before the consensus rule
and the minimum patch area. Analysis and the v2 decision: ``docs/evt2022-add-back-gate/``.

- ``NONE``: no gate; every pixel is eligible.
- ``AGRICULTURE_V1``: crops, orchards and aquaculture blocked (``EVT_PHYS`` Agricultural
  or ``EVT_LF`` Agriculture) except pasture and hay; water, snow/ice and the fill class
  are never eligible. This reproduces the published run's ``evt2022_gate_legend.csv``.
- ``AGRICULTURE_DEVELOPED_V2``: v1, and developed low/medium/high intensity and roads
  blocked too. The adopted gate.

Codes the legend does not list, and nodata, fail closed under every gate but ``NONE``.
"""

from __future__ import annotations

from enum import StrEnum

import numpy as np
import pandas as pd

DEVELOPED_BLOCKED = (7296, 7297, 7298, 7299)   # Developed-Low/Medium/High Intensity, Roads
NEVER_ELIGIBLE_LIFEFORMS = ("Water", "Snow-Ice", "Fill-NoData")
PASTURE_AND_HAY = "Pasture and Hay"            # matches "Pasture and Hay" and "... Pasture and Hayland"


class EvtGatePolicy(StrEnum):
    NONE = "none"
    AGRICULTURE_V1 = "evt2022_agriculture_v1"
    AGRICULTURE_DEVELOPED_V2 = "evt2022_agriculture_developed_v2"

    def eligible_codes(self, legend: pd.DataFrame) -> np.ndarray:
        """The LF 2022 EVT ``VALUE`` codes this gate lets an add-back proposal through on."""
        values = legend["VALUE"].to_numpy(dtype=np.int64)
        if self is EvtGatePolicy.NONE:
            return values
        agricultural = ((legend["EVT_PHYS"] == "Agricultural") | (legend["EVT_LF"] == "Agriculture"))
        pasture = legend["EVT_NAME"].str.contains(PASTURE_AND_HAY, regex=False)
        blocked = (agricultural & ~pasture) | legend["EVT_LF"].isin(NEVER_ELIGIBLE_LIFEFORMS)
        if self is EvtGatePolicy.AGRICULTURE_DEVELOPED_V2:
            blocked |= legend["VALUE"].isin(DEVELOPED_BLOCKED)
        return values[~blocked.to_numpy()]


def eligibility(evt: np.ndarray, legend: pd.DataFrame, policy: EvtGatePolicy) -> np.ndarray:
    """Per pixel, whether ``policy`` lets an add-back proposal through on its EVT 2022 class."""
    if policy is EvtGatePolicy.NONE:
        return np.ones(evt.shape, dtype=bool)
    return np.isin(evt, policy.eligible_codes(legend))
