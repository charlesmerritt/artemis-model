"""LETO cellular-automata stand segmentation over the AOI (LETO stage 2).

A faithful port of the algorithm in LETO `scripts/Cellular_automata/
02_segment_treemap.py` (v3, boundary-vectorized) with rasterio/shapely
replacing the ArcPy I/O; the scientific behaviour — cost function, weights,
thresholds, synchronous boundary-cell reassignment, ownership hard
boundaries, small/oversized handling, similar-stand merging, and the
riparian split into management units — follows the LETO script and its
configuration constants.

The CA loop: every segment-boundary cell simultaneously evaluates joining
each rook-adjacent neighbouring segment, at cost

    sum_f w_f * (cell_f - segment_mean_f)^2   over STDAGE/BALIVE/QMD/TPA (standardized)
  + w_FORTYPCD * [segment dominant type != cell type]
  - SHARED_EDGE_BONUS * shared edges with that segment

with reassignment only within the same ownership class (hard boundaries),
iterated to convergence (<1% of cells changing).

Outputs (work/):
    segmentation.npz      mu_labels (management-unit raster), seg_labels
                          (parent stands), riparian mask
    mu_summary.csv        one row per management unit: acreage, owner class,
                          management class, attribute means, dominant type
    mu_donor_weights.csv  MU_ID x PLT_CN pixel-share donor weights (LETO
                          stage 3 crosswalk)
"""

from __future__ import annotations

import math
import sys
import time
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import ndimage

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (
    CELL_ACRES,
    FLOWLINE_BUFFER_RULES_FT,
    FT_TO_M,
    HARRIS_TO_OWNER_CLASS,
    STREAMS_SHP,
    AOI_OWNERSHIP_TIF,
    WORK,
)

# ============================================================
# SEGMENTATION SETTINGS — LETO 02_segment_treemap.py constants
# ============================================================
INITIAL_SEED_ACRES = 100.0
MIN_STAND_ACRES = 5.0
MAX_STAND_ACRES = 300.0
# LETO ships 40 iterations / 1% convergence; this run lets the CA settle an
# order of magnitude further so boundaries stop moving rather than merely
# slowing (visual QA on the first render showed ragged, still-moving edges).
MAX_ITERATIONS = 100
CONVERGENCE_THRESHOLD = 0.001
MINIMUM_SCORE_IMPROVEMENT = 0.01
WEIGHTS = {"FORTYPCD": 0.30, "STDAGE": 0.25, "BALIVE": 0.20, "QMD": 0.15, "TPA": 0.10}
SHARED_EDGE_BONUS = 0.1
Z_CLIP = 4.0
MERGE_SIMILAR_STANDS = True
SIMILAR_MERGE_MAX_AGE_DIFFERENCE = 10.0
SIMILAR_MERGE_MAX_PASSES = 50
UPLAND_MANAGEMENT_CODE = 0
RIPARIAN_MANAGEMENT_CODE = 1

CONTINUOUS = ["STDAGE", "BALIVE", "QMD", "TPA"]


def connectivity_structure():
    return np.array([[0, 1, 0], [1, 1, 1], [0, 1, 0]], dtype=np.int8)


def robust_standardize(array, valid_mask):
    values = array[valid_mask]
    median = float(np.nanmedian(values))
    q1, q3 = (float(np.nanpercentile(values, p)) for p in (25, 75))
    iqr = q3 - q1
    scale = float(np.nanstd(values)) if not np.isfinite(iqr) or iqr == 0 else iqr / 1.349
    if not np.isfinite(scale) or scale == 0:
        scale = 1.0
    standardized = ((array - median) / scale).astype(np.float32)
    np.clip(standardized, -Z_CLIP, Z_CLIP, out=standardized)
    return standardized, median, scale


def create_initial_segments(valid_mask, cells_per_seed, ownership):
    rows, cols = valid_mask.shape
    block_side = max(1, int(round(math.sqrt(cells_per_seed))))
    labels = np.zeros((rows, cols), dtype=np.int32)
    next_id = 1
    structure = connectivity_structure()
    for r0 in range(0, rows, block_side):
        for c0 in range(0, cols, block_side):
            sl = np.s_[r0:min(rows, r0 + block_side), c0:min(cols, c0 + block_side)]
            block_mask = valid_mask[sl]
            if not np.any(block_mask):
                continue
            block_owner = ownership[sl]
            target = labels[sl]
            for owner_code in np.unique(block_owner[block_mask]):
                owner_mask = block_mask & (block_owner == owner_code)
                local, n = ndimage.label(owner_mask, structure=structure)
                pos = local > 0
                target[pos] = local[pos] + next_id - 1
                next_id += n
    return labels


