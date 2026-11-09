"""Speckle merge: management units under the minimum unit area join a neighbour (ADR 0005).

LETO's small-stand actions act on parent stands, before the riparian and county splits, so
the speckle those splits make survives them. After the splits, a unit under the region's
minimum unit area (1 ac in Florida) is *speckle*. :attr:`SpeckleAction.MERGE_DELETE`, the
default, merges it into the first neighbour that qualifies, in :class:`SpeckleStep` order:

1. ``same_owner``: the same owner class and the same unit class;
2. ``other_owner``: the same unit class under another owner class (it takes that owner class);
3. ``riparian``: for a managed speckle only, a riparian neighbour (it becomes grow-only);
4. otherwise nothing takes it: :attr:`SpeckleAction.MERGE_DELETE`, the default, deletes it
   (no allowed neighbour: an isolated patch, a corner-only contact, riparian speckle beside
   managed land only), and :attr:`SpeckleAction.MERGE` keeps it.

Within a step the neighbour with the lowest ``force_merge`` cost wins
(:func:`pipeline.leto_ca.merge_small_segments`): the weighted squared differences of the two
parent stands' standardized traits, the forest-type term when their dominant types differ,
less ``shared_edge_bonus`` × shared edges, where a shared edge is :attr:`MergeCost.edge_length`
metres of common boundary. Ties go to the lower ``unit_id`` (``<FIPS>-<n>``, by ``n``). The
merge repeats, smallest speckle first, until no speckle can merge.

A unit may be multipart (LETO's ``proximity_merge``, #169): its whole ``Acres`` are measured
against the minimum, and any of its parts' edges count as its edges.

A merge never crosses a county line and riparian area never joins a managed unit. Masked
units (Harris 1-2) neither merge nor receive. The absorbed area takes the receiving unit's row
(parent stand, owner and unit class, representative plot, age), so donor weights follow its
``SEG_ID``; ``Acres`` are summed, so acres are conserved. :attr:`SpeckleAction.KEEP` returns
its input, to replay LETO's own output.

>>> SpeckleSettings.from_config({"action": "keep", "minimum_unit_acres": 1})
SpeckleSettings(action=<SpeckleAction.KEEP: 'keep'>, minimum_unit_acres=1.0)
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from enum import StrEnum

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely

from pipeline.s3_management.owner_classes import MASKED, load_ownership_policy

FORTYPCD = "FORTYPCD"
TOUCH_TOLERANCE = 0.05                         # metres: vector split pieces meet within this
MINIMUM_SHARED_LENGTH = 1.0                    # metres: shorter contact is a corner, not an edge


class UnitClass(StrEnum):
    MANAGED = "managed"
    RIPARIAN = "riparian"                     # grow only


class SpeckleAction(StrEnum):
    MERGE_DELETE = "merge_delete"              # the default: merge, then delete what is left
    MERGE = "merge"                            # merge, keep what is left (ADR 0005 as first written)
    KEEP = "keep"                              # LETO's own output, e.g. the Maine baseline replay


class SpeckleStep(StrEnum):
    """The steps a speckle tries in order; the first with an allowed neighbour wins."""

    SAME_OWNER = "same_owner"
    OTHER_OWNER = "other_owner"
    RIPARIAN = "riparian"


STEPS = tuple(SpeckleStep)


@dataclass(frozen=True)
class SpeckleSettings:
    action: SpeckleAction = SpeckleAction.MERGE_DELETE
    minimum_unit_acres: float = 1.0

    @classmethod
    def from_config(cls, spec: dict) -> SpeckleSettings:
        return cls(SpeckleAction(spec["action"]), float(spec["minimum_unit_acres"]))

    def record(self) -> dict:
        return {"action": self.action.value, "minimum_unit_acres": self.minimum_unit_acres}


@dataclass(frozen=True)
class MergeCost:
    """``force_merge``'s cost terms: trait weights (``FORTYPCD`` and the continuous traits)."""

    weights: dict
    shared_edge_bonus: float
    edge_length: float                         # metres: one cell edge


@dataclass(frozen=True)
class SpeckleResult:
    units: gpd.GeoDataFrame
    qa: pd.DataFrame                           # one row per county
    deleted: gpd.GeoDataFrame                  # speckle MERGE_DELETE removed, as it stood
    receiver: pd.Series                        # each input unit_id -> its unit_id now; None: deleted


def _order(unit_ids: pd.Series) -> np.ndarray:
    """Each unit's rank by ``(county, n)`` of its ``<FIPS>-<n>`` ID: the tie-break order."""
    parts = unit_ids.str.rsplit("-", n=1, expand=True)
    ranked = np.lexsort((parts[1].astype(np.int64).to_numpy(), parts[0].to_numpy()))
    order = np.empty(len(unit_ids), dtype=np.int64)
    order[ranked] = np.arange(len(unit_ids))
    return order


