"""
LETO cellular-automata TreeMap segmentation — a faithful port of the algorithm in
aauslander480/Leto `LETO_v1_project/leto/stage2_segmentation.py`, with arcpy stripped
out so it runs on plain NumPy/SciPy arrays in this sandbox.

The mechanism is unchanged from LETO v1:

  1. seed the valid raster into ~`initial_seed_acres` blocks, split by ownership and
     4-connectivity (`create_initial_segments`);
  2. synchronously reassign every *boundary* cell to the adjacent segment that minimises
     a local cost  =  Σ w_f · (cell_f − seg_mean_f)²  +  w_FORTYPCD·[type≠dom]
                       −  shared_edge_bonus · shared_edges,
     with ownership a hard boundary (a cell may only join a segment of its own owner);
     iterate until the changed fraction falls below `convergence_threshold`
     (`iterative_reassignment`);
  3. split disconnected pieces, split oversized segments, merge undersized segments, and
     merge biologically-similar adjacent stands (`split_*`, `merge_*`).

Features are robust-standardised (median / IQR, clipped to ±z) before segmentation, exactly
as LETO does. The Florida TreeMap VAT carries FORTYPCD/BALIVE/QMD/TPA but not STDAGE, so the
caller passes whatever continuous features it has and their weights; this module does not
assume the five-feature Maine set.
"""

from __future__ import annotations

import math
from collections import defaultdict

import numpy as np
from scipy import ndimage


def connectivity_structure() -> np.ndarray:
    return np.array([[0, 1, 0], [1, 1, 1], [0, 1, 0]], dtype=np.int8)


def robust_standardize(array: np.ndarray, valid_mask: np.ndarray, z_clip: float):
    """Median/IQR standardisation with a ±z_clip clamp — LETO's `robust_standardize`."""
    values = array[valid_mask]
    median = float(np.nanmedian(values))
    q1 = float(np.nanpercentile(values, 25))
    q3 = float(np.nanpercentile(values, 75))
    iqr = q3 - q1
    scale = float(np.nanstd(values)) if (not np.isfinite(iqr) or iqr == 0) else iqr / 1.349
    if not np.isfinite(scale) or scale == 0:
        scale = 1.0
    standardized = ((array - median) / scale).astype(np.float32)
    np.clip(standardized, -z_clip, z_clip, out=standardized)
    return standardized


def create_initial_segments(valid_mask, cells_per_seed, ownership) -> np.ndarray:
    rows, cols = valid_mask.shape
    block_side = max(1, int(round(math.sqrt(cells_per_seed))))
    labels = np.zeros((rows, cols), dtype=np.int32)
    next_id = 1
    structure = connectivity_structure()
    for row_start in range(0, rows, block_side):
        row_end = min(rows, row_start + block_side)
        for col_start in range(0, cols, block_side):
            col_end = min(cols, col_start + block_side)
            block_mask = valid_mask[row_start:row_end, col_start:col_end]
            if not np.any(block_mask):
                continue
            block_owner = ownership[row_start:row_end, col_start:col_end]
            target = labels[row_start:row_end, col_start:col_end]
            for owner_code in np.unique(block_owner[block_mask]):
                owner_mask = block_mask & (block_owner == owner_code)
                local_labels, component_count = ndimage.label(owner_mask, structure=structure)
                positive = local_labels > 0
                target[positive] = local_labels[positive] + next_id - 1
                next_id += component_count
    return labels


