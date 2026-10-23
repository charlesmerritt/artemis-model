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
- **Establishment list** depends on the patch's bookend stratum, per
  ``config/establishment.yaml`` (:class:`EstablishmentPolicy`). The donor always gives
  the forest type (TreeMap VAT ``FORTYPCD``) and the species mix (live TPA share by
  ``SPCD``). Under :attr:`EstablishmentMode.SCALED_YOUNG` (S1, S3, S4: cut or regrowing)
  density and tree size come from the donor forest type group's age-5 FIA profile
  (:mod:`pipeline.s1_initial_state.young_stand_profiles`), so a mature neighbour never
  puts mature biomass on cut ground. Under :attr:`EstablishmentMode.DONOR_AS_IS` (S2:
  tree at both bookends) the donor's own rows are used verbatim. Decision record:
  ``docs/adr/0002-scaled-nearest-neighbour-establishment.md``.
  :func:`establishment_tree_lists` (every patch gets the donor's verbatim rows) is
  what ``statewide_repair`` still writes; it has not adopted the modes.

Provenance stays explicit: nothing here rewrites measured plots. The repaired
raster carries the donor ``TM_ID`` on recovered pixels plus a provenance band,
and ``establishment_patches.csv`` records which patches were imputed and from
whom.

Identifiers never touch a float: ``TM_ID``/``PLT_CN`` are coerced with
:func:`pipeline.ids.as_id_series` (see the gotcha in AGENTS.md — a 19-digit
``PLT_CN`` silently loses digits through float64).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

import numpy as np
import pandas as pd
import yaml
from scipy import ndimage

from pipeline.ids import as_id_series, report_key_overlap
from pipeline.s1_initial_state.young_stand_profiles import (
    BA_FACTOR,
    ESTABLISHMENT_PATH,
    PARENT,
    PROFILES_PATH,
    ForestTypeGroup,
    YoungStandProfile,
    forest_type_group,
    load_young_stand_profiles,
)

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



# ── scaled nearest-neighbour establishment ──────────────────────────────────────────────


class EstablishmentMode(StrEnum):
    """What an added-back patch takes from its donor plot."""

    SCALED_YOUNG = "scaled_young"   # type and species mix from the donor; density and size at target age
    DONOR_AS_IS = "donor_as_is"     # the donor's tree rows verbatim


class EstablishmentDensity(StrEnum):
    """Where a scaled list's TPA comes from."""

    FOREST_TYPE_PROFILE = "forest_type_profile"   # the age-5 profile's TPA, split by species share
    DONOR = "donor"                               # the donor's own live TPA by species


# Without evidence of standing forest, never add mature biomass.
UNLISTED_STRATUM_MODE = EstablishmentMode.SCALED_YOUNG


def mode_for_stratum(stratum, stratum_modes: Mapping[int, EstablishmentMode]) -> EstablishmentMode:
    """The configured mode of a bookend stratum; :data:`UNLISTED_STRATUM_MODE` if unlisted."""
    return stratum_modes.get(int(stratum), UNLISTED_STRATUM_MODE)


@dataclass(frozen=True)
class EstablishmentPolicy:
    target_age: int
    density: EstablishmentDensity
    stratum_modes: Mapping[int, EstablishmentMode]
    profiles: Mapping[ForestTypeGroup, YoungStandProfile]

    def mode_for(self, stratum: int) -> EstablishmentMode:
        return mode_for_stratum(stratum, self.stratum_modes)

    def profile_for(self, group: ForestTypeGroup) -> YoungStandProfile:
        """The group's profile, or the nearest pool's up :data:`PARENT` when it has none."""
        while group not in self.profiles:
            if group not in PARENT:
                raise KeyError(f"no young-stand profile for {group} or any pool above it")
            group = PARENT[group]
        return self.profiles[group]

    @classmethod
    def from_config(cls, config: dict, profiles: Mapping[ForestTypeGroup, YoungStandProfile]):
        return cls(int(config["target_age"]), EstablishmentDensity(config["density"]),
                   {int(s): EstablishmentMode(m) for s, m in config["stratum_modes"].items()},
                   dict(profiles))


def load_establishment_policy(path: Path = ESTABLISHMENT_PATH,
                              profiles_path: Path = PROFILES_PATH) -> EstablishmentPolicy:
    """``config/establishment.yaml`` with the committed ``config/young_stand_profiles.yaml``."""
    return EstablishmentPolicy.from_config(yaml.safe_load(Path(path).read_text()),
                                           load_young_stand_profiles(profiles_path))


def _live_species_tpa(rows: pd.DataFrame) -> pd.Series:
    """Live TPA by species for one donor, ordered by ``SPCD``."""
    live = rows[rows["STATUSCD"] == 1]
    return live.groupby("SPCD")["TPA_UNADJ"].sum().sort_index()


def _scaled_rows(rows: pd.DataFrame, fortypcd, policy: EstablishmentPolicy) -> pd.DataFrame:
    """One donor's scaled list: a row per live species, at the profile's size."""
    group = forest_type_group(fortypcd)
    profile = policy.profile_for(group)
    species = _live_species_tpa(rows)
    if policy.density is EstablishmentDensity.FOREST_TYPE_PROFILE:
        tpa = profile.tpa * species / species.sum()
    else:
        tpa = species
    return pd.DataFrame({
        "TM_ID": rows["TM_ID"].iloc[0],
        "PLT_CN": rows["PLT_CN"].iloc[0],
        "STATUSCD": 1,
        "TPA_UNADJ": tpa.to_numpy(float),
        "SPCD": species.index.to_numpy(),
        "DIA": profile.dbh_in,
        "HT": profile.ht_ft,
        "CR": pd.NA,                       # left to FVS: no measured crown for a scaled tree
        "establishment_mode": str(EstablishmentMode.SCALED_YOUNG),
        "establishment_age": policy.target_age,
        "forest_type_group": str(group),
    })


def scaled_establishment_lists(donor_modes: pd.DataFrame, tree_table: pd.DataFrame,
                               fortypcd: pd.Series, policy: EstablishmentPolicy) -> pd.DataFrame:
    """Establishment tree lists keyed by ``(TM_ID, establishment_mode)``.

    ``donor_modes`` lists each ``(TM_ID, establishment_mode)`` pair in use. ``tree_table`` is
    the TreeMap tree table (``TM_ID``, ``PLT_CN``, ``STATUSCD``, ``TPA_UNADJ``, ``SPCD``,
    ``DIA``, ``HT``, ...); ``fortypcd`` maps ``TM_ID`` to the TreeMap VAT ``FORTYPCD``.

    ``SCALED_YOUNG`` rows: one per live donor species, ``TPA_UNADJ`` = profile TPA x the
    species' share of donor live TPA (or the donor's own TPA under
    ``EstablishmentDensity.DONOR``), ``DIA``/``HT`` from the profile, ``CR`` empty, and
    ``establishment_age`` = the target age. ``DONOR_AS_IS`` rows: the donor's rows verbatim,
    ``establishment_age`` empty. Both carry the donor's ``PLT_CN`` as an exact string and
    its ``forest_type_group``. The list depends only on the donor and the mode, so patches
    sharing both share rows.
    """
    table = tree_table.copy()
    table["TM_ID"] = as_id_series(table["TM_ID"], column="TM_ID")
    table["PLT_CN"] = as_id_series(table["PLT_CN"], column="PLT_CN")
    fortypcd = fortypcd.set_axis(as_id_series(pd.Series(fortypcd.index), column="TM_ID"))
    keys = donor_modes.assign(TM_ID=as_id_series(donor_modes["TM_ID"], column="TM_ID"))
    report_key_overlap(keys["TM_ID"], table["TM_ID"], left_name="patch donor", right_name="tree table")

    by_donor = dict(tuple(table.groupby("TM_ID", sort=False)))
    parts = []
    for tm_id, mode in keys[["TM_ID", "establishment_mode"]].drop_duplicates().itertuples(index=False):
        rows = by_donor.get(tm_id)
        if rows is None:
            continue
        code = fortypcd.get(tm_id)
        if EstablishmentMode(mode) is EstablishmentMode.SCALED_YOUNG:
            parts.append(_scaled_rows(rows, code, policy))
        else:
            parts.append(rows.assign(establishment_mode=str(EstablishmentMode.DONOR_AS_IS),
                                     establishment_age=pd.NA,
                                     forest_type_group=str(forest_type_group(code))))
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()


def live_stand_metrics(rows: pd.DataFrame, keys: list[str]) -> pd.DataFrame:
    """Live TPA and live basal area (ft²/ac) per stand, indexed by ``keys``."""
    live = rows[rows["STATUSCD"] == 1]
    ba = live["TPA_UNADJ"] * BA_FACTOR * live["DIA"].astype(float) ** 2
    return pd.DataFrame({"live_tpa": live["TPA_UNADJ"], "live_ba_ft2_per_ac": ba,
                         **{k: live[k] for k in keys}}).groupby(keys).sum()


def _totals(acres: pd.Series, metrics: pd.DataFrame) -> dict:
    """Acre-weighted TPA and BA per acre, and landscape totals, over matched stands."""
    a = acres.to_numpy(float)
    total_acres = a.sum()
    trees = (a * metrics["live_tpa"].to_numpy(float)).sum()
    ba = (a * metrics["live_ba_ft2_per_ac"].to_numpy(float)).sum()
    return {"live_tpa": round(trees / total_acres, 1) if total_acres else None,
            "live_ba_ft2_per_ac": round(ba / total_acres, 1) if total_acres else None,
            "live_trees": round(trees), "live_ba_ft2": round(ba)}


def establishment_effect(patch_acres: pd.DataFrame, donor_rows: pd.DataFrame,
                         established: pd.DataFrame) -> dict:
    """Live TPA and basal area the added-back acres carry: mature donors vs. what is established.

    ``patch_acres`` has ``TM_ID``, ``establishment_mode`` and ``acres`` (one row per patch,
    or per patch and county). ``donor_rows`` are the donors' TreeMap tree rows; the
    ``mature_donor`` figures are what the verbatim lists would have put on every acre.
    ``established`` is :func:`scaled_establishment_lists` output.
    """
    acres = patch_acres.assign(TM_ID=as_id_series(patch_acres["TM_ID"], column="TM_ID"),
                               establishment_mode=patch_acres["establishment_mode"].map(str))
    acres = acres.groupby(["TM_ID", "establishment_mode"], as_index=False)["acres"].sum()
    donors = donor_rows.assign(TM_ID=as_id_series(donor_rows["TM_ID"], column="TM_ID"))
    mature = live_stand_metrics(donors, ["TM_ID"])
    after = live_stand_metrics(established, ["TM_ID", "establishment_mode"])

    def summarize(frame: pd.DataFrame) -> dict:
        before = mature.reindex(frame["TM_ID"]).fillna(0.0)
        est = after.reindex(pd.MultiIndex.from_frame(frame[["TM_ID", "establishment_mode"]])).fillna(0.0)
        return {"acres": round(float(frame["acres"].sum()), 1),
                "mature_donor": _totals(frame["acres"], before),
                "established": _totals(frame["acres"], est)}

    return {"by_mode": {m: summarize(f) for m, f in acres.groupby("establishment_mode")},
            "total": summarize(acres)}
