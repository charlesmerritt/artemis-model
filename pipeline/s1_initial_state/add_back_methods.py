"""The add-back methods, and the rule that combines them into one decision.

Each method proposes TreeMap hole pixels to add back as forest, from evidence the
others do not share (``docs/improved-rasters-fl/presentation.html#methods``):

- ``BOOKENDS``: LANDFIRE EVT 2016 and 2024 bracket the hole with forest evidence
  (S1/S2 outright, S3/S4 through the AlphaEarth gate). This is
  :mod:`pipeline.s1_initial_state.statewide_repair`'s accepted add-back.
- ``OBATA_DISTURBANCE``: the Landsat IFZ disturbance detector (Obata et al., ForestSAT
  2018) dates a stand-replacing cut 2010-2022.
- ``HANSEN_LOSS``: UMD Global Forest Change records canopy loss 2001-2022 on ground that
  carried at least 30% canopy in 2000.

A method that has not run is *pending* (``None``), which is not the same as a method
that ran and found nothing: the "at least two agree" rule is meaningless until two
methods have run, so it refuses rather than silently adding nothing back.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum

import numpy as np


class AddBackMethod(StrEnum):
    BOOKENDS = "bookends"
    OBATA_DISTURBANCE = "obata_disturbance"
    HANSEN_LOSS = "hansen_loss"


# Least to most permissive. When one pixel is found by several methods, the area
# accounting credits it to the first method here, so no pixel is counted twice.
METHOD_PRIORITY = (AddBackMethod.BOOKENDS, AddBackMethod.OBATA_DISTURBANCE,
                   AddBackMethod.HANSEN_LOSS)

# One bit per method in the provenance raster, in priority order: 1, 2, 4.
METHOD_BIT = {method: 1 << i for i, method in enumerate(METHOD_PRIORITY)}


class ConsensusRule(StrEnum):
    UNION = "union"                # any run method is enough (the rule while only bookends has run)
    AT_LEAST_TWO = "at_least_two"  # the proposed synthesis: two independent methods must agree


class MethodMasks:
    """Per-method boolean masks on one grid; ``None`` marks a method that has not run."""

    def __init__(self, masks: Mapping[AddBackMethod, np.ndarray | None]):
        self.masks = {method: masks.get(method) for method in METHOD_PRIORITY}
        shapes = {m.shape for m in self.masks.values() if m is not None}
        if len(shapes) > 1:
            raise ValueError(f"method masks disagree on shape: {sorted(shapes)}")

    @property
    def run(self) -> tuple[AddBackMethod, ...]:
        return tuple(m for m in METHOD_PRIORITY if self.masks[m] is not None)

    @property
    def pending(self) -> tuple[AddBackMethod, ...]:
        return tuple(m for m in METHOD_PRIORITY if self.masks[m] is None)

    def restricted_to(self, allowed: np.ndarray) -> MethodMasks:
        """Every run method's proposals limited to ``allowed``; a pending method stays pending."""
        return MethodMasks({m: None if mk is None else mk & allowed for m, mk in self.masks.items()})

    def votes(self) -> np.ndarray:
        """How many run methods propose each pixel."""
        if not self.run:
            raise ValueError("no add-back method has run")
        return sum(self.masks[m].astype(np.uint8) for m in self.run)

    def combine(self, rule: ConsensusRule) -> np.ndarray:
        votes = self.votes()
        if rule is ConsensusRule.UNION:
            return votes >= 1
        if len(self.run) < 2:
            raise ValueError(
                f"{rule} needs at least two methods to have run; only {list(self.run)} have")
        return votes >= 2


def method_bits(masks: MethodMasks) -> np.ndarray:
    """uint8 bitmask of every run method that proposed each pixel (``METHOD_BIT``)."""
    shape = masks.masks[masks.run[0]].shape if masks.run else ()
    bits = np.zeros(shape, dtype=np.uint8)
    for method in masks.run:
        bits[masks.masks[method]] |= METHOD_BIT[method]
    return bits


def priority_attribution(masks: MethodMasks,
                         rule: ConsensusRule) -> dict[AddBackMethod, int | None]:
    """Accepted pixels credited to the first method in ``METHOD_PRIORITY`` that found them.

    The credits sum to the accepted total exactly. A pending method is ``None``.
    """
    unclaimed = masks.combine(rule).copy()
    credited: dict[AddBackMethod, int | None] = {}
    for method in METHOD_PRIORITY:
        mask = masks.masks[method]
        if mask is None:
            credited[method] = None
            continue
        claim = unclaimed & mask
        credited[method] = int(claim.sum())
        unclaimed &= ~claim
    return credited
