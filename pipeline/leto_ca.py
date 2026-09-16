"""LETO cellular-automata stand segmentation (LETO stage 2) as a pure-array library.

A faithful port of the boundary-vectorized algorithm in LETO
`scripts/Cellular_automata/02_segment_treemap.py` (v3) to NumPy/SciPy, with no
raster I/O: callers pass arrays in and get label rasters back. This module
unifies the two ports that previously lived in
`experiments/2026-08-24_leto-ca-forest-viz/03_ca_segment.py` (constants,
riparian management-unit stage) and `research/leto_ca_demo/leto_ca.py`
(parameterized library interface).

The CA loop: every segment-boundary cell simultaneously evaluates joining each
adjacent neighbouring segment, at cost

    sum_f w_f * (cell_f - segment_mean_f)^2   over standardized continuous features
  + w_FORTYPCD * [segment dominant forest type != cell type]
  - shared_edge_bonus * shared edges with that segment

with reassignment only within the same ownership class (hard boundaries),
iterated until the changed fraction falls below the convergence threshold.
Post-CA: split disconnected pieces, split oversized segments, merge undersized
segments (union-find, lowest attribute cost), and merge similar adjacent
stands. The riparian stage then splits each parent stand into upland/riparian
management units (`split_management_units`).

Features are robust-standardised (median / IQR / 1.349, clipped to +-z) before
segmentation, exactly as LETO does. `DEFAULT_CFG` carries the constants the
experiments/2026-08-24 pipeline validated end-to-end against FVS; callers with
different data (e.g. a TreeMap VAT without STDAGE) override `variable_weights`
and the similar-merge settings rather than assuming the five-feature set.
"""

from __future__ import annotations

import math
from collections import defaultdict

import numpy as np
from scipy import ndimage

# Management-unit classes assigned by the riparian split.
UPLAND_MANAGEMENT_CODE = 0
RIPARIAN_MANAGEMENT_CODE = 1

# LETO 02_segment_treemap.py constants, as validated in the 2026-08-24
# experiment run. LETO ships 40 iterations / 1% convergence; this configuration
# lets the CA settle an order of magnitude further so boundaries stop moving
# rather than merely slowing (visual QA on the first render showed ragged,
# still-moving edges). The similar-stand merge gates on raw stand age (10-year
# window), LETO's maturity criterion.
DEFAULT_CFG = {
    "initial_seed_acres": 100.0,
    "minimum_stand_acres": 5.0,
    "maximum_stand_acres": 300.0,
    "maximum_iterations": 100,
    "convergence_threshold": 0.001,
    "minimum_score_improvement": 0.01,
    "use_eight_neighbors": False,
    "shared_edge_bonus": 0.1,
    "standardization_clip": 4.0,
    "variable_weights": {"FORTYPCD": 0.30, "STDAGE": 0.25, "BALIVE": 0.20,
                         "QMD": 0.15, "TPA": 0.10},
    "merge_similar_stands": True,
    "similar_merge_require_same_fortypcd": True,
    "similar_merge_min_shared_edges": 1,
    "similar_merge_similarity_name": "STDAGE",
    "similar_merge_max_similarity_difference": 10.0,
    "similar_merge_max_passes": 50,
}


def connectivity_structure() -> np.ndarray:
    return np.array([[0, 1, 0], [1, 1, 1], [0, 1, 0]], dtype=np.int8)


def robust_standardize(array, valid_mask, z_clip):
    """Median/IQR standardisation with a +-z_clip clamp — LETO's robust_standardize.

    Returns (standardized float32 array, median, scale) so callers can record
    the homogeneity scales for QA.
    """
    values = array[valid_mask]
    median = float(np.nanmedian(values))
    q1, q3 = (float(np.nanpercentile(values, p)) for p in (25, 75))
    iqr = q3 - q1
    scale = float(np.nanstd(values)) if not np.isfinite(iqr) or iqr == 0 else iqr / 1.349
    if not np.isfinite(scale) or scale == 0:
        scale = 1.0
    standardized = ((array - median) / scale).astype(np.float32)
    np.clip(standardized, -z_clip, z_clip, out=standardized)
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


