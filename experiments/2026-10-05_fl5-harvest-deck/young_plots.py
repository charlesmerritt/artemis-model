"""Give each add-back pixel a real young FIA plot, so LETO does not read mature trees on cut ground.

A provisional cut of ADR 0003 (branch ``add-back-stand-age``). The improved TreeMap keeps the
neighbour plot's ``TM_ID`` on provenance-4 pixels for forest type and species only; LETO's CA
would read that mature plot's STDAGE/BALIVE/QMD/TPA and merge the patch into the stand around
it. Here each such pixel instead takes a young plot of the neighbour's forest type at the
pixel's stand age, widening +-1, +-2 years, then to the forest type group.

Two simplifications against the ADR, both to be replaced by the raster correction:

- **Age** is drawn uniformly from 1-10 per connected patch (seeded), not dated from the
  Obata/Hansen cut year.
- **Pool** is the FIA plots TreeMap references in the five-county pilot
  (``FIA_5county_consolidated.db``), not the AL/FL/GA TreeMap library, and ties break on the
  ``PLT_CN`` string only (no species-mix distance).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

import numpy as np
import pandas as pd
from scipy import ndimage

AGE_LOW, AGE_HIGH = 1, 10


class MatchRung(StrEnum):
    TYPE_EXACT_AGE = "type_exact_age"
    TYPE_AGE_PM1 = "type_age_pm1"
    TYPE_AGE_PM2 = "type_age_pm2"
    GROUP_EXACT_AGE = "group_exact_age"
    GROUP_AGE_PM1 = "group_age_pm1"
    GROUP_AGE_PM2 = "group_age_pm2"
    GROUP_NEAREST_AGE = "group_nearest_age"
    UNMATCHED = "unmatched"


@dataclass(frozen=True)
class YoungMatch:
    plt_cn: str | None
    rung: MatchRung
    age_gap: int | None


_LADDER = (
    ("FORTYPCD", 0, MatchRung.TYPE_EXACT_AGE),
    ("FORTYPCD", 1, MatchRung.TYPE_AGE_PM1),
    ("FORTYPCD", 2, MatchRung.TYPE_AGE_PM2),
    ("TYPGRPCD", 0, MatchRung.GROUP_EXACT_AGE),
    ("TYPGRPCD", 1, MatchRung.GROUP_AGE_PM1),
    ("TYPGRPCD", 2, MatchRung.GROUP_AGE_PM2),
)


def _closest(candidates: pd.DataFrame, age: int) -> tuple[str, int]:
    gap = (candidates["STDAGE"] - age).abs()
    best = candidates.assign(_gap=gap).sort_values(["_gap", "PLT_CN"]).iloc[0]
    return str(best["PLT_CN"]), int(best["_gap"])


def match_young_plot(fortypcd: int, age: int, pool: pd.DataFrame,
                     type_group: int | None = None) -> YoungMatch:
    """The young plot for one (neighbour forest type, stand age) pair.

    ``pool`` has PLT_CN (exact text), FORTYPCD, TYPGRPCD and STDAGE. ``type_group`` is the
    neighbour's forest type group; when omitted it is looked up from the pool.
    """
    if type_group is None:
        groups = pool.loc[pool["FORTYPCD"] == fortypcd, "TYPGRPCD"]
        type_group = int(groups.iloc[0]) if len(groups) else None
    keys = {"FORTYPCD": fortypcd, "TYPGRPCD": type_group}
    for column, window, rung in _LADDER:
        candidates = pool[(pool[column] == keys[column])
                          & ((pool["STDAGE"] - age).abs() <= window)]
        if len(candidates):
            plt_cn, gap = _closest(candidates, age)
            return YoungMatch(plt_cn, rung, gap)
    in_group = pool[pool["TYPGRPCD"] == type_group]
    if len(in_group):
        plt_cn, gap = _closest(in_group, age)
        return YoungMatch(plt_cn, MatchRung.GROUP_NEAREST_AGE, gap)
    return YoungMatch(None, MatchRung.UNMATCHED, None)


def patch_ages(mask: np.ndarray, seed: int) -> np.ndarray:
    """One stand age in [1, 10] per 8-connected patch of ``mask``; 0 elsewhere."""
    labels, count = ndimage.label(mask, structure=np.ones((3, 3), dtype=bool))
    draws = np.random.default_rng(seed).integers(AGE_LOW, AGE_HIGH + 1, size=count + 1)
    draws[0] = 0
    return draws[labels].astype(np.int16)