def segment_statistics_arrays(labels, feature_arrays, forest_type):
    valid = labels > 0
    ids = labels[valid].astype(np.int64, copy=False)
    max_id = int(ids.max()) if ids.size else 0
    counts = np.bincount(ids, minlength=max_id + 1).astype(np.int64)
    means = {}
    for name, array in feature_arrays.items():
        sums = np.bincount(ids, weights=array[valid].astype(np.float64), minlength=max_id + 1)
        means[name] = np.divide(sums, counts, out=np.zeros(max_id + 1), where=counts > 0)
    types = forest_type[valid].astype(np.int64, copy=False)
    unique_types, compact = np.unique(types, return_inverse=True)
    n_types = len(unique_types)
    if n_types:
        pair_counts = np.bincount(ids * n_types + compact,
                                  minlength=(max_id + 1) * n_types).reshape(max_id + 1, n_types)
        dom_idx = np.argmax(pair_counts, axis=1)
        dominant_type = unique_types[dom_idx].astype(np.int32)
        dominant_count = pair_counts[np.arange(max_id + 1), dom_idx].astype(np.int64)
    else:
        dominant_type = np.zeros(max_id + 1, dtype=np.int32)
        dominant_count = np.zeros(max_id + 1, dtype=np.int64)
    return {"counts": counts, "means": means,
            "dominant_type": dominant_type, "dominant_count": dominant_count}


def segment_categorical_mode(labels, values):
    valid = labels > 0
    ids = labels[valid].astype(np.int64, copy=False)
    max_id = int(ids.max()) if ids.size else 0
    categories = values[valid].astype(np.int64, copy=False)
    unique_values, compact = np.unique(categories, return_inverse=True)
    n_values = len(unique_values)
    if n_values == 0:
        return np.zeros(max_id + 1, dtype=np.int32)
    counts = np.bincount(ids * n_values + compact,
                         minlength=(max_id + 1) * n_values).reshape(max_id + 1, n_values)
    return unique_values[np.argmax(counts, axis=1)].astype(np.int32)


def identify_boundary_cells(labels, valid_mask):
    b = np.zeros(labels.shape, dtype=bool)
    b[:-1, :] |= valid_mask[:-1, :] & valid_mask[1:, :] & (labels[:-1, :] != labels[1:, :])
    b[1:, :] |= valid_mask[1:, :] & valid_mask[:-1, :] & (labels[1:, :] != labels[:-1, :])
    b[:, :-1] |= valid_mask[:, :-1] & valid_mask[:, 1:] & (labels[:, :-1] != labels[:, 1:])
    b[:, 1:] |= valid_mask[:, 1:] & valid_mask[:, :-1] & (labels[:, 1:] != labels[:, :-1])
    return b


def boundary_neighbor_matrix(labels, rows_idx, cols_idx):
    rows, cols = labels.shape
    offsets = [(-1, 0), (0, -1), (0, 1), (1, 0)]
    neighbors = np.zeros((rows_idx.size, len(offsets)), dtype=np.int32)
    for k, (dr, dc) in enumerate(offsets):
        rr, cc = rows_idx + dr, cols_idx + dc
        inside = (rr >= 0) & (rr < rows) & (cc >= 0) & (cc < cols)
        neighbors[inside, k] = labels[rr[inside], cc[inside]]
    return neighbors


def assignment_cost_vector(candidate_ids, edge_counts, stats, cell_features, cell_forest_type):
    cost = np.zeros(candidate_ids.size, dtype=np.float64)
    for name in CONTINUOUS:
        diff = cell_features[name] - stats["means"][name][candidate_ids]
        cost += WEIGHTS[name] * diff * diff
    cost += np.where(stats["dominant_type"][candidate_ids] == cell_forest_type,
                     0.0, WEIGHTS["FORTYPCD"])
    cost -= SHARED_EDGE_BONUS * edge_counts
    return cost