def segment_statistics_arrays(labels, feature_arrays, forest_type):
    valid = labels > 0
    ids = labels[valid].astype(np.int64, copy=False)
    max_id = int(ids.max()) if ids.size else 0
    counts = np.bincount(ids, minlength=max_id + 1).astype(np.int64)
    means = {}
    for name, array in feature_arrays.items():
        values = array[valid].astype(np.float64, copy=False)
        sums = np.bincount(ids, weights=values, minlength=max_id + 1)
        means[name] = np.divide(sums, counts, out=np.zeros(max_id + 1), where=counts > 0)
    types = forest_type[valid].astype(np.int64, copy=False)
    unique_types, compact_types = np.unique(types, return_inverse=True)
    n_types = len(unique_types)
    if n_types:
        combined = ids * n_types + compact_types
        pair_counts = np.bincount(combined, minlength=(max_id + 1) * n_types).reshape(max_id + 1, n_types)
        dominant_index = np.argmax(pair_counts, axis=1)
        dominant_type = unique_types[dominant_index].astype(np.int32)
        dominant_count = pair_counts[np.arange(max_id + 1), dominant_index].astype(np.int64)
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
    combined = ids * n_values + compact
    counts = np.bincount(combined, minlength=(max_id + 1) * n_values).reshape(max_id + 1, n_values)
    return unique_values[np.argmax(counts, axis=1)].astype(np.int32)


def identify_boundary_cells(labels, valid_mask):
    boundary = np.zeros(labels.shape, dtype=bool)
    boundary[:-1, :] |= valid_mask[:-1, :] & valid_mask[1:, :] & (labels[:-1, :] != labels[1:, :])
    boundary[1:, :] |= valid_mask[1:, :] & valid_mask[:-1, :] & (labels[1:, :] != labels[:-1, :])
    boundary[:, :-1] |= valid_mask[:, :-1] & valid_mask[:, 1:] & (labels[:, :-1] != labels[:, 1:])
    boundary[:, 1:] |= valid_mask[:, 1:] & valid_mask[:, :-1] & (labels[:, 1:] != labels[:, :-1])
    return boundary


def boundary_neighbor_matrix(labels, brows, bcols, eight):
    rows, cols = labels.shape
    offsets = [(-1, 0), (0, -1), (0, 1), (1, 0)]
    if eight:
        offsets = [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]
    neighbors = np.zeros((brows.size, len(offsets)), dtype=np.int32)
    for index, (dr, dc) in enumerate(offsets):
        rr, cc = brows + dr, bcols + dc
        inside = (rr >= 0) & (rr < rows) & (cc >= 0) & (cc < cols)
        neighbors[inside, index] = labels[rr[inside], cc[inside]]
    return neighbors


def _cost_vector(candidate_ids, edge_counts, stats, cell_features, cell_ft, weights,
                 continuous_names, shared_edge_bonus):
    cost = np.zeros(candidate_ids.size, dtype=np.float64)
    means = stats["means"]
    for name in continuous_names:
        d = cell_features[name] - means[name][candidate_ids]
        cost += weights[name] * d * d
    cost += np.where(stats["dominant_type"][candidate_ids] == cell_ft, 0.0, weights["FORTYPCD"])
    cost -= shared_edge_bonus * edge_counts
    return cost


