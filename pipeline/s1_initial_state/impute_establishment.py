"""Nearest-neighbour imputation of establishment tree lists for recovered patches.

The hole-rectification work returns harvested forest to TreeMap as an *add-back
mask* — acreage, not inventory. A recovered patch carries no ``TM_ID`` and
therefore no tree list, so it cannot seed FVS. This module closes that gap the
simple way AGENTS.md prescribes: re-establish each recovered patch with the
initial treelist of a nearby, similar unit.

The rule is deliberately minimal and deterministic:

- **Donor** = the modal TreeMap ``TM_ID`` in a ring around the patch. The ring
  is the patch's neighbourhood under a 7 x 7 structuring element (Chebyshev
  radius 3), matching the donor survey in
  ``docs/treemap-raster-correction/presentation.html``. If the first ring holds
  no TreeMap plot at all, the radius doubles (7, 15, 31, ...) up to a limit;
  patches that never reach a donor are reported unresolved, never guessed.
- **Establishment list** = the donor plot's own tree rows, taken verbatim, as
  an age-0 planting prescription: species mix and planting density (TPA) are
  the donor's, which is exactly the "pattern of nearby, similar units" the
  landscape should be re-seeded with. Downstream stages may clearcut-and-
  regenerate the donor in FVS; this module only fixes the *identity* of the
  tree list and its density.

Provenance stays explicit: nothing here rewrites measured plots. The repaired
raster carries the donor ``TM_ID`` on recovered pixels plus a provenance band,
and ``establishment_patches.csv`` records which patches were imputed and from
whom.

Identifiers never touch a float: ``TM_ID``/``PLT_CN`` are coerced with
:func:`pipeline.ids.as_id_series` (see the gotcha in AGENTS.md — a 19-digit
``PLT_CN`` silently loses digits through float64).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import ndimage

from pipeline.ids import as_id_series, report_key_overlap

ACRES_PER_PIXEL = 0.2224  # 30 m pixel = 900 m²

# Establishment window per stratum (low, high) — when the stand was last cut.
# S1 was cut before the 2016 vintage; S2/S3 between the 2016 and 2022 vintages;
# S4 is regrowth with no dated cut evidence, S5 is not added back at all.
STRATUM_CUT_WINDOW = {
    1: (None, 2016),
    2: (2016, 2022),
    3: (2016, 2022),
    4: (None, None),
    5: (None, None),
}

RING_RADII = (3, 7, 15, 31, 63)


def label_patches(mask: np.ndarray) -> tuple[np.ndarray, int]:
    """8-connected patch labels (1..count) for a boolean mask; 0 outside."""
    structure = np.ones((3, 3), dtype=bool)
    labels, count = ndimage.label(mask, structure=structure)
    return labels.astype(np.int32), int(count)


def _patch_stratum(strata: np.ndarray | None, patch: np.ndarray) -> int:
    """Modal stratum over the patch's own pixels (0 when no strata given)."""
    if strata is None:
        return 0
    values = strata[patch]
    values = values[values > 0]
    if values.size == 0:
        return 0
    counts = np.bincount(values)
    return int(counts.argmax())


def donor_assignments(
    donors: np.ndarray,
    labels: np.ndarray,
    *,
    strata: np.ndarray | None = None,
    ring_radii: tuple[int, ...] = RING_RADII,
) -> tuple[pd.DataFrame, list[int]]:
    """One donor ``TM_ID`` per patch: the modal TreeMap plot in its ring.

    ``donors`` is an int array of TreeMap ``TM_ID`` values on the same grid as
    ``labels`` — 0 (or any non-positive value) marks a pixel with no plot, i.e.
    a hole, a patch pixel, or nodata, and can never be selected as a donor.
    ``strata`` optionally carries the 1–5 hole strata for the record.

    Returns ``(assignments, unresolved)`` where ``assignments`` is indexed by
    patch id and ``unresolved`` lists the patch ids for which no ring radius
    reached any TreeMap plot. Ties for the modal donor resolve to the lowest
    ``TM_ID`` so the result is reproducible.
    """
    patches = ndimage.find_objects(labels)
    rows = []
    unresolved: list[int] = []
    for patch_id, bbox in enumerate(patches, start=1):
        if bbox is None:
            continue
        patch = labels[bbox] == patch_id
        pixels = int(patch.sum())
        stratum = _patch_stratum(strata[bbox] if strata is not None else None, patch)
        low, high = STRATUM_CUT_WINDOW.get(stratum, (None, None))

        donor = pd.NA
        n_rows, n_cols = labels.shape
        for radius in ring_radii:
            r0 = max(bbox[0].start - radius, 0)
            r1 = min(bbox[0].stop + radius, n_rows)
            c0 = max(bbox[1].start - radius, 0)
            c1 = min(bbox[1].stop + radius, n_cols)
            window = donors[r0:r1, c0:c1]
            candidates = window[window > 0]
            if candidates.size:
                counts = np.bincount(candidates)
                donor = str(int(counts.argmax()))  # argmax -> lowest id on ties
                break
        else:
            unresolved.append(patch_id)

        rows.append({
            "patch_id": patch_id,
            "pixels": pixels,
            "acres": pixels * ACRES_PER_PIXEL,
            "stratum": stratum,
            "donor_tm_id": donor,
            "est_year_low": low,
            "est_year_high": high,
        })

    assignments = pd.DataFrame(rows).set_index("patch_id")
    return assignments, unresolved


def establishment_tree_lists(
    assignments: pd.DataFrame, tree_table: pd.DataFrame
) -> pd.DataFrame:
    """The donor plots' tree rows, verbatim, keyed to the patches they seed.

    ``tree_table`` is the TreeMap tree table (one row per tree). Its ``TM_ID``
    and ``PLT_CN`` columns are coerced to exact strings — a 19-digit
    ``PLT_CN`` through a float is the bug class from AGENTS.md. Rows are
    returned once per donor; patches sharing a donor share rows, so the frame
    stays the size of the donor population, not the imputed acreage.
    """
    donor_ids = as_id_series(
        assignments.loc[assignments["donor_tm_id"].notna(), "donor_tm_id"],
        column="TM_ID",
    )
    table = tree_table.copy()
    table["TM_ID"] = as_id_series(table["TM_ID"], column="TM_ID")
    if "PLT_CN" in table.columns:
        table["PLT_CN"] = as_id_series(table["PLT_CN"], column="PLT_CN")
    report_key_overlap(
        donor_ids, table["TM_ID"], left_name="patch donor", right_name="tree table"
    )

    donors = pd.DataFrame({"TM_ID": donor_ids}).drop_duplicates()
    lists = donors.merge(table, on="TM_ID", how="inner")
    resolved = assignments[assignments["donor_tm_id"].notna()]
    keys = pd.DataFrame({
        "TM_ID": as_id_series(resolved["donor_tm_id"], column="TM_ID"),
        # patch id may be the index (donor_assignments output) or a column.
        "patch_id": (
            resolved["patch_id"] if "patch_id" in resolved.columns else resolved.index
        ),
        "acres": resolved["acres"],
        "stratum": resolved["stratum"],
    }).reset_index(drop=True)
    return lists.merge(keys, on="TM_ID", how="inner")