def boundary_neighbor_matrix(labels, rows_idx, cols_idx, eight=False):
    rows, cols = labels.shape
    if eight:
        offsets = [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]
    else:
        offsets = [(-1, 0), (0, -1), (0, 1), (1, 0)]
    neighbors = np.zeros((rows_idx.size, len(offsets)), dtype=np.int32)
    for k, (dr, dc) in enumerate(offsets):
        rr, cc = rows_idx + dr, cols_idx + dc
        inside = (rr >= 0) & (rr < rows) & (cc >= 0) & (cc < cols)
        neighbors[inside, k] = labels[rr[inside], cc[inside]]
    return neighbors


def assignment_cost_vector(candidate_ids, edge_counts, stats, cell_features, cell_forest_type,
                           weights, continuous_names, shared_edge_bonus):
    cost = np.zeros(candidate_ids.size, dtype=np.float64)
    for name in continuous_names:
        diff = cell_features[name] - stats["means"][name][candidate_ids]
        cost += weights[name] * diff * diff
    cost += np.where(stats["dominant_type"][candidate_ids] == cell_forest_type,
                     0.0, weights["FORTYPCD"])
    cost -= shared_edge_bonus * edge_counts
    return cost


def iterative_reassignment(labels, valid_mask, feature_arrays, forest_type, ownership, *,
                           weights, continuous_names, shared_edge_bonus, eight_neighbors,
                           max_iterations, convergence_threshold, min_score_improvement,
                           log=print):
    total_valid = int(valid_mask.sum())
    ftype = np.where(valid_mask, forest_type, 0).astype(np.int32)
    for iteration in range(1, max_iterations + 1):
        stats = segment_statistics_arrays(labels, feature_arrays, ftype)
        segment_owner = segment_categorical_mode(labels, ownership)
        boundary = identify_boundary_cells(labels, valid_mask)
        br, bc = np.where(boundary)
        if br.size == 0:
            break
        current = labels[br, bc]
        neighbors = boundary_neighbor_matrix(labels, br, bc, eight_neighbors)
        cell_features = {n: feature_arrays[n][br, bc].astype(np.float64)
                         for n in continuous_names}
        cell_type = ftype[br, bc]
        cell_owner = ownership[br, bc]
        cur_edges = np.sum(neighbors == current[:, None], axis=1).astype(np.float64)
        best_ids = current.copy()
        best_cost = assignment_cost_vector(current, cur_edges, stats, cell_features, cell_type,
                                           weights, continuous_names, shared_edge_bonus)
        for k in range(neighbors.shape[1]):
            cand = neighbors[:, k]
            ok = (cand > 0) & (cand != current) & (segment_owner[cand] == cell_owner)
            if not np.any(ok):
                continue
            cand_edges = np.sum(neighbors == cand[:, None], axis=1).astype(np.float64)
            cand_cost = assignment_cost_vector(cand, cand_edges, stats, cell_features, cell_type,
                                               weights, continuous_names, shared_edge_bonus)
            improve = ok & (cand_cost < best_cost - min_score_improvement)
            best_cost[improve] = cand_cost[improve]
            best_ids[improve] = cand[improve]
        changed = best_ids != current
        n_changed = int(changed.sum())
        if n_changed:
            new_labels = labels.copy()
            new_labels[br[changed], bc[changed]] = best_ids[changed]
            labels = new_labels
        frac = n_changed / total_valid if total_valid else 0.0
        log(f"iter {iteration:02d}: {br.size:,} boundary cells, {n_changed:,} changed "
            f"({frac:.4%})")
        if frac <= convergence_threshold:
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


def split_oversized_segments(labels, maximum_cells, log=print):
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
    log(f"oversized-segment splits: {n_split}")
    return output