def iterative_reassignment(labels, valid_mask, feature_arrays, forest_type, ownership):
    total_valid = int(valid_mask.sum())
    ftype = np.where(valid_mask, forest_type, 0).astype(np.int32)
    for iteration in range(1, MAX_ITERATIONS + 1):
        t0 = time.perf_counter()
        stats = segment_statistics_arrays(labels, feature_arrays, ftype)
        segment_owner = segment_categorical_mode(labels, ownership)
        boundary = identify_boundary_cells(labels, valid_mask)
        br, bc = np.where(boundary)
        if br.size == 0:
            break
        current = labels[br, bc]
        neighbors = boundary_neighbor_matrix(labels, br, bc)
        cell_features = {n: a[br, bc].astype(np.float64) for n, a in feature_arrays.items()}
        cell_type = ftype[br, bc]
        cell_owner = ownership[br, bc]
        cur_edges = np.sum(neighbors == current[:, None], axis=1).astype(np.float64)
        best_ids = current.copy()
        best_cost = assignment_cost_vector(current, cur_edges, stats, cell_features, cell_type)
        for k in range(neighbors.shape[1]):
            cand = neighbors[:, k]
            ok = (cand > 0) & (cand != current) & (segment_owner[cand] == cell_owner)
            if not np.any(ok):
                continue
            cand_edges = np.sum(neighbors == cand[:, None], axis=1).astype(np.float64)
            cand_cost = assignment_cost_vector(cand, cand_edges, stats, cell_features, cell_type)
            improve = ok & (cand_cost < best_cost - MINIMUM_SCORE_IMPROVEMENT)
            best_cost[improve] = cand_cost[improve]
            best_ids[improve] = cand[improve]
        changed = best_ids != current
        n_changed = int(changed.sum())
        if n_changed:
            new_labels = labels.copy()
            new_labels[br[changed], bc[changed]] = best_ids[changed]
            labels = new_labels
        frac = n_changed / total_valid if total_valid else 0.0
        print(f"iter {iteration:02d}: {br.size:,} boundary cells, {n_changed:,} changed "
              f"({frac:.4%}), {time.perf_counter() - t0:.1f}s")
        if frac <= CONVERGENCE_THRESHOLD:
            break
    return labels


def split_disconnected_segments(labels):
    output = np.zeros_like(labels, dtype=np.int32)
    max_id = int(labels.max())
    if max_id == 0:
        return output
    slices = ndimage.find_objects(labels, max_label=max_id)
    structure = connectivity_structure()
    next_id = 1
    for old_id, bbox in enumerate(slices, start=1):
        if bbox is None:
            continue
        local = labels[bbox] == old_id
        pieces, n = ndimage.label(local, structure=structure)
        target = output[bbox]
        pos = pieces > 0
        target[pos] = pieces[pos] + next_id - 1
        next_id += n
    return output


def renumber_segments(labels):
    max_id = int(labels.max())
    if max_id == 0:
        return labels.astype(np.int32, copy=True)
    present = np.bincount(labels.ravel(), minlength=max_id + 1) > 0
    present[0] = False
    lookup = np.zeros(max_id + 1, dtype=np.int32)
    lookup[present] = np.arange(1, int(present.sum()) + 1, dtype=np.int32)
    return lookup[labels]