def iterative_reassignment(labels, valid_mask, feature_arrays, forest_type, ownership,
                           *, weights, continuous_names, shared_edge_bonus, eight_neighbors,
                           max_iterations, convergence_threshold, min_score_improvement,
                           log=print):
    total_valid = int(valid_mask.sum())
    ft_int = np.where(valid_mask, forest_type, 0).astype(np.int32)
    for iteration in range(1, max_iterations + 1):
        stats = segment_statistics_arrays(labels, feature_arrays, ft_int)
        segment_owner = segment_categorical_mode(labels, ownership)
        boundary = identify_boundary_cells(labels, valid_mask)
        brows, bcols = np.where(boundary)
        if brows.size == 0:
            log("  no segment boundaries remain")
            break
        current_ids = labels[brows, bcols]
        neighbor_ids = boundary_neighbor_matrix(labels, brows, bcols, eight_neighbors)
        cell_features = {n: feature_arrays[n][brows, bcols].astype(np.float64) for n in continuous_names}
        cell_ft = ft_int[brows, bcols]
        cell_owner = ownership[brows, bcols]
        current_edges = np.sum(neighbor_ids == current_ids[:, None], axis=1).astype(np.float64)
        best_ids = current_ids.copy()
        best_cost = _cost_vector(current_ids, current_edges, stats, cell_features, cell_ft,
                                 weights, continuous_names, shared_edge_bonus)
        for direction in range(neighbor_ids.shape[1]):
            cand = neighbor_ids[:, direction]
            cand_valid = (cand > 0) & (cand != current_ids) & (segment_owner[cand] == cell_owner)
            if not np.any(cand_valid):
                continue
            cand_edges = np.sum(neighbor_ids == cand[:, None], axis=1).astype(np.float64)
            cand_cost = _cost_vector(cand, cand_edges, stats, cell_features, cell_ft,
                                     weights, continuous_names, shared_edge_bonus)
            improve = cand_valid & (cand_cost < best_cost - min_score_improvement)
            best_cost[improve] = cand_cost[improve]
            best_ids[improve] = cand[improve]
        changed_mask = best_ids != current_ids
        changed = int(changed_mask.sum())
        if changed:
            new_labels = labels.copy()
            new_labels[brows[changed_mask], bcols[changed_mask]] = best_ids[changed_mask]
            labels = new_labels
        frac = changed / total_valid if total_valid else 0.0
        log(f"  iter {iteration:02d}: {brows.size:,} boundary cells, {changed:,} changed ({frac:.3%})")
        if frac <= convergence_threshold:
            log("  convergence threshold reached")
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
        local_mask = labels[bbox] == old_id
        pieces, piece_count = ndimage.label(local_mask, structure=structure)
        target = output[bbox]
        positive = pieces > 0
        target[positive] = pieces[positive] + next_id - 1
        next_id += piece_count
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
    left, right = labels[:, :-1], labels[:, 1:]
    m = (left > 0) & (right > 0) & (left != right)
    pair_a.append(left[m].astype(np.int64))
    pair_b.append(right[m].astype(np.int64))
    upper, lower = labels[:-1, :], labels[1:, :]
    m = (upper > 0) & (lower > 0) & (upper != lower)
    pair_a.append(upper[m].astype(np.int64))
    pair_b.append(lower[m].astype(np.int64))
    if sum(a.size for a in pair_a) == 0:
        return np.empty((0, 2), dtype=np.int64), np.empty(0, dtype=np.int64)
    a = np.concatenate(pair_a)
    b = np.concatenate(pair_b)
    lo, hi = np.minimum(a, b), np.maximum(a, b)
    max_id = int(labels.max()) + 1
    encoded = lo * max_id + hi
    unique, counts = np.unique(encoded, return_counts=True)
    pairs = np.column_stack((unique // max_id, unique % max_id)).astype(np.int64)
    return pairs, counts.astype(np.int64)


def _bisect_connected_mask(mask):
    rows, cols = np.where(mask)
    if len(rows) < 2:
        return [mask]
    row_span = int(rows.max() - rows.min())
    col_span = int(cols.max() - cols.min())
    if col_span >= row_span:
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
        for cid in range(1, n + 1):
            c = comp == cid
            if np.any(c):
                pieces.append(c)
    if len(pieces) < 2:
        order = np.lexsort((cols, rows))
        half = len(order) // 2
        first = np.zeros(mask.shape, dtype=bool)
        second = np.zeros(mask.shape, dtype=bool)
        first[rows[order[:half]], cols[order[:half]]] = True
        second[rows[order[half:]], cols[order[half:]]] = True
        pieces = [first, second]
    return pieces


def split_oversized_segments(labels, maximum_cells, log=print):
    output = np.zeros_like(labels, dtype=np.int32)
    max_id = int(labels.max())
    slices = ndimage.find_objects(labels, max_label=max_id)
    next_id = 1
    split_count = 0
    for segment_id, bbox in enumerate(slices, start=1):
        if bbox is None:
            continue
        queue = [labels[bbox] == segment_id]
        local_output = output[bbox]
        while queue:
            mask = queue.pop()
            n = int(mask.sum())
            if n <= maximum_cells or n <= 1:
                local_output[mask] = next_id
                next_id += 1
                continue
            pieces = _bisect_connected_mask(mask)
            if len(pieces) < 2:
                local_output[mask] = next_id
                next_id += 1
                continue
            split_count += 1
            queue.extend(pieces)
    log(f"  oversized-split operations: {split_count:,}")
    return output


def merge_small_segments(labels, feature_arrays, forest_type, ownership, minimum_cells,
                         *, weights, continuous_names, shared_edge_bonus, maximum_cells=None,
                         log=print):
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
        pairs, shared = segment_adjacency_arrays(labels)
        adjacency = defaultdict(list)
        for (a, b), s in zip(pairs, shared):
            adjacency[int(a)].append((int(b), int(s)))
            adjacency[int(b)].append((int(a), int(s)))
        parent = np.arange(len(counts), dtype=np.int64)
        current_size = counts.astype(np.int64, copy=True)

        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return int(x)

        merged = 0
        for small_id in small[np.argsort(counts[small])]:
            source_root = find(int(small_id))
            if source_root != int(small_id) or current_size[source_root] >= minimum_cells:
                continue
            best_root, best_cost = None, float("inf")
            for neighbor_id, shared_edges in adjacency.get(int(small_id), []):
                troot = find(neighbor_id)
                if troot == source_root or current_size[troot] == 0:
                    continue
                if segment_owner[source_root] != segment_owner[troot]:
                    continue
                combined = current_size[source_root] + current_size[troot]
                if maximum_cells is not None and combined > maximum_cells:
                    continue
                cost = 0.0
                for name in continuous_names:
                    d = stats["means"][name][source_root] - stats["means"][name][troot]
                    cost += weights[name] * d * d
                if stats["dominant_type"][source_root] != stats["dominant_type"][troot]:
                    cost += weights["FORTYPCD"]
                cost -= shared_edge_bonus * shared_edges
                if cost < best_cost:
                    best_cost, best_root = cost, troot
            if best_root is None:
                continue
            ssz, tsz = current_size[source_root], current_size[best_root]
            comb = ssz + tsz
            for name in continuous_names:
                stats["means"][name][best_root] = (
                    (stats["means"][name][source_root] * ssz + stats["means"][name][best_root] * tsz) / comb
                    if comb > 0 else 0.0)
            if stats["dominant_type"][source_root] == stats["dominant_type"][best_root]:
                stats["dominant_count"][best_root] += stats["dominant_count"][source_root]
            elif stats["dominant_count"][source_root] > stats["dominant_count"][best_root]:
                stats["dominant_type"][best_root] = stats["dominant_type"][source_root]
                stats["dominant_count"][best_root] = stats["dominant_count"][source_root]
            parent[source_root] = best_root
            current_size[best_root] = comb
            current_size[source_root] = 0
            merged += 1
        log(f"  small-merge pass {pass_number}: {merged:,} merged")
        if merged == 0:
            break
        lookup = np.arange(len(parent), dtype=np.int64)
        for sid in active:
            lookup[sid] = find(int(sid))
        labels = lookup[labels].astype(np.int32)
        labels = split_disconnected_segments(labels)
    return labels


def merge_similar_adjacent_stands(labels, feature_arrays, forest_type, ownership,
                                  maximum_cells, *, require_same_fortypcd=True,
                                  minimum_shared_edges=2, similarity_name="QMD",
                                  max_similarity_difference=None, maximum_passes=50, log=print):
    """LETO's similar-stand merge. Age is unavailable for FL, so the maturity gate uses
    `similarity_name` (default QMD) with `max_similarity_difference`; None disables the gate."""
    total = 0
    for pass_number in range(1, maximum_passes + 1):
        stats = segment_statistics_arrays(labels, feature_arrays, forest_type)
        counts = stats["counts"]
        sim_means = stats["means"].get(similarity_name)
        dom = stats["dominant_type"]
        segment_owner = segment_categorical_mode(labels, ownership)
        pairs, shared = segment_adjacency_arrays(labels)
        if pairs.size == 0:
            break
        first, second = pairs[:, 0], pairs[:, 1]
        combined = counts[first] + counts[second]
        eligible = (combined <= maximum_cells) & (shared >= minimum_shared_edges) & \
                   (segment_owner[first] == segment_owner[second])
        if max_similarity_difference is not None and sim_means is not None:
            sim_diff = np.abs(sim_means[first] - sim_means[second])
            eligible &= sim_diff <= max_similarity_difference
        else:
            sim_diff = np.zeros(first.size)
        if require_same_fortypcd:
            eligible &= dom[first] == dom[second]
        cand = np.flatnonzero(eligible)
        if cand.size == 0:
            break
        order = cand[np.lexsort((-shared[cand], sim_diff[cand]))]
        used = np.zeros(len(counts), dtype=bool)
        parent = np.arange(len(counts), dtype=np.int64)
        merged = 0
        for index in order:
            a, b = int(first[index]), int(second[index])
            if used[a] or used[b]:
                continue
            if counts[a] > counts[b] or (counts[a] == counts[b] and a < b):
                target, source = a, b
            else:
                target, source = b, a
            parent[source] = target
            used[source] = used[target] = True
            merged += 1
        if merged == 0:
            break
        labels = parent[labels].astype(np.int32)
        labels = split_disconnected_segments(labels)
        total += merged
        log(f"  similar-merge pass {pass_number}: {merged:,} merged")
    log(f"  total similar merged: {total:,}")
    return labels


def segment(features_raw, forest_type, ownership, valid_mask, cell_acres, cfg, log=print):
    """Run the full LETO CA segmentation. Returns renumbered int32 labels (0 = nodata).

    `features_raw`: dict of continuous feature name -> raw float raster (pre-standardisation).
    `cfg`: dict with the LETO `segmentation` block plus `variable_weights`.
    """
    weights = dict(cfg["variable_weights"])
    continuous_names = [n for n in features_raw]  # FORTYPCD is categorical, handled separately
    z = cfg["standardization_clip"]
    std = {n: robust_standardize(features_raw[n], valid_mask, z) for n in continuous_names}
    for n in continuous_names:
        std[n] = np.where(valid_mask, std[n], 0.0).astype(np.float32)

    cells_per_seed = cfg["initial_seed_acres"] / cell_acres
    min_cells = max(1, int(round(cfg["minimum_stand_acres"] / cell_acres)))
    max_cells = int(round(cfg["maximum_stand_acres"] / cell_acres))

    log(f"seed≈{cells_per_seed:.0f} cells, min={min_cells}, max={max_cells} cells/stand")
    labels = create_initial_segments(valid_mask, cells_per_seed, ownership)
    log(f"initial segments: {int(labels.max()):,}")
    labels = iterative_reassignment(
        labels, valid_mask, std, forest_type, ownership,
        weights=weights, continuous_names=continuous_names,
        shared_edge_bonus=cfg["shared_edge_bonus"], eight_neighbors=cfg["use_eight_neighbors"],
        max_iterations=cfg["maximum_iterations"], convergence_threshold=cfg["convergence_threshold"],
        min_score_improvement=cfg["minimum_score_improvement"], log=log)
    labels = split_disconnected_segments(labels)
    labels = split_oversized_segments(labels, max_cells, log=log)
    labels = merge_small_segments(labels, std, forest_type, ownership, min_cells,
                                  weights=weights, continuous_names=continuous_names,
                                  shared_edge_bonus=cfg["shared_edge_bonus"],
                                  maximum_cells=max_cells, log=log)
    if cfg.get("merge_similar_stands", True):
        labels = merge_similar_adjacent_stands(
            labels, std, forest_type, ownership, max_cells,
            require_same_fortypcd=cfg["similar_merge_require_same_fortypcd"],
            minimum_shared_edges=cfg["similar_merge_min_shared_edges"],
            max_similarity_difference=cfg.get("similar_merge_max_similarity_difference"),
            maximum_passes=cfg["similar_merge_max_passes"], log=log)
    labels = renumber_segments(labels)
    log(f"final stands: {int(labels.max()):,}")
    return labels
