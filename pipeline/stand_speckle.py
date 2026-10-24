"""Post-LETO speckle cleanup: absorb small parent stands into a neighbour.

LETO's force_merge only merges a stand under the minimum area into a same-owner neighbour,
and keeps it when there is none (``retain_when_no_candidate``). Where the ownership layer is
speckled (Harris 10 m warped to 30 m), that leaves many tiny stands bounded only by other
owners. ARTEMIS keeps LETO's output as is (the ground truth) and cleans it here, after LETO.

Each stand under ``min_pixels`` with a 4-neighbour edge to another stand joins the
neighbour it shares the most edge pixels with. Ties go to the larger neighbour, then the
lower label. Passes repeat until no small stand has a neighbour, so a cluster of small stands
ends as one stand or inside a large one. A small stand with no neighbour (a forest island)
stays. Labels come back consecutive from 1; 0 stays background.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

MAX_PASSES = 50


@dataclass(frozen=True)
class SpeckleReport:
    absorbed_stands: int
    absorbed_pixels: int
    isolated_small_stands: int
    passes: int


def _edges(seg: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(stand, neighbour, shared edge pixels) for every 4-neighbour pair, both directions."""
    a = np.concatenate([seg[:, :-1].ravel(), seg[:-1, :].ravel()])
    b = np.concatenate([seg[:, 1:].ravel(), seg[1:, :].ravel()])
    keep = (a > 0) & (b > 0) & (a != b)
    a, b = a[keep], b[keep]
    pairs = np.unique(np.stack([np.concatenate([a, b]), np.concatenate([b, a])]), axis=1,
                      return_counts=True)
    (src, dst), counts = pairs
    return src, dst, counts


def _root(parent: np.ndarray, i: int) -> int:
    while parent[i] != i:
        parent[i] = parent[parent[i]]
        i = parent[i]
    return i


def absorb_small_stands(seg: np.ndarray, min_pixels: int) -> tuple[np.ndarray, SpeckleReport]:
    """``seg`` with every non-isolated stand under ``min_pixels`` absorbed into a neighbour."""
    seg = seg.astype(np.int64, copy=True)
    absorbed_pixels = absorbed_stands = 0
    passes = 0
    for passes in range(1, MAX_PASSES + 1):
        sizes = np.bincount(seg.ravel())
        sizes[0] = 0
        src, dst, counts = _edges(seg)
        small = (sizes[src] > 0) & (sizes[src] < min_pixels)
        src, dst, counts = src[small], dst[small], counts[small]
        if not len(src):
            break
        # Best neighbour per small stand: most shared edge, then larger, then lower label.
        order = np.lexsort((dst, -sizes[dst], -counts, src))
        src, dst = src[order], dst[order]
        first = np.r_[True, src[1:] != src[:-1]]
        src, dst = src[first], dst[first]
        parent = np.arange(len(sizes))
        for s, d in zip(src, dst):
            rs, rd = _root(parent, s), _root(parent, d)
            if rs == rd:
                continue
            # The larger stand keeps its label, so a large neighbour is never relabelled.
            keep, drop = (rs, rd) if (sizes[rs], -rs) > (sizes[rd], -rd) else (rd, rs)
            parent[drop] = keep
            sizes[keep] += sizes[drop]
        roots = np.array([_root(parent, i) for i in range(len(parent))])
        moved = roots[seg] != seg
        absorbed_pixels += int((moved & (seg > 0)).sum())
        absorbed_stands += int((roots[1:] != np.arange(1, len(roots))).sum())
        seg = roots[seg]
    sizes = np.bincount(seg.ravel())
    sizes[0] = 0
    src, _, _ = _edges(seg)
    has_neighbour = np.zeros(len(sizes), dtype=bool)
    has_neighbour[src] = True
    isolated = int(((sizes > 0) & (sizes < min_pixels) & ~has_neighbour).sum())
    labels, inverse = np.unique(seg, return_inverse=True)
    if labels[0] != 0:
        inverse = inverse + 1
    out = inverse.reshape(seg.shape).astype(np.int32)
    return out, SpeckleReport(absorbed_stands, absorbed_pixels, isolated, passes)