def _adjacency(units: gpd.GeoDataFrame, speckle: np.ndarray, open_: np.ndarray
               ) -> dict[int, dict[int, float]]:
    """Shared boundary metres between open units of a county where at least one is speckle."""
    geoms = units.geometry.to_numpy()
    grown = shapely.buffer(geoms, TOUCH_TOLERANCE)
    county = units["county_fips"].to_numpy()
    tree = shapely.STRtree(geoms)
    left, right = tree.query(grown[speckle], predicate="intersects")
    left = np.flatnonzero(speckle)[left]
    keep = (left != right) & open_[right] & (county[left] == county[right])
    left, right = left[keep], right[keep]
    shared = shapely.length(shapely.intersection(shapely.boundary(geoms[left]), grown[right]))
    nbrs: dict[int, dict[int, float]] = defaultdict(dict)
    for a, b, metres in zip(left.tolist(), right.tolist(), shared.tolist()):
        if metres >= MINIMUM_SHARED_LENGTH:
            nbrs[a][b] = nbrs[b][a] = metres
    return nbrs


class _Merger:
    """Union-find over units: a component is its root unit's row, with the summed acres."""

    def __init__(self, units: gpd.GeoDataFrame, traits: pd.DataFrame, cost: MergeCost,
                 minimum: float, key: str):
        self.owner = units["owner_class"].to_numpy()
        self.unit_class = units["unit_class"].to_numpy()
        self.acres = units["Acres"].to_numpy(dtype=float).copy()
        self.order = _order(units["unit_id"])
        self.open = self.owner != MASKED
        self.parent = np.arange(len(units))
        self.minimum, self.cost_terms = minimum, cost
        rows = traits.reindex(units[key].to_numpy())
        self.continuous = [n for n in cost.weights if n != FORTYPCD]
        self.traits = rows[self.continuous].to_numpy(dtype=float)
        self.forest_type = rows[FORTYPCD].to_numpy()
        self.nbrs = _adjacency(units, self.open & (self.acres < minimum), self.open)
        self.events: list[tuple[int, SpeckleStep, float]] = []

    def _allowed(self, step: SpeckleStep, src: int, nb: int) -> bool:
        same_class = self.unit_class[nb] == self.unit_class[src]
        if step is SpeckleStep.SAME_OWNER:
            return same_class and self.owner[nb] == self.owner[src]
        if step is SpeckleStep.OTHER_OWNER:
            return same_class and self.owner[nb] != self.owner[src]
        return self.unit_class[src] == UnitClass.MANAGED and self.unit_class[nb] == UnitClass.RIPARIAN

    def _cost(self, src: int, nb: int, metres: float) -> float:
        terms = self.cost_terms
        diff = self.traits[src] - self.traits[nb]
        cost = float(sum(terms.weights[n] * d * d for n, d in zip(self.continuous, diff)))
        if self.forest_type[src] != self.forest_type[nb]:
            cost += terms.weights[FORTYPCD]
        return cost - terms.shared_edge_bonus * metres / terms.edge_length

    def _choose(self, src: int) -> tuple[SpeckleStep, int] | None:
        for step in STEPS:
            allowed = [(self._cost(src, nb, m), self.order[nb], nb)
                       for nb, m in self.nbrs.get(src, {}).items() if self._allowed(step, src, nb)]
            if allowed:
                return step, min(allowed)[2]
        return None

    def _absorb(self, src: int, tgt: int) -> None:
        for nb, metres in self.nbrs.pop(src, {}).items():
            del self.nbrs[nb][src]
            if nb != tgt:
                self.nbrs[tgt][nb] = self.nbrs[nb][tgt] = self.nbrs[tgt].get(nb, 0.0) + metres
        self.parent[src] = tgt
        self.acres[tgt] += self.acres[src]
        self.acres[src] = 0.0

    def find(self, unit: int) -> int:
        while self.parent[unit] != unit:
            self.parent[unit] = self.parent[self.parent[unit]]
            unit = self.parent[unit]
        return int(unit)

    def roots(self) -> np.ndarray:
        """Each unit's component root: the unit whose row it takes."""
        return np.array([self.find(i) for i in range(len(self.parent))], dtype=np.int64)

    def speckle(self) -> np.ndarray:
        roots = np.flatnonzero(self.open & (self.parent == np.arange(len(self.parent)))
                               & (self.acres < self.minimum))
        return roots[np.lexsort((self.order[roots], self.acres[roots]))]

    def run(self) -> None:
        while True:
            merged = 0
            for src in self.speckle().tolist():
                if self.acres[src] >= self.minimum:          # it grew this pass, as a receiver
                    continue
                choice = self._choose(src)
                if choice is None:
                    continue
                step, tgt = choice
                self.events.append((src, step, float(self.acres[src])))
                self._absorb(src, tgt)
                merged += 1
            if merged == 0:
                return