def segment_adjacency_arrays(labels):
    pair_a, pair_b = [], []
    for a, b in ((labels[:, :-1], labels[:, 1:]), (labels[:-1, :], labels[1:, :])):
        mask = (a > 0) & (b > 0) & (a != b)
        pair_a.append(a[mask].astype(np.int64))
        pair_b.append(b[mask].astype(np.int64))
    if sum(x.size for x in pair_a) == 0:
        return np.empty((0, 2), dtype=np.int64), np.empty(0, dtype=np.int64)
    a, b = np.concatenate(pair_a), np.concatenate(pair_b)
    lo, hi = np.minimum(a, b), np.maximum(a, b)
    max_id = int(labels.max()) + 1
    unique, counts = np.unique(lo * max_id + hi, return_counts=True)
    return np.column_stack((unique // max_id, unique % max_id)), counts.astype(np.int64)


def _bisect_connected_mask(mask):
    rows, cols = np.where(mask)
    if len(rows) < 2:
        return [mask]
    if int(cols.max() - cols.min()) >= int(rows.max() - rows.min()):
        cut = int(np.median(cols))
        grid = np.indices(mask.shape)[1]
    else:
        cut = int(np.median(rows))
        grid = np.indices(mask.shape)[0]
    first, second = mask & (grid <= cut), mask & (grid > cut)
    pieces = []
    for part in (first, second):
        if not np.any(part):
            continue
        comp, n = ndimage.label(part, structure=connectivity_structure())
        pieces.extend(comp == i for i in range(1, n + 1))
    if len(pieces) < 2:
        order = np.lexsort((cols, rows))
        half = len(order) // 2
        first = np.zeros(mask.shape, dtype=bool)
        second = np.zeros(mask.shape, dtype=bool)
        first[rows[order[:half]], cols[order[:half]]] = True
        second[rows[order[half:]], cols[order[half:]]] = True
        pieces = [first, second]
    return pieces


def split_oversized_segments(labels, maximum_cells):
    output = np.zeros_like(labels, dtype=np.int32)
    max_id = int(labels.max())
    slices = ndimage.find_objects(labels, max_label=max_id)
    next_id, n_split = 1, 0
    for seg_id, bbox in enumerate(slices, start=1):
        if bbox is None:
            continue
        local_out = output[bbox]
        queue = [labels[bbox] == seg_id]
        while queue:
            mask = queue.pop()
            n_cells = int(mask.sum())
            if n_cells <= maximum_cells or n_cells <= 1:
                local_out[mask] = next_id
                next_id += 1
                continue
            pieces = _bisect_connected_mask(mask)
            if len(pieces) < 2:
                local_out[mask] = next_id
                next_id += 1
                continue
            n_split += 1
            queue.extend(pieces)
    print(f"oversized-segment splits: {n_split}")
    return output


def merge_small_segments(labels, feature_arrays, forest_type, ownership,
                         minimum_cells, maximum_cells=None):
    pass_number = 0
    while True:
        pass_number += 1
        stats = segment_statistics_arrays(labels, feature_arrays, forest_type)
        segment_owner = segment_categorical_mode(labels, ownership)
        counts = stats["counts"].copy()
        active = np.flatnonzero(counts > 0)
        active = active[active > 0]
        small = active[counts[active] < minimum_cells]
        if small.size == 0:
            break
        pairs, shared_counts = segment_adjacency_arrays(labels)
        adjacency = defaultdict(list)
        for (a, b), s in zip(pairs, shared_counts):
            adjacency[int(a)].append((int(b), int(s)))
            adjacency[int(b)].append((int(a), int(s)))
        parent = np.arange(len(counts), dtype=np.int64)
        size = counts.astype(np.int64, copy=True)

        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return int(x)

        merged = 0
        for small_id in small[np.argsort(counts[small])]:
            src = find(int(small_id))
            if src != int(small_id) or size[src] >= minimum_cells:
                continue
            best_root, best_cost = None, float("inf")
            for nb, shared in adjacency.get(int(small_id), []):
                tgt = find(nb)
                if tgt == src or size[tgt] == 0:
                    continue
                if segment_owner[src] != segment_owner[tgt]:
                    continue
                if maximum_cells is not None and size[src] + size[tgt] > maximum_cells:
                    continue
                cost = 0.0
                for name in CONTINUOUS:
                    d = stats["means"][name][src] - stats["means"][name][tgt]
                    cost += WEIGHTS[name] * d * d
                if stats["dominant_type"][src] != stats["dominant_type"][tgt]:
                    cost += WEIGHTS["FORTYPCD"]
                cost -= SHARED_EDGE_BONUS * shared
                if cost < best_cost:
                    best_cost, best_root = cost, tgt
            if best_root is None:
                continue
            s_size, t_size = size[src], size[best_root]
            total = s_size + t_size
            for name in CONTINUOUS:
                combined = (stats["means"][name][src] * s_size
                            + stats["means"][name][best_root] * t_size)
                stats["means"][name][best_root] = combined / total if total else 0.0
            if stats["dominant_type"][src] == stats["dominant_type"][best_root]:
                stats["dominant_count"][best_root] += stats["dominant_count"][src]
            elif stats["dominant_count"][src] > stats["dominant_count"][best_root]:
                stats["dominant_type"][best_root] = stats["dominant_type"][src]
                stats["dominant_count"][best_root] = stats["dominant_count"][src]
            parent[src] = best_root
            size[best_root] = total
            size[src] = 0
            merged += 1
        print(f"small-segment merge pass {pass_number}: {merged} merged")
        if merged == 0:
            break
        lookup = np.arange(len(parent), dtype=np.int64)
        for seg_id in active:
            lookup[seg_id] = find(int(seg_id))
        labels = split_disconnected_segments(lookup[labels].astype(np.int32))
    return labels


def merge_similar_adjacent_stands(labels, raw_age, forest_type, ownership, maximum_cells):
    total_merged = 0
    for pass_number in range(1, SIMILAR_MERGE_MAX_PASSES + 1):
        stats = segment_statistics_arrays(labels, {"STDAGE": raw_age}, forest_type)
        counts = stats["counts"]
        age_means = stats["means"]["STDAGE"]
        dominant = stats["dominant_type"]
        segment_owner = segment_categorical_mode(labels, ownership)
        pairs, shared = segment_adjacency_arrays(labels)
        if pairs.size == 0:
            break
        first, second = pairs[:, 0], pairs[:, 1]
        age_diff = np.abs(age_means[first] - age_means[second])
        eligible = ((counts[first] + counts[second] <= maximum_cells)
                    & (age_diff <= SIMILAR_MERGE_MAX_AGE_DIFFERENCE)
                    & (shared >= 1)
                    & (segment_owner[first] == segment_owner[second])
                    & (dominant[first] == dominant[second]))
        cand = np.flatnonzero(eligible)
        if cand.size == 0:
            break
        order = cand[np.lexsort((-shared[cand], age_diff[cand]))]
        used = np.zeros(len(counts), dtype=bool)
        parent = np.arange(len(counts), dtype=np.int64)
        merged = 0
        for idx in order:
            a, b = int(first[idx]), int(second[idx])
            if used[a] or used[b]:
                continue
            if counts[a] > counts[b] or (counts[a] == counts[b] and a < b):
                target, source = a, b
            else:
                target, source = b, a
            parent[source] = target
            used[source] = used[target] = True
            merged += 1
        print(f"similar-stand merge pass {pass_number}: {merged} merged")
        if merged == 0:
            break
        labels = split_disconnected_segments(parent[labels].astype(np.int32))
        total_merged += merged
    print(f"total similar-stand merges: {total_merged}")
    return labels


def rasterize_riparian(shape, transform):
    """Buffer NHD flowlines by the LETO FCode rules and burn to the AOI grid."""
    import geopandas as gpd
    from rasterio.features import rasterize

    streams = gpd.read_file(STREAMS_SHP).to_crs("EPSG:5070")
    shapes = []
    for _, row in streams.iterrows():
        rule = FLOWLINE_BUFFER_RULES_FT.get(int(row["fcode"]))
        if rule is None or rule[1] <= 0:
            continue
        shapes.append(row.geometry.buffer(rule[1] * FT_TO_M))
    if not shapes:
        return np.zeros(shape, dtype=bool)
    burned = rasterize(((g, 1) for g in shapes), out_shape=shape,
                       transform=transform, fill=0, dtype="uint8")
    return burned.astype(bool)


def main() -> None:
    import json

    import rasterio

    data = np.load(WORK / "attributes.npz")
    valid = data["valid"]
    forest_type = np.where(valid, np.nan_to_num(data["FORTYPCD"], nan=0), 0).astype(np.int32)
    raw = {n: np.nan_to_num(data[n], nan=0.0) for n in CONTINUOUS}
    feature_arrays, scales = {}, {}
    for name in CONTINUOUS:
        std, med, scale = robust_standardize(data[name], valid)
        feature_arrays[name] = np.nan_to_num(std, nan=0.0)
        scales[name] = scale

    with rasterio.open(AOI_OWNERSHIP_TIF) as src:
        harris = src.read(1)
        transform = src.transform
    # CA ownership hard-boundary codes: forest owner classes stay distinct,
    # non-forest/water/nodata on forested TreeMap cells fall to unknown (0).
    ownership = np.where(np.isin(harris, list(HARRIS_TO_OWNER_CLASS)), harris, 0).astype(np.int16)
    ownership[~valid] = -1

    cells_per_seed = INITIAL_SEED_ACRES / CELL_ACRES
    min_cells = max(1, int(math.ceil(MIN_STAND_ACRES / CELL_ACRES)))
    max_cells = max(min_cells, int(MAX_STAND_ACRES / CELL_ACRES))
    print(f"{int(valid.sum()):,} valid cells; seed {cells_per_seed:.0f} cells, "
          f"min {min_cells}, max {max_cells}")

    labels = create_initial_segments(valid, cells_per_seed, ownership)
    print(f"initial segments: {labels.max():,}")
    labels = iterative_reassignment(labels, valid, feature_arrays, forest_type, ownership)
    labels = split_disconnected_segments(labels)
    labels = split_oversized_segments(labels, max_cells)
    labels = merge_small_segments(labels, feature_arrays, forest_type, ownership,
                                  min_cells, max_cells)
    if MERGE_SIMILAR_STANDS:
        labels = merge_similar_adjacent_stands(labels, raw["STDAGE"], forest_type,
                                               ownership, max_cells)
    seg_labels = renumber_segments(labels)
    n_segments = int(seg_labels.max())
    print(f"parent stands (SEG_ID): {n_segments:,}")

    # Riparian split: buffered NHD flowlines become their own management
    # units within each parent stand (LETO ManagementUnits_Final).
    riparian = rasterize_riparian(seg_labels.shape, transform) & valid
    print(f"riparian cells: {int(riparian.sum()):,} "
          f"({riparian.sum() * CELL_ACRES:.0f} acres)")
    combo = seg_labels.astype(np.int64) * 2 + riparian
    combo[seg_labels == 0] = 0
    mu_labels = np.zeros_like(seg_labels)
    next_id = 1
    structure = connectivity_structure()
    for value in np.unique(combo[combo > 0]):
        comp, n = ndimage.label(combo == value, structure=structure)
        pos = comp > 0
        mu_labels[pos] = comp[pos] + next_id - 1
        next_id += n
    print(f"management units (MU_ID): {int(mu_labels.max()):,}")

    # Per-MU summary
    valid_mu = mu_labels > 0
    ids = mu_labels[valid_mu].astype(np.int64)
    max_id = int(ids.max())
    counts = np.bincount(ids, minlength=max_id + 1)
    mu_ids = np.flatnonzero(counts > 0)
    mu_ids = mu_ids[mu_ids > 0]
    parent = segment_categorical_mode(mu_labels, seg_labels)
    owner_mode = segment_categorical_mode(mu_labels, np.maximum(ownership, 0))
    mgmt = segment_categorical_mode(mu_labels, riparian.astype(np.int32))
    dom_type = segment_categorical_mode(mu_labels, forest_type)
    rows = {"MU_ID": mu_ids, "PARENT_SEG": parent[mu_ids],
            "PIXEL_COUNT": counts[mu_ids], "ACRES": counts[mu_ids] * CELL_ACRES,
            "OWN_CODE": owner_mode[mu_ids],
            "OWNER_CLASS": [HARRIS_TO_OWNER_CLASS.get(int(c), "unknown")
                            for c in owner_mode[mu_ids]],
            "MGMT_CLASS": np.where(mgmt[mu_ids] > 0, RIPARIAN_MANAGEMENT_CODE,
                                   UPLAND_MANAGEMENT_CODE),
            "FORTYPCD_DOM": dom_type[mu_ids]}
    for name in CONTINUOUS:
        sums = np.bincount(ids, weights=raw[name][valid_mu], minlength=max_id + 1)
        rows[f"{name}_MEAN"] = np.divide(sums, counts, out=np.zeros(max_id + 1),
                                         where=counts > 0)[mu_ids]
    summary = pd.DataFrame(rows)
    summary.to_csv(WORK / "mu_summary.csv", index=False)

    # Donor weights: pixel share of each TreeMap plot (PLT_CN) within each MU
    # — LETO stage 3's segment/plot crosswalk.
    tm = data["tm"]
    vat = pd.read_csv(WORK / "vat_aoi.csv", dtype={"PLT_CN": str})
    value_to_plt = dict(zip(vat["Value"].astype(np.int64), vat["PLT_CN"]))
    pairs = pd.DataFrame({"MU_ID": mu_labels[valid_mu], "VALUE": tm[valid_mu]})
    weights = (pairs.groupby(["MU_ID", "VALUE"]).size().rename("CELLS").reset_index())
    weights["PLT_CN"] = weights["VALUE"].map(value_to_plt)
    weights["WEIGHT"] = weights["CELLS"] / weights.groupby("MU_ID")["CELLS"].transform("sum")
    weights.to_csv(WORK / "mu_donor_weights.csv", index=False)

    np.savez_compressed(WORK / "segmentation.npz", mu_labels=mu_labels,
                        seg_labels=seg_labels, riparian=riparian)
    qa = {
        "valid_cells": int(valid.sum()),
        "parent_segments": n_segments,
        "management_units": int(len(mu_ids)),
        "riparian_units": int((summary["MGMT_CLASS"] == RIPARIAN_MANAGEMENT_CODE).sum()),
        "total_acres": float(valid.sum() * CELL_ACRES),
        "acres_by_owner_class": summary.groupby("OWNER_CLASS")["ACRES"].sum().round(1).to_dict(),
        "homogeneity_scales": {k: float(v) for k, v in scales.items()},
    }
    (WORK / "segmentation_qa.json").write_text(json.dumps(qa, indent=2))
    print(json.dumps(qa, indent=2))


if __name__ == "__main__":
    main()