def merge_small_segments(labels, feature_arrays, forest_type, ownership, minimum_cells, *,
                         weights, continuous_names, shared_edge_bonus, maximum_cells=None,
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
                for name in continuous_names:
                    d = stats["means"][name][src] - stats["means"][name][tgt]
                    cost += weights[name] * d * d
                if stats["dominant_type"][src] != stats["dominant_type"][tgt]:
                    cost += weights["FORTYPCD"]
                cost -= shared_edge_bonus * shared
                if cost < best_cost:
                    best_cost, best_root = cost, tgt
            if best_root is None:
                continue
            s_size, t_size = size[src], size[best_root]
            total = s_size + t_size
            for name in continuous_names:
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
        log(f"small-segment merge pass {pass_number}: {merged} merged")
        if merged == 0:
            break
        lookup = np.arange(len(parent), dtype=np.int64)
        for seg_id in active:
            lookup[seg_id] = find(int(seg_id))
        labels = split_disconnected_segments(lookup[labels].astype(np.int32))
    return labels


def merge_similar_adjacent_stands(labels, similarity_features, forest_type, ownership,
                                  maximum_cells, *, similarity_name,
                                  max_similarity_difference, require_same_fortypcd=True,
                                  minimum_shared_edges=1, maximum_passes=50, log=print):
    """LETO's similar-stand merge.

    The maturity gate compares per-segment means of
    `similarity_features[similarity_name]` (raw stand age in LETO, with a
    10-year window); pass `max_similarity_difference=None` to disable the gate
    where the similarity attribute is unavailable.
    """
    total_merged = 0
    for pass_number in range(1, maximum_passes + 1):
        stats = segment_statistics_arrays(labels, similarity_features, forest_type)
        counts = stats["counts"]
        sim_means = stats["means"].get(similarity_name)
        dominant = stats["dominant_type"]
        segment_owner = segment_categorical_mode(labels, ownership)
        pairs, shared = segment_adjacency_arrays(labels)
        if pairs.size == 0:
            break
        first, second = pairs[:, 0], pairs[:, 1]
        eligible = ((counts[first] + counts[second] <= maximum_cells)
                    & (shared >= minimum_shared_edges)
                    & (segment_owner[first] == segment_owner[second]))
        if max_similarity_difference is not None and sim_means is not None:
            sim_diff = np.abs(sim_means[first] - sim_means[second])
            eligible &= sim_diff <= max_similarity_difference
        else:
            sim_diff = np.zeros(first.size)
        if require_same_fortypcd:
            eligible &= dominant[first] == dominant[second]
        cand = np.flatnonzero(eligible)
        if cand.size == 0:
            break
        order = cand[np.lexsort((-shared[cand], sim_diff[cand]))]
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
        log(f"similar-stand merge pass {pass_number}: {merged} merged")
        if merged == 0:
            break
        labels = split_disconnected_segments(parent[labels].astype(np.int32))
        total_merged += merged
    log(f"total similar-stand merges: {total_merged}")
    return labels


def segment(features_raw, forest_type, ownership, valid_mask, cell_acres, cfg=None, *,
            similarity_raw=None, info=None, log=print):
    """Run the full LETO CA segmentation. Returns renumbered int32 parent-stand
    labels (0 = nodata).

    features_raw:   dict of continuous feature name -> raw float raster
                    (pre-standardisation). FORTYPCD is categorical and passed
                    separately as `forest_type`.
    ownership:      integer raster of owner classes; a hard boundary for the CA.
                    Cells outside `valid_mask` should carry a sentinel that no
                    valid cell uses (e.g. -1).
    cfg:            overrides merged over DEFAULT_CFG. `variable_weights` must
                    cover FORTYPCD plus every key of `features_raw`.
    similarity_raw: optional dict of raw feature rasters for the similar-stand
                    merge gate (LETO compares raw stand-age means, not
                    standardized ones). Defaults to the standardized features.
    info:           optional dict; filled with "homogeneity_scales" (the robust
                    scale per feature) for QA output.
    """
    cfg = {**DEFAULT_CFG, **(cfg or {})}
    weights = dict(cfg["variable_weights"])
    continuous_names = list(features_raw)
    z_clip = cfg["standardization_clip"]

    std, scales = {}, {}
    for name in continuous_names:
        standardized, _median, scale = robust_standardize(features_raw[name], valid_mask, z_clip)
        std[name] = np.where(valid_mask, np.nan_to_num(standardized, nan=0.0), 0.0
                             ).astype(np.float32)
        scales[name] = scale
    if info is not None:
        info["homogeneity_scales"] = scales

    cells_per_seed = cfg["initial_seed_acres"] / cell_acres
    min_cells = max(1, int(math.ceil(cfg["minimum_stand_acres"] / cell_acres)))
    max_cells = max(min_cells, int(cfg["maximum_stand_acres"] / cell_acres))
    log(f"{int(valid_mask.sum()):,} valid cells; seed {cells_per_seed:.0f} cells, "
        f"min {min_cells}, max {max_cells}")

    labels = create_initial_segments(valid_mask, cells_per_seed, ownership)
    log(f"initial segments: {labels.max():,}")
    labels = iterative_reassignment(
        labels, valid_mask, std, forest_type, ownership,
        weights=weights, continuous_names=continuous_names,
        shared_edge_bonus=cfg["shared_edge_bonus"],
        eight_neighbors=cfg["use_eight_neighbors"],
        max_iterations=cfg["maximum_iterations"],
        convergence_threshold=cfg["convergence_threshold"],
        min_score_improvement=cfg["minimum_score_improvement"], log=log)
    labels = split_disconnected_segments(labels)
    labels = split_oversized_segments(labels, max_cells, log=log)
    labels = merge_small_segments(labels, std, forest_type, ownership, min_cells,
                                  weights=weights, continuous_names=continuous_names,
                                  shared_edge_bonus=cfg["shared_edge_bonus"],
                                  maximum_cells=max_cells, log=log)
    if cfg["merge_similar_stands"]:
        similarity_name = cfg["similar_merge_similarity_name"]
        similarity_features = similarity_raw if similarity_raw is not None else std
        labels = merge_similar_adjacent_stands(
            labels, similarity_features, forest_type, ownership, max_cells,
            similarity_name=similarity_name,
            max_similarity_difference=cfg["similar_merge_max_similarity_difference"],
            require_same_fortypcd=cfg["similar_merge_require_same_fortypcd"],
            minimum_shared_edges=cfg["similar_merge_min_shared_edges"],
            maximum_passes=cfg["similar_merge_max_passes"], log=log)
    labels = renumber_segments(labels)
    log(f"parent stands (SEG_ID): {int(labels.max()):,}")
    return labels


def split_management_units(seg_labels, riparian_mask):
    """Split each parent stand into upland/riparian management units (LETO
    ManagementUnits_Final): each spatially-connected piece of
    (parent stand x riparian class) becomes its own MU. Returns an int32
    MU-label raster (0 = nodata)."""
    combo = seg_labels.astype(np.int64) * 2 + riparian_mask
    combo[seg_labels == 0] = 0
    # split_disconnected_segments is O(n_unique_labels) via per-label bounding
    # boxes (ndimage.find_objects); a per-value `combo == value` + full-array
    # ndimage.label loop here would be O(n_unique_labels x total_cells) —
    # invisible at AOI scale but well over an hour at five-county scale.
    return split_disconnected_segments(combo.astype(np.int32))


def uniform_label_lookup(labels, values, dtype=np.int32):
    """Per-label value for an attribute that is constant within each label
    (e.g. a management unit's parent stand, or its riparian class). Returns an
    array indexed by label id. A direct lookup, not a categorical mode: the
    (n_labels x n_values) count table a mode builds allocates hundreds of
    gigabytes at full-region scale (188k x 155k)."""
    valid = labels > 0
    ids = labels[valid].astype(np.int64, copy=False)
    max_id = int(ids.max()) if ids.size else 0
    lookup = np.zeros(max_id + 1, dtype=dtype)
    lookup[ids] = values[valid]
    return lookup