def _dissolve(units: gpd.GeoDataFrame, root: np.ndarray, acres: np.ndarray) -> gpd.GeoDataFrame:
    keep = np.flatnonzero(root == np.arange(len(root)))
    out = units.iloc[keep].copy()
    geometry = out.geometry.to_numpy().copy()
    position = {r: i for i, r in enumerate(keep.tolist())}
    absorbed = np.flatnonzero(root != np.arange(len(root)))
    members = pd.Series(np.concatenate([absorbed, root[absorbed]])).groupby(
        np.concatenate([root[absorbed], root[absorbed]]))
    pieces = units.geometry.to_numpy()
    for r, group in members:
        geometry[position[int(r)]] = shapely.union_all(pieces[np.unique(group.to_numpy())])
    out = out.set_geometry(gpd.GeoSeries(geometry, index=out.index, crs=units.crs))
    out["Acres"] = acres[keep]
    return out.reset_index(drop=True)


def _qa(before: gpd.GeoDataFrame, after: gpd.GeoDataFrame, deleted: gpd.GeoDataFrame,
        merger: _Merger | None, minimum: float) -> pd.DataFrame:
    """Per county: merges by step, speckle kept and deleted, grow-only acres, owner drift.

    Owner drift is what merges move between owner classes; deleted acres are their own line.
    """
    events = pd.DataFrame(merger.events if merger else [], columns=["unit", "step", "acres"])
    events["county_fips"] = before["county_fips"].to_numpy()[events["unit"].to_numpy(dtype=int)]
    owners = list(load_ownership_policy()["classes"])      # one header for every county
    rows = []
    for county, units in before.groupby("county_fips", sort=True):
        kept = after[(after["county_fips"] == county) & (after["owner_class"] != MASKED)
                     & (after["Acres"] < minimum)]
        mine = events[events["county_fips"] == county]
        row = {"county_fips": county, "units_before": len(units),
               "units_after": int((after["county_fips"] == county).sum()),
               "speckle_before": int(((units["owner_class"] != MASKED)
                                      & (units["Acres"] < minimum)).sum())}
        for step in STEPS:
            hit = mine[mine["step"] == step]
            row[f"merged_{step}"] = len(hit)
            row[f"merged_{step}_acres"] = round(float(hit["acres"].sum()), 4)
        row["grow_only_acres"] = row[f"merged_{SpeckleStep.RIPARIAN}_acres"]
        row["speckle_kept"], row["speckle_kept_acres"] = len(kept), round(float(kept["Acres"].sum()), 4)
        gone = deleted[deleted["county_fips"] == county]
        row["speckle_deleted"] = len(gone)
        row["speckle_deleted_acres"] = round(float(gone["Acres"].sum()), 4)
        row["speckle_deleted_riparian_acres"] = round(float(
            gone.loc[gone["unit_class"] == UnitClass.RIPARIAN, "Acres"].sum()), 4)
        ended = pd.concat([after[after["county_fips"] == county], gone])
        drift = (ended.groupby("owner_class")["Acres"].sum()
                 .sub(units.groupby("owner_class")["Acres"].sum(), fill_value=0.0))
        row.update({f"owner_drift_acres_{owner}": round(float(drift.get(owner, 0.0)), 4)
                    for owner in owners})
        rows.append(row)
    return pd.DataFrame(rows)


def _receiver(units: gpd.GeoDataFrame, root: np.ndarray, deleted: gpd.GeoDataFrame) -> pd.Series:
    ids = units["unit_id"].to_numpy()
    now = pd.Series(ids[root], index=pd.Index(ids, name="unit_id"), dtype=object)
    return now.where(~now.isin(set(deleted["unit_id"])), None)


def merge_speckle(units: gpd.GeoDataFrame, traits: pd.DataFrame, settings: SpeckleSettings,
                  cost: MergeCost, *, key: str = "SEG_ID") -> SpeckleResult:
    """Merge each speckle of ``units`` into a neighbour; return the units and their QA table.

    ``units`` needs ``unit_id``, ``county_fips``, ``owner_class``, ``unit_class``, ``Acres``,
    ``key`` and a metric CRS; a unit may be multipart, and its ``Acres`` are measured whole.
    ``traits`` is indexed by ``key`` (by default ``SEG_ID``, the parent stand): standardized
    continuous traits (the keys of ``cost.weights`` but ``FORTYPCD``) and dominant ``FORTYPCD``.
    """
    minimum = settings.minimum_unit_acres
    none = units.iloc[:0]
    if settings.action is SpeckleAction.KEEP or units.empty:
        return SpeckleResult(units, _qa(units, units, none, None, minimum), none,
                             _receiver(units, np.arange(len(units)), none))
    units = units.reset_index(drop=True)
    merger = _Merger(units, traits, cost, minimum, key)
    merger.run()
    root = merger.roots()
    merged = _dissolve(units, root, merger.acres)
    deleted = none
    if settings.action is SpeckleAction.MERGE_DELETE:
        left = (merged["owner_class"] != MASKED) & (merged["Acres"] < minimum)
        merged, deleted = merged[~left].reset_index(drop=True), merged[left].reset_index(drop=True)
    return SpeckleResult(merged, _qa(units, merged, deleted, merger, minimum), deleted,
                         _receiver(units, root, deleted))
