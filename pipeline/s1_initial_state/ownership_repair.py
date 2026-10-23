"""Repair the Harris/NWOS ownership raster so every improved-TreeMap forest pixel has an owner.

NWOS (Harris, Caputo & Butler 2025, ``RDS-2025-0045``) takes its land cover from NLCD, and
TreeMap its forest from LANDFIRE EVT, which is in the NLCD lineage. They miss the same land:
in the five-county pilot, 66% of the forest the TreeMap add-back recovers is NWOS Non-Forest.
After the add-back those pixels are forest with no owner, and no management regime can be
assigned to them.

The repair is spatial nearest-neighbour imputation: each forest pixel NWOS calls
Non-Forest or Water (or leaves nodata) takes the class of the nearest pixel NWOS
assigns a known owner (family, corporate, tribal, federal, state, local). Past
``max_distance_px`` it becomes Unknown Forest. It does not guess an owner from far away.
Known owners and NWOS's own Unknown Forest are never changed.
"""

from __future__ import annotations

from enum import IntEnum, StrEnum

import numpy as np
from scipy import ndimage

UNKNOWN_FOREST, NON_FOREST, WATER = 0, 1, 2
KNOWN_OWNERS = (3, 4, 5, 6, 7, 8)          # family, corporate, tribal, federal, state, local
NWOS_FOREST = (UNKNOWN_FOREST, *KNOWN_OWNERS)
NODATA = 15
MAX_DISTANCE_PX = 63                       # ~1.9 km: the widest donor ring the TreeMap fill uses


class OwnershipRepairScope(StrEnum):
    ADDED_BACK = "added_back"  # only pixels the TreeMap add-back recovered
    ALL_FOREST = "all_forest"  # every improved-TreeMap forest pixel NWOS does not carry as forest


class OwnershipProvenance(IntEnum):
    NOT_FOREST = 0   # not forest in the improved TreeMap; NWOS value kept
    PUBLISHED = 1    # forest, NWOS value kept
    IMPUTED = 2      # forest, owner imputed from the nearest known owner
    UNRESOLVED = 3   # forest, no known owner within reach; set to Unknown Forest


def repair_ownership(
    nwos: np.ndarray,
    forest: np.ndarray,
    added_back: np.ndarray,
    *,
    scope: OwnershipRepairScope = OwnershipRepairScope.ALL_FOREST,
    max_distance_px: float = MAX_DISTANCE_PX,
) -> tuple[np.ndarray, np.ndarray]:
    """Return ``(repaired uint8 NWOS classes, uint8 OwnershipProvenance)``.

    ``forest`` is the improved TreeMap's forest (published plus added back);
    ``added_back`` the recovered part of it, used by ``OwnershipRepairScope.ADDED_BACK``.
    """
    needs_owner = forest & ~np.isin(nwos, NWOS_FOREST)
    if scope is OwnershipRepairScope.ADDED_BACK:
        needs_owner &= added_back

    repaired = nwos.copy()
    provenance = np.where(forest, OwnershipProvenance.PUBLISHED,
                          OwnershipProvenance.NOT_FOREST).astype(np.uint8)
    known = np.isin(nwos, KNOWN_OWNERS)
    if known.any():
        distance, (rows, cols) = ndimage.distance_transform_edt(~known, return_indices=True)
        imputed = needs_owner & (distance <= max_distance_px)
        repaired[imputed] = nwos[rows[imputed], cols[imputed]]
        provenance[imputed] = OwnershipProvenance.IMPUTED
    else:
        imputed = np.zeros_like(needs_owner)
    unresolved = needs_owner & ~imputed
    repaired[unresolved] = UNKNOWN_FOREST
    provenance[unresolved] = OwnershipProvenance.UNRESOLVED
    return repaired, provenance
